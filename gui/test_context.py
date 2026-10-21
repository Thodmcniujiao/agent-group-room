"""Delivery isolation, raw-history durability and signed provider round-trips."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engines import preferences
from store import Store
from room_settings import RoomSettings
from context import ContextTurn,read_originals
from api_protocol import gemini_request,gemini_response
import provider_sessions


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'state');self.rid=self.store.create('A',str(self.root))['id']
        self.settings=preferences.load();self.settings['executor']='deepseek'
        self.fp=patch('model_catalog.connection_context',return_value=('fixture-connection','fixture'));self.fp.start()
    def tearDown(self):self.fp.stop();self.store.db.close();self.temp.cleanup()
    def turn(self,identity='claude',**kwargs):
        source=next(a for a in self.settings['agents'] if a['id']==identity)
        return ContextTurn(self.store,self.rid,identity,source,source['model'],self.settings,'test',**kwargs)

    def test_raw_messages_survive_old_history_and_storage_cutoffs(self):
        for i in range(35):self.store.message(self.rid,'peer','original '+str(i))
        content='start-'+'中文x'*40000+'-last-detail'
        mid=self.store.message(self.rid,'peer',status='streaming');self.store.update(mid,content,'complete')
        self.assertIn('original 0',self.store.history(self.rid))
        turn=self.turn();turn.compose('find the original')
        self.assertTrue(any(m['id']==mid for m in turn.omitted))
        text='';offset=0
        while True:
            page=turn.read('group_get_message',{'message_id':mid,'offset':offset});text+=page['text']
            if page['next_offset'] is None:break
            offset=page['next_offset']
        self.assertEqual(text,content)
        self.assertIn('not_loaded',turn.input)

    def test_completed_original_is_immutable_and_finalization_is_idempotent(self):
        mid=self.store.message(self.rid,'peer','original')
        self.store.update(mid,'original','complete')
        with self.assertRaises(ValueError):self.store.update(mid,'rewritten','complete')
        self.assertEqual(len(self.store.originals(self.rid)),1)

    def test_completed_order_not_stream_creation_order(self):
        a=self.store.message(self.rid,'A',status='streaming');b=self.store.message(self.rid,'B',status='streaming')
        self.store.update(b,'B done','complete');boundary=self.store.boundary(self.rid)
        self.store.update(a,'A done','complete')
        self.assertEqual([m['id'] for m in self.store.originals(self.rid,after=boundary)],[a])

    def test_discussion_sees_raw_peers_without_monitor_repeating_them(self):
        mid=self.store.message(self.rid,'chatgpt','unique-original-7285')
        turn=self.turn();value=turn.compose('monitor instruction has no quotation')
        self.assertIn('unique-original-7285',value);self.assertIn(mid,value)
        self.assertIn('chatgpt',value);self.assertNotIn('role": "assistant',value)
        turn.state['native']={'messages':[{'role':'assistant','content':'my own native answer'}]}
        turn.complete('my own native answer')
        self.store.message(self.rid,'claude','my own native answer')
        second=self.turn();second.compose('next')
        self.assertNotIn('unique-original-7285',second.input)
        self.assertIn('my own native answer',second.state['native']['messages'][0]['content'])

    def test_executor_never_receives_peer_messages_or_read_tools(self):
        self.store.message(self.rid,'peer','MUST-NOT-REACH-EXECUTOR')
        turn=self.turn('deepseek');prompt=turn.compose('monitor contract version 1')
        self.assertEqual(prompt,'monitor contract version 1');self.assertFalse(turn.tools)
        self.assertEqual(turn.selected,[])
        with self.assertRaises(ValueError):turn.read('group_read_messages',{})
        delivery=self.store.deliveries(self.rid)[0]
        self.assertEqual(delivery['contract']['text'],prompt)
        self.assertNotIn('MUST-NOT',json.dumps(delivery))

    def test_roles_models_rooms_and_coding_purposes_have_distinct_bindings(self):
        worker=self.turn();coding=self.turn(coding=True)
        self.settings['executor']='claude';execution=self.turn()
        self.assertNotEqual(worker.key,execution.key);self.assertNotEqual(worker.key,coding.key)
        self.rid=self.store.create('B',str(self.root))['id'];other=self.turn()
        self.assertNotEqual(other.key,execution.key)

    def test_unfinished_turn_starts_new_branch_without_assuming_delivery(self):
        first=self.turn();first.compose('first');branch=first.state['branch']
        second=self.turn();self.assertNotEqual(branch,second.state['branch'])
        self.assertEqual(second.state['cursor'],0)
        self.assertIn('未完成',second.state['rotation'])

    def test_tool_cutoff_and_room_scope_cannot_be_overridden_by_arguments(self):
        old=self.store.message(self.rid,'peer','visible')
        turn=self.turn();turn.compose('read')
        later=self.store.message(self.rid,'peer','not yet visible')
        other=self.store.create('Other',str(self.root))['id'];foreign=self.store.message(other,'peer','foreign')
        self.assertEqual(turn.read('group_get_message',{'message_id':old})['text'],'visible')
        for mid in [later,foreign]:
            with self.assertRaises(ValueError):turn.read('group_get_message',{'message_id':mid})

    def test_native_signed_history_is_rotated_whole_without_slicing(self):
        turn=self.turn('deepseek');original={'messages':[{'role':'assistant','signature':'opaque','content':'X'*260000}]}
        turn.state['native']=copy.deepcopy(original);turn.save();turn.compose('next')
        self.assertEqual(turn.state['native'],{})
        row=self.store.db.execute("SELECT data FROM agent_sessions WHERE key LIKE '%:archive:%'").fetchone()
        self.assertEqual(json.loads(row[0])['native'],original)

    def test_executor_selection_defaults_and_explicit_none_are_room_local(self):
        with patch.object(preferences,'PREFS',self.root/'absent.json'):
            rooms=RoomSettings(self.store);rooms.seed(self.rid,self.settings)
            rooms.membership(self.rid,'claude',True)
            value=rooms.membership(self.rid,'deepseek',True)
            self.assertEqual(value['executor'],'deepseek')
            value=rooms.update(self.rid,{'executor':'claude'});self.assertEqual(value['executor'],'claude')
            self.assertGreater(value['context_epoch'],0)
            value=rooms.update(self.rid,{'executor':None})
            value=rooms.membership(self.rid,'chatgpt',True);self.assertIsNone(value['executor'])
            with self.assertRaises(ValueError):rooms.update(self.rid,{'executor':'not-in-room'})


class GeminiProtocolTests(unittest.TestCase):
    def test_stateless_roundtrip_keeps_opaque_thought_and_function_steps_exactly(self):
        steps=[{'type':'thought','signature':'opaque-signed-value','summary':[]},
            {'type':'function_call','id':'call-1','name':'group_read_messages','arguments':{'after_seq':0}}]
        response=gemini_response({'status':'requires_action','steps':steps})
        history=[{'role':'system','content':'constant'},{'role':'user','content':'request'},
            response['choices'][0]['message'],{'role':'tool','tool_call_id':'call-1','content':'original tool result'}]
        payload=gemini_request('models/gemini-3.8-flash',history)
        self.assertFalse(payload['store']);self.assertNotIn('previous_interaction_id',payload)
        self.assertEqual(payload['input'][1:3],steps)
        self.assertEqual(payload['input'][3]['call_id'],'call-1')
        self.assertEqual(payload['model'],'gemini-3.8-flash')
        self.assertEqual(steps[0]['signature'],'opaque-signed-value')

    def test_incomplete_response_is_never_published_as_complete(self):
        with self.assertRaises(ValueError):gemini_response({'status':'incomplete','steps':[]})

    def test_foreign_plain_assistant_message_is_not_forged_as_gemini_state(self):
        with self.assertRaises(ValueError):gemini_request('gemini-3.8-flash',[{'role':'assistant','content':'someone else'}])


class NativeAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_tools_read_originals_without_losing_thought_signatures(self):
        import httpx
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            try:
                rid=store.create('tool test',folder)['id'];mid=store.message(rid,'peer','EXACT-ORIGINAL')
                agent=dict(id='gemini',backend='openai-compatible',base_url='https://generativelanguage.googleapis.com/v1beta/openai',model='gemini-3.8-flash',name='Gemini')
                with patch('model_catalog.connection_context',return_value=('fixture','key')):
                    turn=ContextTurn(store,rid,'gemini',agent,agent['model'],{'executor':None},'test')
                    prompt=turn.compose('read a message')
                requests=[]
                def respond(req):
                    payload=json.loads(req.content);requests.append(payload)
                    if len(requests)==1:
                        data={'status':'requires_action','steps':[{'type':'thought','signature':'signed-original','summary':[]},
                            {'type':'function_call','id':'read-1','name':'group_get_message','arguments':{'message_id':mid}}]}
                    else:
                        self.assertTrue(any(s.get('signature')=='signed-original' for s in payload['input']))
                        result=payload['input'][-1];self.assertEqual(result['call_id'],'read-1')
                        self.assertIn('EXACT-ORIGINAL',result['result'][0]['text'])
                        data={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':'verified original'}]}]}
                    return httpx.Response(200,json=data)
                real=httpx.AsyncClient
                def client(**kw):return real(**kw,transport=httpx.MockTransport(respond))
                with patch.object(provider_sessions.httpx,'AsyncClient',side_effect=client):
                    text,_=await provider_sessions.api_conversation(turn,prompt,'fixture',agent['base_url'],lambda s:None)
                self.assertEqual(text,'verified original');self.assertEqual(len(requests),2)
            finally:store.db.close()


class ProjectToolTests(unittest.TestCase):
    def setUp(self):
        from project_tools import ProjectTools
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.tools=ProjectTools(self.root,['result.txt'])
    def tearDown(self):self.temp.cleanup()

    def test_create_read_and_edit_only_contract_file(self):
        self.assertTrue(self.tools.execute('project_Write',{'path':'result.txt','content':'before'}).startswith('OK'))
        self.assertEqual(self.tools.execute('project_Read',{'path':'result.txt'}),'before')
        self.assertTrue(self.tools.execute('project_Edit',{'path':'result.txt','old_string':'before','new_string':'after'}).startswith('OK'))
        self.assertEqual((self.root/'result.txt').read_text(encoding='utf-8'),'after')

    def test_no_path_escape_or_unlisted_writes(self):
        for path in ['../outside.txt','other.txt','sub/result.txt']:
            with self.assertRaises(ValueError):self.tools.execute('project_Write',{'path':path,'content':'no'})
        self.assertFalse((self.root/'other.txt').exists())

    def test_no_shell_or_credential_tool(self):
        with self.assertRaises(ValueError):self.tools.execute('project_Bash',{'command':'whoami'})
        with self.assertRaises(ValueError):self.tools.execute('project_Read',{'path':'.env'})
        with self.assertRaises(ValueError):self.tools.execute('project_Write',{'content':'missing path'})

    def test_existing_file_is_not_blindly_overwritten(self):
        (self.root/'result.txt').write_text('keep',encoding='utf-8')
        result=self.tools.execute('project_Write',{'path':'result.txt','content':'overwrite'})
        self.assertTrue(result.startswith('ERROR'))
        self.assertEqual((self.root/'result.txt').read_text(encoding='utf-8'),'keep')


if __name__=='__main__':unittest.main()
