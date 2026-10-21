"""Room isolation, exact pending-request continuation and local avatar persistence."""
import asyncio
import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,AsyncMock,Mock

import httpx
from engines import preferences
from store import Store
from room_settings import RoomSettings
from context_settings import context_settings
from context import ContextTurn,estimate
from budget_control import BudgetGate,ContextBudgetExceeded
from avatars import Avatars
import provider_sessions

PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jKZkAAAAASUVORK5CYII='


class RoomControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'state')
        self.rid=self.store.create('A',str(self.root))['id'];self.settings=preferences.load()
        self.settings['executor']='deepseek';self.settings['context']=context_settings({'soft_tokens':12000,'output_tokens':1024,'margin_tokens':512,'overflow':'pause'})
        self.gate=BudgetGate(self.store);self.store.budget_gate=self.gate
        self.fp=patch('model_catalog.connection_context',return_value=('test-connection',''));self.fp.start()
    def tearDown(self):self.fp.stop();self.store.db.close();self.tmp.cleanup()
    def turn(self,identity='deepseek'):
        source=next(a for a in self.settings['agents'] if a['id']==identity)
        return ContextTurn(self.store,self.rid,identity,source,source['model'],self.settings,'test')

    def test_controls_are_per_room_and_invalid_edit_is_atomic(self):
        rooms=RoomSettings(self.store);other=self.store.create('B',str(self.root))['id']
        rooms.seed(self.rid,self.settings);rooms.seed(other,self.settings)
        with patch.object(preferences,'load',return_value=self.settings):
            rooms.update(self.rid,{'context':{'soft_tokens':256000,'tool_rounds':22}})
            self.assertEqual(rooms.load(self.rid)['context']['soft_tokens'],256000)
            self.assertEqual(rooms.load(other)['context']['soft_tokens'],128000)
            before=copy.deepcopy(self.store.configuration(self.rid))
            for body in [{'context':{'soft_tokens':True}},{'context':{'output_tokens':200000}},
                         {'context':{'soft_tokens':4096}},{'context':{'overflow':'ignore'}},{'_force_context_budget':True}]:
                with self.assertRaises(ValueError):rooms.update(self.rid,body)
                self.assertEqual(before,self.store.configuration(self.rid))

    def test_json_escaping_system_and_tools_fit_before_request_not_after(self):
        for _ in range(35):self.store.message(self.rid,'peer','"\\\n中文'*500)
        turn=self.turn('claude');turn.source={**turn.source,'backend':'openai-compatible'}
        turn.compose('short instruction')
        self.assertLessEqual(turn.request_estimate(turn.input)+turn.budget['margin'],turn.input_budget)
        self.assertTrue(turn.omitted)
        self.assertEqual(len(self.store.originals(self.rid)),35)

    def test_cli_history_pause_keeps_native_thread_and_force_does_not_erase_it(self):
        turn=self.turn('chatgpt')
        turn.state['native']={'thread_id':'original-thread','usage':{'last':{'inputTokens':30000}}}
        with self.assertRaises(ContextBudgetExceeded):turn.compose('same task')
        self.assertEqual(turn.state['native']['thread_id'],'original-thread')
        self.settings['_force_context_budget']=True;turn.compose('same task')
        self.assertEqual(turn.state['native']['thread_id'],'original-thread')
        self.assertFalse(turn.state['needs_compaction'])

    async def test_budget_force_resumes_same_request_without_duplicate_user_prompt(self):
        turn=self.turn();original_system='original-state-'+'x'*30000
        turn.state['native']={'messages':[{'role':'system','content':original_system}]};turn.save()
        requests=[];real=provider_sessions.AsyncOpenAI
        def respond(request):
            payload=json.loads(request.content);requests.append(payload)
            data={'id':'test','object':'chat.completion.chunk','created':0,'model':turn.model,
                'choices':[{'index':0,'delta':{'role':'assistant','content':'finished once'},'finish_reason':'stop'}]}
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        def client(**kw):return real(**kw,http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        with patch.object(provider_sessions,'AsyncOpenAI',side_effect=client),patch('model_catalog.effective_effort',new=AsyncMock(return_value=None)),patch.object(preferences,'get_member_key',return_value='fixture-key'):
            task=asyncio.create_task(provider_sessions.reply(turn,'approved contract unchanged',self.root,None,turn.source))
            for _ in range(100):
                if self.gate.snapshot(self.rid):break
                await asyncio.sleep(.01)
            self.assertEqual(self.store.room(self.rid)['status'],'budget_paused');self.assertEqual(requests,[])
            self.gate.force(self.rid);answer=await asyncio.wait_for(task,3)
        self.assertEqual(answer,'finished once');self.assertEqual(len(requests),1)
        self.assertEqual(requests[0]['messages'][0]['content'],original_system)
        self.assertEqual(sum(m['role']=='user' for m in requests[0]['messages']),1)
        self.assertEqual(requests[0]['messages'][-1]['content'],'approved contract unchanged')
        self.assertTrue(turn.forced);self.assertFalse(self.gate.snapshot(self.rid))
        self.assertNotIn('_force_context_budget',self.store.configuration(self.rid) or {})

    async def test_raising_room_limit_resumes_without_forcing_and_cancellation_clears_wait(self):
        turn=self.turn();turn.compose('small')
        task=asyncio.create_task(turn.check_budget(20000))
        await asyncio.sleep(0)
        controls=context_settings({'soft_tokens':64000,'output_tokens':1024,'margin_tokens':512})
        self.gate.update(self.rid,controls);await asyncio.wait_for(task,1)
        self.assertFalse(turn.forced);self.assertEqual(turn.budget['soft'],64000)
        task=asyncio.create_task(turn.check_budget(100000));await asyncio.sleep(0);task.cancel()
        await asyncio.gather(task,return_exceptions=True);self.assertFalse(self.gate.snapshot(self.rid))


class AvatarTests(unittest.TestCase):
    def test_user_supervisor_and_same_vendor_agents_keep_distinct_images_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'avatars.json';a=Avatars(path)
            for identity in ['you','supervisor','agent-one','agent-two']:a.save(identity,PNG)
            self.assertEqual(set(Avatars(path).snapshot()),{'you','supervisor','agent-one','agent-two'})
            a.save('agent-one',None)
            self.assertNotIn('agent-one',a.snapshot());self.assertIn('agent-two',a.snapshot())
    def test_invalid_or_oversized_raster_does_not_overwrite_previous_avatar(self):
        with tempfile.TemporaryDirectory() as folder:
            a=Avatars(Path(folder)/'a.json');before=a.save('you',PNG)
            for image in ['file:///private.png','data:image/svg+xml;base64,PHN2Zy8+','data:image/png;base64,not-base64',
                          'data:image/png;base64,'+base64.b64encode(b'x'*70000).decode()]:
                with self.assertRaises(ValueError):a.save('you',image)
                self.assertEqual(a.snapshot(),before)


class ExecutionControlTests(unittest.TestCase):
    def test_room_output_and_tool_rounds_reach_the_coding_executor(self):
        previous=asyncio.get_event_loop_policy()
        try:import deepseek_server
        finally:asyncio.set_event_loop_policy(previous)
        from deepseek_mcp.config import Config
        with tempfile.TemporaryDirectory() as folder:
            cfg=Config('fixture-key',Path(folder),allowed_tools=['Read'])
            with patch.object(deepseek_server,'_original_load',return_value=cfg),patch.dict(os.environ,{
                    'TIERED_CONTEXT_FILE':str(Path(folder)/'session.json'),'TIERED_CONTEXT_OUTPUT':'8192',
                    'TIERED_CONTEXT_TOOL_ROUNDS':'7','TIERED_MEMBER_EFFORT':''}):
                selected=deepseek_server._selected_config()
            self.assertEqual(selected.max_output_tokens,8192);self.assertEqual(selected.max_turns,7)
            self.assertEqual(selected.reasoning_effort,'provider-default')


class RoomControlApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_force_button_can_continue_an_old_blocked_room_without_persisting_override(self):
        import server
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder);rid=store.create('blocked',folder)['id'];rooms=RoomSettings(store)
            settings=preferences.load();rooms.seed(rid,settings,legacy=True)
            store.message(rid,'you','previous approved goal');store.status(rid,'blocked')
            work=Mock();work.budget_gate.snapshot.return_value=[]
            try:
                with patch.object(server,'STORE',store),patch.object(server,'ROOMS',rooms),patch.object(server,'WORK',work):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url=server.ORIGIN) as client:
                        response=await client.post('/api/rooms/'+rid+'/force-budget',json={},headers={'X-Tiered-Token':server.TOKEN})
                self.assertEqual(response.status_code,200)
                args=work.start.call_args.args
                self.assertEqual(args[2],'workflow');self.assertTrue(args[4]['_force_context_budget'])
                self.assertIn('核对已有文件',args[1])
                self.assertNotIn('_force_context_budget',rooms.load(rid))
            finally:store.db.close()

    async def test_avatar_endpoints_validate_identity_and_require_mutation_token(self):
        import server
        with tempfile.TemporaryDirectory() as folder:
            avatars=Avatars(Path(folder)/'avatars.json')
            with patch.object(server,'AVATARS',avatars):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url=server.ORIGIN) as client:
                    self.assertEqual((await client.post('/api/avatars/you',json={'image':PNG})).status_code,403)
                    headers={'X-Tiered-Token':server.TOKEN}
                    for identity in ['you','supervisor','chatgpt']:
                        response=await client.post('/api/avatars/'+identity,json={'image':PNG},headers=headers)
                        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['avatars'][identity],PNG)
                    invalid=await client.post('/api/avatars/not-saved',json={'image':PNG},headers=headers)
                    self.assertEqual(invalid.status_code,400)
                    self.assertEqual(len((await client.get('/api/avatars')).json()['avatars']),3)
