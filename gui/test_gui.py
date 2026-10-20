"""Integration checks using isolated rooms and deterministic provider fixtures."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import logging
from unittest.mock import patch

TEST_HOME=tempfile.TemporaryDirectory(prefix='tiered-gui-tests-')
os.environ['TIERED_GUI_DATA_DIR']=TEST_HOME.name
sys.path.insert(0,str(Path(__file__).parent))
import server
import workflows
from workflows import validate_plan,validate_command,confirm_recovery,changed_hashes
from store import Store
from supervision import validate_decision
from engines import preferences
import engines
import roster
import httpx
PREFERENCE_PATCHES=[patch.object(preferences,'STATE',Path(TEST_HOME.name)/'prefs'),
    patch.object(preferences,'PREFS',Path(TEST_HOME.name)/'prefs/preferences.json'),
    patch.object(preferences,'SECRET',Path(TEST_HOME.name)/'prefs/deepseek.dpapi'),
    patch.object(preferences,'PROFILE',Path(TEST_HOME.name)/'profile.toml')]
for item in PREFERENCE_PATCHES:item.start()


class ModelAndPolicyTests(unittest.TestCase):
    def test_deepseek_selection_reaches_the_execution_config(self):
        previous_policy=asyncio.get_event_loop_policy()
        try:
            import deepseek_server
        finally:
            asyncio.set_event_loop_policy(previous_policy)
        from deepseek_mcp.config import Config
        with tempfile.TemporaryDirectory() as folder:
            original=Config('sk-fixture',Path(folder),allowed_tools=['Read'])
            with patch.object(deepseek_server,'_original_load',return_value=original),patch.dict(os.environ,{'TIERED_DEEPSEEK_MODEL':'deepseek-v4-pro'}):
                selected=deepseek_server._selected_config()
            self.assertEqual(selected.model,'deepseek-v4-pro')

    def test_key_is_encrypted_for_the_current_windows_user(self):
        with tempfile.TemporaryDirectory() as folder:
            key='sk-deployment-encryption-fixture'
            with patch.object(preferences,'STATE',Path(folder)),patch.object(preferences,'SECRET',Path(folder)/'key.dpapi'):
                preferences.save_key(key)
                self.assertNotIn(key.encode(),preferences.SECRET.read_bytes())
                self.assertEqual(preferences.get_key(),key)

    def test_models_are_selectable_without_shell_syntax(self):
        for value in ['gpt-6.1-sol','claude-sonnet-5-5','deepseek-v4-pro','claude-fable-5-1[1m]']:
            self.assertEqual(preferences.model_id(value),value)
        for value in ['x; whoami','$(echo x)','--model','a\nb']:
            with self.assertRaises(ValueError): preferences.model_id(value)

    def test_plans_cannot_escape_or_edit_agent_configuration(self):
        for path in ['../x.py','C:/secret.txt','src/*','.claude/settings.json']:
            with self.assertRaises(ValueError): validate_plan({'tasks':[{'goal':'x','allowed_paths':[path],'acceptance_commands':['node test.js']}]})
        for command in ['git reset --hard','git push','npm install evil','del /s .']:
            with self.assertRaises(Exception): validate_command(command)
        validate_command('node test.js')

    def test_history_persists_and_marks_interrupted_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder);room=store.create('Test','C:/test')
            mid=store.message(room['id'],'chatgpt','visible reply',model='gpt-6.1-sol',status='streaming')
            store.status(room['id'],'running');store.db.close()
            store=Store(folder)
            self.assertEqual(store.room(room['id'])['status'],'interrupted')
            self.assertEqual(store.messages(room['id'])[0]['text'],'visible reply')
            self.assertEqual(store.messages(room['id'])[0]['status'],'interrupted')
            store.db.close()


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url=server.ORIGIN)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_mutation_requires_local_origin_and_token(self):
        denied=await self.client.post('/api/rooms',json={})
        self.assertEqual(denied.status_code,403)
        denied=await self.client.post('/api/rooms',json={},headers={'Origin':'https://example.com','X-Tiered-Token':server.TOKEN})
        self.assertEqual(denied.status_code,403)
        denied=await self.client.get('/api/health',headers={'Host':'malicious.example'})
        self.assertEqual(denied.status_code,403)

    async def test_room_creation_messages_and_export(self):
        with tempfile.TemporaryDirectory() as folder:
            response=await self.client.post('/api/rooms',json={'project':folder,'title':'三方测试'},headers={'X-Tiered-Token':server.TOKEN})
            self.assertEqual(response.status_code,200,response.text)
            room=response.json()
            for provider in ['chatgpt','claude','deepseek']:
                server.STORE.message(room['id'],provider,'hello '+provider,model=provider+'-chosen')
            data=(await self.client.get('/api/rooms/'+room['id'])).json()
            self.assertEqual(len(data['messages']),3)
            exported=(await self.client.get('/api/rooms/'+room['id']+'/export')).text
            self.assertIn('ChatGPT',exported);self.assertIn('Claude',exported);self.assertIn('DeepSeek',exported)
            self.assertIn('chatgpt-chosen',exported)

    async def test_policy_persists_and_live_message_does_not_start_second_run(self):
        with tempfile.TemporaryDirectory() as folder:
            rid=server.STORE.create('Policy',folder)['id'];headers={'X-Tiered-Token':server.TOKEN}
            response=await self.client.post('/api/rooms/'+rid+'/policy',json={'text':'先独立讨论，再互相质疑'},headers=headers)
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['revision'],1)
            server.WORK.running[rid]=object()
            try:
                with patch.object(server.WORK,'start') as start:
                    response=await self.client.post('/api/rooms/'+rid+'/send',json={'mode':'discuss','target':'supervisor','text':'不要改文件'},headers=headers)
                    self.assertEqual(response.json()['status'],'updated');start.assert_not_called()
            finally:server.WORK.running.pop(rid,None)
            data=(await self.client.get('/api/rooms/'+rid)).json()
            self.assertIn('不要改文件',data['policy']['text']);self.assertEqual(data['policy']['revision'],2)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_a_running_chat_sets_terminal_state(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'state');root=Path(folder)/'project';root.mkdir()
            rid=store.create('Cancel test',str(root))['id'];work=workflows.Workflows(store)
            started=asyncio.Event()
            async def waiting(*args):
                started.set();await asyncio.Event().wait()
            work.reply=waiting
            work.start(rid,'wait','discuss','claude',preferences.load())
            await started.wait();await work.stop(rid)
            self.assertEqual(store.room(rid)['status'],'stopped')
            self.assertNotIn(rid,work.running)
            store.db.close()

    async def test_ack_requires_matching_file_hash_and_scope(self):
        class Reply:
            isError=False
            def __init__(self,value):
                self.content=[type('Block',(),{'type':'text','text':json.dumps(value)})()]
        class Session:
            ack=False
            async def call_tool(self,name,args):
                if name=='get_deepseek_recovery': return Reply({'ok':True,'pending':[record]})
                self.ack=True;return Reply({'ok':True,'pending':[]})
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'x.txt').write_text('actual',encoding='utf-8')
            record={'path':'x.txt','sha256':'wrong','transaction_id':'tx-1'}
            session=Session()
            with self.assertRaises(RuntimeError): await confirm_recovery(session,root,{'x.txt'})
            self.assertFalse(session.ack)
            record['sha256']=hashlib.sha256(b'actual').hexdigest()
            with self.assertRaises(RuntimeError): await confirm_recovery(session,root,{'other.txt'})
            self.assertFalse(session.ack)
            await confirm_recovery(session,root,{'x.txt'});self.assertTrue(session.ack)

    async def test_deleted_file_is_detectable_and_nongit_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaises(ValueError): await changed_hashes(root)
            def git(*args): subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)
            git('init');(root/'x.txt').write_text('original',encoding='utf-8');git('add','x.txt')
            git('-c','user.name=Tiered Test','-c','user.email=test@example.invalid','commit','-m','fixture')
            before=await changed_hashes(root);(root/'x.txt').unlink();after=await changed_hashes(root)
            missing=object()
            self.assertNotEqual(before.get('x.txt',missing),after.get('x.txt',missing))


RULES=[{'id':'R1','text':'按需协作，真实验证','criterion':'有实际工作者记录及本机验收证据'}]
def decision(kind,agents=None,**kwargs):
    return {'message':'本轮按用户要求安排','rules':RULES,'checks':[{'rule_id':'R1','status':'pending','evidence':'等待工作结果','evidence_ids':[]}],
            'action':{'type':kind,'agents':agents or [],'instruction':'完成所分配的任务',**kwargs}}


class SupervisorValidationTests(unittest.TestCase):
    def test_finish_requires_real_successful_evidence_and_all_rules(self):
        value=decision('finish');value['checks'][0].update(status='met',evidence='已核对',evidence_ids=['E01'])
        for records in [[],[{'id':'E01','status':'failed'}],[{'id':'E01','status':'complete'},{'id':'E02','kind':'execute','status':'failed'}]]:
            with self.assertRaises(ValueError):validate_decision(value,RULES,records)
        validate_decision(value,RULES,[{'id':'E01','status':'complete'}])
        value['checks'][0]['evidence_ids']=['E99']
        with self.assertRaises(ValueError):validate_decision(value,RULES,[{'id':'E01','status':'complete'}])
        value['checks'][0].update(evidence_ids=['E01'],status='violated')
        with self.assertRaises(ValueError):validate_decision(value,RULES,[{'id':'E01','status':'complete'}])

    def test_any_worker_can_execute_but_never_two_at_once(self):
        for provider in ['chatgpt','claude','deepseek']:
            validate_decision(decision('execute',[provider]),RULES,[])
        for agents in [['chatgpt','claude'],[{}],['claude','claude']]:
            with self.assertRaises(ValueError):validate_decision(decision('execute',agents),RULES,[])


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_can_blind_parallel_ask_then_choose_any_writer_with_real_test(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'project';root.mkdir()
            subprocess.run(['git','init'],cwd=root,check=True,capture_output=True)
            (root/'test.js').write_text("const fs=require('fs');if(fs.readFileSync('result.txt','utf8')!=='OK')process.exit(1)",encoding='utf-8')
            store=Store(Path(folder)/'state');rid=store.create('Supervisor test',str(root))['id']
            task={'goal':'Write OK','allowed_paths':['result.txt'],'acceptance_commands':['node test.js'],'context':'Write exactly OK'}
            steps=[decision('ask',['chatgpt','deepseek'],parallel=True,share_previous=False),decision('execute',['claude'],task=task),decision('finish')]
            steps[-1]['checks'][0].update(status='met',evidence='两方独立发言，Claude 写入且实际 Node 验收通过',evidence_ids=['E01','E02','E03'])
            calls=[];work=workflows.Workflows(store)
            async def reply(rid,provider,prompt,root,settings,phase,coding=False):
                calls.append((provider,phase,coding,prompt))
                if provider=='supervisor':return json.dumps(steps.pop(0))
                if coding:(root/'result.txt').write_text('OK',encoding='utf-8');return 'STATUS: DONE'
                return '独立提案 '+provider
            work.reply=reply;selected=preferences.load();selected['models']['claude']='claude-haiku-5-5'
            connections={p:{'ready':True} for p in ['chatgpt','claude','deepseek']}
            with patch('roster.connections',return_value=connections),patch('engines.preferences.get_key',return_value='sk-fixture'):
                await work.start(rid,'Write OK','workflow','all',selected)
            self.assertEqual(store.room(rid)['status'],'complete',store.messages(rid))
            self.assertEqual((root/'result.txt').read_text(),'OK')
            self.assertTrue((root/'.tiered/REPORT.md').exists())
            worker_calls=[c for c in calls if c[0]!='supervisor']
            self.assertEqual([c[0] for c in worker_calls],['chatgpt','deepseek','claude'])
            self.assertTrue(worker_calls[-1][2]);self.assertIn('此前其他工作者的答复未提供',worker_calls[0][3])
            events=store.events(rid,0);self.assertTrue(any(e['type']=='test' and e['exit_code']==0 for e in events))
            store.db.close()

    async def test_rule_update_during_monitor_reply_discards_old_arrangement(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=Store(root/'state');rid=store.create('Rules',str(root))['id'];work=workflows.Workflows(store)
            calls=[];n=0
            async def reply(rid,provider,prompt,root,settings,phase,coding=False):
                nonlocal n
                calls.append(provider)
                if provider=='supervisor':
                    n+=1
                    if n==1:store.set_policy(rid,'新规则：只让 Claude 发言');return json.dumps(decision('ask',['deepseek']))
                    if n==2:return json.dumps(decision('ask',['claude']))
                    value=decision('finish');value['checks'][0].update(status='met',evidence='Claude 已回复',evidence_ids=['E01']);return json.dumps(value)
                return '已收到'
            work.reply=reply
            with patch('roster.connections',return_value={p:{'ready':True} for p in ['chatgpt','claude','deepseek']}):
                await work.start(rid,'检查规则','workflow','all',preferences.load())
            self.assertEqual(store.room(rid)['status'],'complete',store.messages(rid))
            self.assertNotIn('deepseek',calls);self.assertIn('claude',calls);store.db.close()

class ConfigurableTeamTests(unittest.IsolatedAsyncioTestCase):
    def custom(self,mid='qwen',name='自定义成员'):
        return dict(id=mid,name=name,backend='openai-compatible',model='custom-model',effort='high',base_url='https://example.invalid/v1',no_auth=False)

    async def test_configured_team_persists_duplicates_and_removed_member_history(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with patch.object(preferences,'STATE',base),patch.object(preferences,'PREFS',base/'prefs.json'),patch.object(preferences,'PROFILE',base/'profile.toml'):
                a=self.custom();b=self.custom('qwen2','同一家另一位')
                config=preferences.save({'members':[a,b],'supervisor':{'provider':'qwen','model':'monitor-model'}})
                self.assertEqual([m['id'] for m in preferences.load()['members']],['qwen','qwen2'])
                self.assertEqual(config['supervisor']['model'],'monitor-model')
                preferences.save({'members':[a]})
                self.assertEqual(preferences.load()['member_names']['qwen2'],'同一家另一位')
                with self.assertRaises(ValueError):preferences.save({'members':[b]})
                with self.assertRaises(ValueError):preferences.validate_members([a,a])
                a['base_url']='https://user:secret@example.invalid'
                with self.assertRaises(ValueError):preferences.validate_members([a])

    async def test_generic_api_routes_selected_url_model_and_private_key(self):
        m=self.custom();selected=preferences.load();selected['members']=[m];selected['supervisor'].update(provider='qwen',model='independent-monitor')
        requests=[]
        def handler(request):
            requests.append(request)
            chunk={'id':'fixture','object':'chat.completion.chunk','created':1,'model':'custom-model','choices':[{'index':0,'delta':{'content':'连接检查成功'},'finish_reason':None}]}
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
        real=engines.AsyncOpenAI
        def client(**kwargs):return real(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder);rid=store.create('generic API',folder)['id']
            with patch.object(engines,'AsyncOpenAI',side_effect=client),patch.object(preferences,'get_member_key',return_value='private-fixture-key'):
                await roster.reply(store,rid,'qwen','hello',Path(folder),selected,'讨论')
                await roster.reply(store,rid,'supervisor','hello',Path(folder),selected,'监督')
            self.assertEqual(str(requests[0].url),'https://example.invalid/v1/chat/completions')
            self.assertEqual(requests[0].headers['authorization'],'Bearer private-fixture-key')
            self.assertEqual(json.loads(requests[0].content)['model'],'custom-model')
            self.assertEqual(json.loads(requests[1].content)['model'],'independent-monitor')
            messages=store.messages(rid);self.assertEqual(messages[0]['provider'],'qwen');self.assertEqual(messages[1]['provider'],'supervisor')
            self.assertNotIn('private-fixture-key',json.dumps(messages));store.db.close()

    async def test_arbitrary_four_members_are_valid_and_removed_ids_rejected(self):
        members={'alpha','beta','gamma','delta'}
        validate_decision(decision('ask',list(members),parallel=True),RULES,[],members)
        with self.assertRaises(ValueError):validate_decision(decision('ask',['chatgpt']),RULES,[],members)

    async def test_member_credentials_are_isolated_and_custom_does_not_inherit_deepseek_key(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(preferences,'STATE',Path(folder)),patch.object(preferences,'get_key',return_value='official-only'):
                a=self.custom('alpha');b=self.custom('beta')
                self.assertEqual(preferences.get_member_key(a),'')
                preferences.save_member_key('alpha','private-alpha');preferences.save_member_key('beta','private-beta')
                self.assertEqual(preferences.get_member_key(a),'private-alpha');self.assertEqual(preferences.get_member_key(b),'private-beta')
                for path in (Path(folder)/'credentials').iterdir():self.assertNotIn(b'private-',path.read_bytes())

    async def test_custom_execution_adapter_uses_member_endpoint_and_key(self):
        previous=asyncio.get_event_loop_policy()
        try:import deepseek_server
        finally:asyncio.set_event_loop_policy(previous)
        from deepseek_mcp.config import Config
        with tempfile.TemporaryDirectory() as folder:
            original=Config('original-secret',Path(folder),allowed_tools=['Read'])
            with patch.object(deepseek_server,'_original_load',return_value=original),patch.dict(os.environ,{'TIERED_MEMBER_API_KEY':'member-secret','TIERED_MEMBER_BASE_URL':'https://example.invalid/v1','TIERED_DEEPSEEK_MODEL':'custom-code-model'}):
                selected=deepseek_server._selected_config()
            self.assertEqual(selected.api_key,'member-secret');self.assertEqual(selected.base_url,'https://example.invalid/v1');self.assertEqual(selected.model,'custom-code-model')

if __name__=='__main__':
    logging.getLogger().setLevel(logging.CRITICAL)
    try: unittest.main()
    finally:
        server.STORE.db.close()
        for item in reversed(PREFERENCE_PATCHES):item.stop()
        TEST_HOME.cleanup()
