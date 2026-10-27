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
import time
import unittest
import logging
from unittest.mock import AsyncMock,Mock,patch

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
import debugger
import model_catalog
import account_usage
import logins
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
    async def test_room_instance_api_isolates_aliases_avatars_and_dispatch(self):
        from avatars import Avatars
        png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jKZkAAAAASUVORK5CYII='
        headers={'X-Tiered-Token':server.TOKEN}
        preferences.save({});original=preferences.PREFS.read_bytes()
        with tempfile.TemporaryDirectory(dir=TEST_HOME.name) as folder,patch.object(server,'AVATARS',Avatars(Path(folder)/'avatars.json')):
            rid=(await self.client.post('/api/rooms',json={'project':folder},headers=headers)).json()['id']
            base='/api/rooms/'+rid+'/members'
            self.assertEqual((await self.client.post(base,json={'agent_id':'chatgpt'})).status_code,403)
            replies=await asyncio.gather(*(self.client.post(base,json={'agent_id':'chatgpt'},headers=headers) for _ in range(2)))
            self.assertTrue(all(r.status_code==200 for r in replies))
            selected=(await self.client.get('/api/rooms/'+rid)).json()['room_settings']
            a,b=selected['members'];self.assertNotEqual(a['id'],b['id'])
            self.assertTrue((await roster.connections(selected))[a['id']]['ready'])
            response=await self.client.post(base+'/'+a['id'],json={'name':'方案设计师'},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['room_settings']['members'][1],b)
            avatar_url=base+'/'+a['id']+'/avatar'
            self.assertEqual((await self.client.post(avatar_url,json={'image':png})).status_code,403)
            response=await self.client.post(avatar_url,json={'image':png},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            key='room:'+rid+':'+a['id']
            self.assertEqual(response.json()['avatars'],{key:png})
            self.assertEqual((await self.client.post(base+'/'+b['id']+'/avatar',json={'image':'data:image/svg+xml,bad'},headers=headers)).status_code,400)
            other=(await self.client.post('/api/rooms',json={'project':folder},headers=headers)).json()['id']
            self.assertEqual((await self.client.post('/api/rooms/'+other+'/members/'+a['id']+'/avatar',json={'image':png},headers=headers)).status_code,400)
            state=(await self.client.get('/api/connection-state?room_id='+rid)).json()
            self.assertEqual(state['avatars'],{key:png})
            server.STORE.message(rid,a['id'],'实例消息')
            exported=await self.client.get('/api/rooms/'+rid+'/export')
            self.assertIn('## 方案设计师',exported.text)
            with patch.object(server.WORK,'start') as start:
                sent=await self.client.post('/api/rooms/'+rid+'/send',json={'text':'测试实例','target':b['id']},headers=headers)
                self.assertEqual(sent.status_code,200,sent.text)
                self.assertEqual(start.call_args.args[3],b['id'])
                self.assertEqual([m['id'] for m in start.call_args.args[-1]['members']],[a['id'],b['id']])
            removed=await self.client.post(base+'/'+a['id']+'/membership',json={'enabled':False},headers=headers)
            self.assertEqual(removed.json()['room_settings']['members'],[b])
            self.assertIn('## 方案设计师',(await self.client.get('/api/rooms/'+rid+'/export')).text)
            reset=await self.client.post(avatar_url,json={'image':None},headers=headers)
            self.assertEqual(reset.json()['avatars'],{})
            self.assertEqual(preferences.PREFS.read_bytes(),original)

    async def asyncSetUp(self):
        self.pref_home=tempfile.TemporaryDirectory(dir=TEST_HOME.name)
        base=Path(self.pref_home.name)
        self.patches=[patch.object(preferences,'STATE',base),patch.object(preferences,'PREFS',base/'preferences.json'),
            patch.object(preferences,'SECRET',base/'deepseek.dpapi'),patch.object(preferences,'PROFILE',base/'profile.toml'),
            patch.object(roster,'_auth_cache',{}),patch.object(roster,'auth_status',return_value={
                'chatgpt':{'ready':True,'detail':'已登录'},'claude':{'ready':True,'detail':'已登录'}})]
        self.active_patches=[item.start() for item in self.patches]
        self.debug_patch=patch.object(server,'DEBUG',debugger.AgentDebugger());self.debug_patch.start()
        self.model_patch=patch.object(server,'MODELS',model_catalog.ModelCatalog(base/'model-catalog.json'));self.model_patch.start()
        self.discovery_patch=patch.object(model_catalog,'discover',return_value=[{'id':'fixture-discovered-model','label':'Fixture model'}]);self.discovery=self.discovery_patch.start()
        self.usage_patch=patch.object(server,'USAGE',account_usage.UsageMonitor());self.usage_patch.start()
        self.read_usage_patch=patch.object(account_usage,'read_account',return_value={'status':'ready','plan':'fixture','windows':[{'label':'5 小时','used_percent':20,'remaining_percent':80,'resets_at':None}],'balances':[],'detail':'fixture'});self.read_usage=self.read_usage_patch.start()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url=server.ORIGIN)

    async def asyncTearDown(self):
        await server.USAGE.close();self.read_usage_patch.stop();self.usage_patch.stop()
        await server.MODELS.close();self.discovery_patch.stop();self.model_patch.stop()
        await server.DEBUG.close();self.debug_patch.stop()
        await self.client.aclose()
        for item in reversed(self.patches):item.stop()
        self.pref_home.cleanup()

    async def test_login_requires_csrf_and_reports_missing_cli_in_live_status(self):
        manager=logins.Logins(lambda:None)
        with patch.object(server,'LOGIN',manager),patch.object(logins,'find_cli',return_value=None):
            self.assertEqual((await self.client.post('/api/login/chatgpt',json={})).status_code,403)
            response=await self.client.post('/api/login/chatgpt',json={},headers={'X-Tiered-Token':server.TOKEN})
            self.assertEqual(response.status_code,400)
            self.assertIn('未找到 ChatGPT CLI',response.json()['error'])
            state=(await self.client.get('/api/connection-state')).json()
            self.assertEqual(state['logins']['chatgpt']['status'],'error')

    async def test_successful_login_invalidates_cached_logged_out_connection(self):
        manager=logins.Logins(server.login_complete)
        proc=Mock(returncode=0,communicate=AsyncMock(return_value=(b'',b'')))
        previous={'chatgpt':{'ready':False,'detail':'需要登录'},'claude':{'ready':True,'detail':'已登录'}}
        with patch.object(server,'LOGIN',manager),patch.object(logins,'find_cli',return_value='fixture-codex.exe'),\
             patch.object(logins.asyncio,'create_subprocess_exec',new_callable=AsyncMock,return_value=proc),\
             patch.object(logins,'auth_status',new_callable=AsyncMock,return_value={'chatgpt':{'ready':True}}),\
             patch.object(roster,'_auth_cache',previous),patch.object(roster,'_auth_checked',time.monotonic()):
            response=await self.client.post('/api/login/chatgpt',json={},headers={'X-Tiered-Token':server.TOKEN})
            self.assertEqual(response.status_code,200)
            await manager.tasks['chatgpt']
            state=(await self.client.get('/api/connection-state')).json()
            self.assertTrue(state['connections']['chatgpt']['ready'])
            self.assertEqual(state['logins']['chatgpt']['status'],'complete')

    async def test_rooms_have_independent_members_and_dispatch_uses_room_configuration(self):
        headers={'X-Tiered-Token':server.TOKEN};saved=preferences.save({});before=preferences.PREFS.read_bytes()
        with tempfile.TemporaryDirectory(dir=TEST_HOME.name) as folder:
            a=(await self.client.post('/api/rooms',json={'title':'A','project':folder},headers=headers)).json()['id']
            b=(await self.client.post('/api/rooms',json={'title':'B','project':folder},headers=headers)).json()['id']
            self.assertEqual((await self.client.get('/api/rooms/'+a)).json()['room_settings']['members'],[])
            for rid,aid in [(a,'chatgpt'),(b,'claude')]:
                url='/api/rooms/'+rid+'/members/'+aid+'/membership'
                self.assertEqual((await self.client.post(url,json={'enabled':True})).status_code,403)
                self.assertEqual((await self.client.post(url,json={'enabled':True},headers=headers)).status_code,200)
            await self.client.post('/api/rooms/'+a+'/members/chatgpt',json={'model':'room-a-model'},headers=headers)
            with patch.object(server.WORK,'start') as start:
                response=await self.client.post('/api/rooms/'+a+'/send',json={'text':'test','preferences':{'team':[]}},headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                settings=start.call_args.args[-1]
                self.assertEqual([m['id'] for m in settings['members']],['chatgpt'])
                self.assertEqual(settings['members'][0]['model'],'room-a-model')
            bstate=(await self.client.get('/api/connection-state?room_id='+b)).json()['room_settings']
            self.assertEqual([m['id'] for m in bstate['members']],['claude'])
            self.assertEqual(preferences.PREFS.read_bytes(),before)

    async def test_two_room_invitations_are_atomic_and_room_edits_cannot_change_connections(self):
        headers={'X-Tiered-Token':server.TOKEN}
        with tempfile.TemporaryDirectory(dir=TEST_HOME.name) as folder:
            rid=(await self.client.post('/api/rooms',json={'project':folder},headers=headers)).json()['id']
            replies=await asyncio.gather(*(self.client.post('/api/rooms/'+rid+'/members/'+aid+'/membership',
                json={'enabled':True},headers=headers) for aid in ['chatgpt','claude']))
            self.assertTrue(all(r.status_code==200 for r in replies))
            selected=(await self.client.get('/api/rooms/'+rid)).json()['room_settings']
            self.assertEqual({m['id'] for m in selected['members']},{'chatgpt','claude'})
            response=await self.client.post('/api/rooms/'+rid+'/members/chatgpt',json={'base_url':'https://wrong.example'},headers=headers)
            self.assertEqual(response.status_code,400)

    async def test_connection_snapshot_tracks_roster_and_key_changes_without_restart(self):
        headers={'X-Tiered-Token':server.TOKEN}
        self.assertEqual(len((await self.client.get('/api/connection-state')).json()['preferences']['members']),3)
        member={'create':True,'name':'Gemini','backend':'openai-compatible','model':'gemini-3.8-flash',
            'base_url':'https://generativelanguage.googleapis.com/v1beta/openai','effort':'high','no_auth':False}
        created=await self.client.post('/api/agents/gemini',json=member,headers=headers)
        self.assertEqual(created.status_code,200,created.text)
        state=(await self.client.get('/api/connection-state')).json()
        self.assertEqual(len(state['preferences']['agents']),4)
        self.assertEqual(len(state['preferences']['members']),3)
        joined=await self.client.post('/api/team/gemini',json={'enabled':True},headers=headers)
        self.assertEqual(len(joined.json()['preferences']['members']),4)
        self.assertFalse(state['connections']['gemini']['ready'])
        fixture_key='fixture-gemini-key-not-a-real-credential'
        saved=await self.client.post('/api/agents/gemini/key',json={'key':fixture_key},headers=headers)
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertTrue(saved.json()['connections']['gemini']['ready'])
        self.assertNotIn(fixture_key,saved.text)
        updated=(await self.client.get('/api/connection-state')).json()
        self.assertTrue(updated['connections']['gemini']['ready'])
        self.assertNotIn(fixture_key,json.dumps(updated))
        await self.client.post('/api/members/gemini/remove',json={},headers=headers)
        latest=(await self.client.get('/api/connection-state')).json()
        self.assertTrue(latest['connections']['gemini']['ready'])
        self.assertEqual(len(latest['preferences']['members']),3)
        self.assertEqual(len(latest['preferences']['agents']),4)
        # An inactive agent can still be edited, and rejoining never requires a key.
        edited=await self.client.post('/api/agents/gemini',json={'name':'Gemini saved'},headers=headers)
        self.assertEqual(edited.status_code,200,edited.text)
        rejoined=await self.client.post('/api/team/gemini',json={'enabled':True},headers=headers)
        active=next(m for m in rejoined.json()['preferences']['members'] if m['id']=='gemini')
        self.assertEqual(active['name'],'Gemini saved')
        self.assertEqual(preferences.get_member_key(active),fixture_key)
        self.assertNotIn(fixture_key,rejoined.text)

    async def test_partial_member_updates_preserve_other_members_and_monitor_connection(self):
        headers={'X-Tiered-Token':server.TOKEN}
        created=await self.client.post('/api/members/extra',json={'create':True,'name':'另一位','backend':'claude',
            'model':'claude-haiku-5-5'},headers=headers)
        self.assertEqual(created.status_code,200,created.text)
        updated=await self.client.post('/api/members/chatgpt',json={'model':'gpt-6.1-sol'},headers=headers)
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertEqual([m['id'] for m in updated.json()['preferences']['members']],['chatgpt','claude','deepseek','extra'])
        removed=await self.client.post('/api/members/chatgpt/remove',json={},headers=headers)
        self.assertEqual(removed.status_code,200,removed.text)
        self.assertEqual(removed.json()['preferences']['supervisor']['provider'],'chatgpt')
        self.assertIn('chatgpt',[a['id'] for a in removed.json()['preferences']['agents']])
        refused=await self.client.post('/api/members/chatgpt',json={'name':'stale editor'},headers=headers)
        self.assertEqual(refused.status_code,400)
        rejected=await self.client.post('/api/members/claude/key',json={'key':'unused-fixture'},headers=headers)
        self.assertEqual(rejected.status_code,400)

    async def test_team_model_override_survives_disable_without_changing_library_default(self):
        headers={'X-Tiered-Token':server.TOKEN}
        default=preferences.load()['agents'][0]['model']
        changed=await self.client.post('/api/members/chatgpt',json={'model':'gpt-6.1-sol'},headers=headers)
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(changed.json()['preferences']['agents'][0]['model'],default)
        await self.client.post('/api/team/chatgpt',json={'enabled':False},headers=headers)
        await self.client.post('/api/agents/chatgpt',json={'model':'another-default'},headers=headers)
        returned=await self.client.post('/api/team/chatgpt',json={'enabled':True},headers=headers)
        current=returned.json()['preferences']
        self.assertEqual(current['agents'][0]['model'],'another-default')
        self.assertEqual(current['members'][0]['model'],'gpt-6.1-sol')
        self.assertEqual(len(current['agents']),3)
        refused=await self.client.post('/api/team/unsaved',json={'enabled':True},headers=headers)
        self.assertEqual(refused.status_code,400)

    async def test_empty_team_is_saved_but_cannot_start_workers(self):
        headers={'X-Tiered-Token':server.TOKEN}
        for aid in ['chatgpt','claude','deepseek']:
            response=await self.client.post('/api/team/'+aid,json={'enabled':False},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
        selected=preferences.load()
        self.assertEqual(selected['members'],[])
        self.assertEqual(len(selected['agents']),3)
        self.assertEqual(selected['supervisor']['provider'],'chatgpt')
        with tempfile.TemporaryDirectory() as folder:
            rid=server.STORE.create('Empty team',folder)['id']
            with patch.object(server.WORK,'reply') as reply:
                response=await self.client.post('/api/rooms/'+rid+'/send',json={'text':'do work','mode':'workflow'},headers=headers)
                self.assertEqual(response.status_code,400,response.text)
                reply.assert_not_called()
            self.assertEqual(server.STORE.messages(rid),[])
        rejoined=await self.client.post('/api/team/claude',json={'enabled':True},headers=headers)
        self.assertEqual([m['id'] for m in rejoined.json()['preferences']['members']],['claude'])

    async def test_saved_inactive_agent_debug_streams_independently_with_temporary_model(self):
        headers={'X-Tiered-Token':server.TOKEN}
        agent={'create':True,'name':'Gemini','backend':'openai-compatible','model':'saved-default',
            'base_url':'https://generativelanguage.googleapis.com/v1beta/openai'}
        await self.client.post('/api/agents/gemini',json=agent,headers=headers)
        await self.client.post('/api/agents/gemini/key',json={'key':'debug-private-fixture'},headers=headers)
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def provider(sink,rid,prompt,model,phase,**kwargs):
            calls.append((prompt,model,kwargs));mid=sink.message(rid,'gemini',model=model)
            sink.update(mid,'partial visible reply');entered.set();await release.wait()
            sink.update(mid,'complete visible reply','complete');return 'complete visible reply'
        rooms_before=len(server.STORE.rooms())
        with patch.object(debugger,'deepseek_reply',side_effect=provider):
            response=await self.client.post('/api/agents/gemini/debug',json={'prompt':'Test only','model':'temporary-model'},headers=headers)
            self.assertEqual(response.status_code,200,response.text);jid=response.json()['id']
            await asyncio.wait_for(entered.wait(),2)
            state=(await self.client.get('/api/agents/gemini/debug')).json()['jobs'][0]
            self.assertEqual(state['status'],'running');self.assertEqual(state['output'],'partial visible reply')
            duplicate=await self.client.post('/api/agents/gemini/debug',json={'prompt':'duplicate'},headers=headers)
            self.assertEqual(duplicate.status_code,400)
            release.set();await server.DEBUG.get(jid).task
            streamed=await self.client.get('/api/debug/'+jid+'/events')
            self.assertIn('text/event-stream',streamed.headers['content-type'])
            self.assertIn('complete visible reply',streamed.text)
            self.assertNotIn('debug-private-fixture',streamed.text)
        self.assertEqual(calls[0][1],'temporary-model')
        self.assertEqual(calls[0][2]['base_url'],agent['base_url'])
        selected=preferences.load()
        self.assertNotIn('gemini',[m['id'] for m in selected['members']])
        self.assertEqual(roster.agent(selected,'gemini')['model'],'saved-default')
        self.assertEqual(len(server.STORE.rooms()),rooms_before)
        self.assertEqual((await self.client.get('/api/agents/claude/debug')).json()['jobs'],[])

    async def test_debug_stop_cleans_adapter_and_keeps_partial_output(self):
        headers={'X-Tiered-Token':server.TOKEN};entered=asyncio.Event();cleaned=asyncio.Event()
        async def provider(sink,rid,*args,**kwargs):
            mid=sink.message(rid,'chatgpt');sink.update(mid,'partial');entered.set()
            try:await asyncio.Event().wait()
            finally:cleaned.set()
        with patch.object(debugger,'cli_reply',side_effect=provider):
            response=await self.client.post('/api/agents/chatgpt/debug',json={'prompt':'test'},headers=headers)
            jid=response.json()['id'];await asyncio.wait_for(entered.wait(),2)
            stopped=await self.client.post('/api/debug/'+jid+'/cancel',json={},headers=headers)
            self.assertEqual(stopped.json()['status'],'stopped');self.assertTrue(cleaned.is_set())
            self.assertEqual(stopped.json()['output'],'partial')
            self.assertFalse(stopped.json()['error'])

    async def test_debug_requires_saved_agent_and_reports_errors_without_key_leak(self):
        headers={'X-Tiered-Token':server.TOKEN}
        missing=await self.client.post('/api/agents/unsaved/debug',json={'prompt':'test'},headers=headers)
        self.assertEqual(missing.status_code,400)
        with patch.object(preferences,'get_member_key',return_value=''):
            no_key=await self.client.post('/api/agents/deepseek/debug',json={'prompt':'test'},headers=headers)
            self.assertEqual(no_key.status_code,400)
        with patch.object(preferences,'get_member_key',return_value='private-debug-fixture'),patch.object(debugger,'deepseek_reply',side_effect=RuntimeError('failed private-debug-fixture')):
            response=await self.client.post('/api/agents/deepseek/debug',json={'prompt':'test'},headers=headers)
            jid=response.json()['id'];await server.DEBUG.get(jid).task
            history=await self.client.get('/api/agents/deepseek/debug')
            self.assertNotIn('private-debug-fixture',history.text)
            self.assertEqual(history.json()['jobs'][0]['status'],'error')
            self.assertIn('[密钥已隐藏]',history.json()['jobs'][0]['error'])

    async def test_polling_reuses_cli_auth_status_and_explicit_refresh_rechecks_it(self):
        auth=self.active_patches[-1]
        await self.client.get('/api/connection-state')
        await self.client.get('/api/connection-state')
        self.assertEqual(auth.await_count,1)
        await self.client.get('/api/connection-state?refresh=1')
        self.assertEqual(auth.await_count,2)

    async def test_bootstrap_refreshes_shared_catalog_each_time_and_key_save_updates_inactive_agent(self):
        headers={'X-Tiered-Token':server.TOKEN}
        first=(await self.client.get('/api/bootstrap')).json()
        self.assertTrue(first['model_catalog']['refreshing'])
        await asyncio.gather(*server.MODELS.tasks.values())
        ready=(await self.client.get('/api/models')).json()
        self.assertEqual(ready['agents']['chatgpt']['models'][0]['id'],'fixture-discovered-model')
        called=self.discovery.await_count
        await self.client.get('/api/bootstrap');await asyncio.gather(*server.MODELS.tasks.values())
        self.assertGreater(self.discovery.await_count,called)
        gemini={'create':True,'name':'Gemini','backend':'openai-compatible','model':'keep-this-default',
            'base_url':'https://generativelanguage.googleapis.com/v1beta/openai'}
        await self.client.post('/api/agents/gemini',json=gemini,headers=headers)
        response=await self.client.post('/api/agents/gemini/key',json={'key':'metadata-key-fixture'},headers=headers)
        self.assertTrue(response.json()['model_catalog']['refreshing'])
        await asyncio.gather(*server.MODELS.tasks.values())
        catalog=(await self.client.get('/api/models')).json()['agents']['gemini']
        self.assertEqual(catalog['models'][0]['id'],'fixture-discovered-model')
        self.assertNotIn('gemini',[m['id'] for m in preferences.load()['members']])
        self.assertEqual(roster.agent(preferences.load(),'gemini')['model'],'keep-this-default')

    async def test_usage_endpoint_reads_inactive_saved_connection_without_changing_team_or_history(self):
        headers={'X-Tiered-Token':server.TOKEN}
        await self.client.post('/api/members/deepseek/remove',json={},headers=headers)
        await self.client.post('/api/agents/deepseek/key',json={'key':'usage-key-fixture'},headers=headers)
        await asyncio.gather(*server.USAGE.tasks.values())
        rooms_before=len(server.STORE.rooms());saved=preferences.PREFS.read_bytes()
        rejected=await self.client.post('/api/agents/deepseek/usage',json={})
        self.assertEqual(rejected.status_code,403)
        self.read_usage.return_value={'status':'ready','plan':'','windows':[],'balances':[{'currency':'CNY','total_balance':'12.50'}],'detail':'fixture balance'}
        started=await self.client.post('/api/agents/deepseek/usage',json={},headers=headers)
        self.assertEqual(started.status_code,200,started.text)
        await asyncio.gather(*server.USAGE.tasks.values())
        state=(await self.client.get('/api/usage')).json()['agents']['deepseek']
        self.assertEqual(state['balances'][0]['total_balance'],'12.50')
        self.assertNotIn('usage-key-fixture',json.dumps(state))
        self.assertEqual(preferences.PREFS.read_bytes(),saved)
        self.assertNotIn('deepseek',[m['id'] for m in preferences.load()['members']])
        self.assertEqual(len(server.STORE.rooms()),rooms_before)

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

    async def test_groups_archive_rename_and_delete_room(self):
        headers={'X-Tiered-Token':server.TOKEN}
        with tempfile.TemporaryDirectory() as first,tempfile.TemporaryDirectory() as second:
            group=(await self.client.post('/api/groups',json={'name':' 前端 ','path':first},headers=headers)).json()
            self.assertEqual(group['name'],'前端')
            created=await self.client.post('/api/rooms',json={'title':'分组协作','group_id':group['id']},headers=headers)
            self.assertEqual(created.status_code,200,created.text);room=created.json()
            self.assertEqual((room['group_id'],room['project']),(group['id'],str(server.project_path(first))))
            boot=(await self.client.get('/api/bootstrap')).json()
            self.assertIn(group['id'],[g['id'] for g in boot['groups']])
            renamed=(await self.client.post('/api/rooms/'+room['id']+'/meta',json={'title':'新的名字','archived':True},headers=headers)).json()
            self.assertEqual(renamed['title'],'新的名字');self.assertTrue(renamed['archived'])
            bad=await self.client.post('/api/rooms/'+room['id']+'/meta',json={'title':'x','owner':'y'},headers=headers)
            self.assertEqual(bad.status_code,400)
            epoch=server.ROOMS.load(room['id']).get('context_epoch',0)
            moved=(await self.client.post('/api/groups/'+group['id'],json={'path':second,'apply_to_rooms':True},headers=headers)).json()
            self.assertEqual(moved['rooms'][0]['project'],str(server.project_path(second)))
            self.assertEqual(server.ROOMS.load(room['id'])['context_epoch'],epoch+1)
            server.STORE.message(room['id'],'chatgpt','保留到删除前')
            server.STORE.status(room['id'],'running')
            refused=await self.client.post('/api/rooms/'+room['id']+'/delete',json={},headers=headers)
            self.assertEqual(refused.status_code,400)
            server.STORE.status(room['id'],'idle')
            self.assertEqual((await self.client.post('/api/groups/'+group['id']+'/delete',json={},headers=headers)).json()['ungrouped'],1)
            self.assertIsNone(server.STORE.room(room['id'])['group_id'])
            deleted=await self.client.post('/api/rooms/'+room['id']+'/delete',json={},headers=headers)
            self.assertEqual(deleted.status_code,200,deleted.text)
            self.assertNotIn(room['id'],[r['id'] for r in server.STORE.rooms()])
            with server.STORE.lock:
                self.assertEqual(server.STORE.db.execute('SELECT count(*) FROM messages WHERE room=?',(room['id'],)).fetchone()[0],0)
                self.assertEqual(server.STORE.db.execute('SELECT count(*) FROM room_settings WHERE room=?',(room['id'],)).fetchone()[0],0)
            self.assertTrue(Path(first).is_dir())

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


class ModelCatalogTests(unittest.IsolatedAsyncioTestCase):
    def agent(self,aid='gemini',url='https://generativelanguage.googleapis.com/v1beta/openai'):
        return dict(id=aid,backend='openai-compatible',name=aid,model='saved-default',effort='high',base_url=url,no_auth=False)

    async def test_only_the_four_official_providers_are_queried(self):
        for url,provider in [('https://api.openai.com/v1','openai'),('https://api.anthropic.com/v1','claude-api'),
            ('https://generativelanguage.googleapis.com/v1beta/openai','gemini'),('https://api.deepseek.com','deepseek')]:
            self.assertEqual(model_catalog.provider_for(self.agent(url=url)),provider)
        for url in ['https://example.com/v1','https://api.openai.com.example.com/v1','http://localhost:8000/v1']:
            self.assertIsNone(model_catalog.provider_for(self.agent(url=url)))

    async def test_claude_api_metadata_pagination_uses_its_own_authentication(self):
        calls=[]
        def handler(request):
            calls.append(request);following=request.url.params.get('after_id')
            return httpx.Response(200,json={'data':[{'id':'claude-page-two' if following else 'claude-page-one','display_name':'Claude'}],
                'has_more':not following,'last_id':'cursor-one'})
        real=httpx.AsyncClient
        with patch.object(model_catalog.httpx,'AsyncClient',side_effect=lambda **kwargs:real(**kwargs,transport=httpx.MockTransport(handler))):
            result=await model_catalog.discover(self.agent(url='https://api.anthropic.com/v1'),'claude-private-fixture')
        self.assertEqual([m['id'] for m in result],['claude-page-one','claude-page-two'])
        self.assertEqual(calls[0].headers['x-api-key'],'claude-private-fixture')
        self.assertNotIn('authorization',calls[0].headers)
        self.assertTrue(all(r.method=='GET' and r.url.path=='/v1/models' for r in calls))

    async def test_connection_deduplication_credential_isolation_and_four_provider_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog=model_catalog.ModelCatalog(Path(folder)/'catalog.json')
            agents=[self.agent('one'),self.agent('same'),self.agent('other'),self.agent('qwen','https://dashscope.aliyuncs.com/compatible-mode/v1')]
            keys={'one':'private-one','same':'private-one','other':'private-other','qwen':'private-qwen'}
            async def discover(agent,key):return [{'id':agent['id']+'-model'}]
            with patch.object(preferences,'get_member_key',side_effect=lambda a:keys[a['id']]),patch.object(model_catalog,'discover',side_effect=discover) as fetch:
                settings={'agents':agents,'members':[]}
                pending=catalog.request_refresh(settings)
                self.assertEqual(len(pending),2)
                self.assertEqual(catalog.request_refresh(settings),pending)
                await asyncio.gather(*pending)
                snap=catalog.snapshot(settings)['agents']
                self.assertEqual(fetch.await_count,2)
                self.assertEqual(snap['one']['models'],snap['same']['models'])
                self.assertNotEqual(snap['one']['models'],snap['other']['models'])
                self.assertEqual(snap['qwen']['state'],'manual')
                self.assertFalse(any(key in catalog.path.read_text() for key in keys.values()))
                await asyncio.gather(*catalog.request_refresh(settings))
                self.assertEqual(fetch.await_count,4)
                self.assertEqual([a['model'] for a in agents],['saved-default']*4)
            await catalog.close()

    async def test_failed_refresh_retains_disk_cache_without_leaking_errors_or_crossing_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'catalog.json';catalog=model_catalog.ModelCatalog(path);agent=self.agent();settings={'agents':[agent]}
            with patch.object(preferences,'get_member_key',return_value='account-one-private'):
                with patch.object(model_catalog,'discover',return_value=[{'id':'known-model'},{'id':'known-model'},{'id':'bad;model'}]):
                    await asyncio.gather(*catalog.request_refresh(settings))
                self.assertEqual(len(catalog.snapshot(settings)['agents']['gemini']['models']),1)
                reloaded=model_catalog.ModelCatalog(path)
                with patch.object(model_catalog,'discover',side_effect=RuntimeError('connection account-one-private failed')):
                    await asyncio.gather(*reloaded.request_refresh(settings))
                snap=reloaded.snapshot(settings)
                self.assertEqual(snap['agents']['gemini']['state'],'error')
                self.assertEqual(snap['agents']['gemini']['models'][0]['id'],'known-model')
                self.assertNotIn('account-one-private',json.dumps(snap))
                await reloaded.close()
            with patch.object(preferences,'get_member_key',return_value='different-account-private'):
                self.assertEqual(catalog.snapshot(settings)['agents']['gemini']['models'],[])
            await catalog.close()

    async def test_disabled_gemini_api_has_specific_error_and_trusted_action_link(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog=model_catalog.ModelCatalog(Path(folder)/'catalog.json');agent=self.agent();settings={'agents':[agent]}
            error=RuntimeError('private-credential');error.status_code=403
            error.body={'message':'Gemini API has not been used in project 123456789 before or it is disabled. Enable https://console.example.invalid/generativelanguage.googleapis.com/overview.'}
            with patch.object(preferences,'get_member_key',return_value='private-credential'),patch.object(model_catalog,'discover',side_effect=error):
                await asyncio.gather(*catalog.request_refresh(settings))
                entry=catalog.snapshot(settings)['agents']['gemini']
                self.assertIn('尚未启用 Gemini API',entry['error'])
                self.assertEqual(entry['action_url'],'https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com?project=123456789')
                self.assertNotIn('private-credential',json.dumps(entry))
            await catalog.close()

    async def test_blocked_gemini_key_points_to_credential_restrictions(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog=model_catalog.ModelCatalog(Path(folder)/'catalog.json');settings={'agents':[self.agent()]}
            error=RuntimeError('private-credential');error.status_code=403
            error.body={'message':'ListModels are blocked','details':[{'reason':'API_KEY_SERVICE_BLOCKED','metadata':{'consumer':'projects/123456789'}}]}
            with patch.object(preferences,'get_member_key',return_value='private-credential'),patch.object(model_catalog,'discover',side_effect=error):
                await asyncio.gather(*catalog.request_refresh(settings))
                entry=catalog.snapshot(settings)['agents']['gemini']
                self.assertIn('API_KEY_SERVICE_BLOCKED',entry['error'])
                self.assertEqual(entry['action_url'],'https://console.cloud.google.com/apis/credentials?project=123456789')
                self.assertEqual(entry['action_label'],'检查 Gemini 密钥限制')
            await catalog.close()

    async def test_changed_connection_does_not_receive_late_previous_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog=model_catalog.ModelCatalog(Path(folder)/'catalog.json');old_started=asyncio.Event();release=asyncio.Event()
            agent=self.agent();settings={'agents':[agent]};key=['old-key']
            async def discover(agent,secret):
                if secret=='old-key':old_started.set();await release.wait()
                return [{'id':secret+'-model'}]
            with patch.object(preferences,'get_member_key',side_effect=lambda a:key[0]),patch.object(model_catalog,'discover',side_effect=discover):
                old=catalog.request_refresh(settings);await asyncio.wait_for(old_started.wait(),2)
                key[0]='new-key';await asyncio.gather(*catalog.request_refresh(settings))
                release.set();await asyncio.gather(*old)
                self.assertEqual(catalog.snapshot(settings)['agents']['gemini']['models'][0]['id'],'new-key-model')
            await catalog.close()

    async def test_api_discovery_uses_metadata_endpoint_without_inference(self):
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(200,json={'object':'list','data':[{'id':'live-directory-model','object':'model','created':1,'owned_by':'fixture'}]})
        real=model_catalog.AsyncOpenAI
        def client(**kwargs):return real(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with patch.object(model_catalog,'AsyncOpenAI',side_effect=client):
            result=await model_catalog.discover(self.agent(),'private-fixture')
        self.assertEqual(result[0]['id'],'live-directory-model')
        self.assertEqual(str(calls[0].url),'https://generativelanguage.googleapis.com/v1beta/openai/models')
        self.assertEqual(calls[0].method,'GET')
        self.assertEqual(calls[0].headers['authorization'],'Bearer private-fixture')


class AccountUsageTests(unittest.IsolatedAsyncioTestCase):
    def agent(self,aid='deepseek',backend='deepseek',url='https://api.deepseek.com'):
        return dict(id=aid,name=aid,backend=backend,model='test-model',effort='high',base_url=url,no_auth=False)

    async def test_plan_windows_distinguish_unknown_from_zero_and_keep_remaining_correct(self):
        data={'rateLimitsByLimitId':{'codex':{'planType':'plus','primary':{'usedPercent':28,'windowDurationMins':300,'resetsAt':1792561301},'secondary':{'usedPercent':None,'windowDurationMins':10080}}}}
        value=account_usage.codex_meter(data)
        self.assertEqual(len(value['windows']),1);self.assertEqual(value['windows'][0]['remaining_percent'],72)
        self.assertNotIn('accountId',value)
        claude=account_usage.claude_meter({'subscription_type':'pro','rate_limits_available':True,'rate_limits':{'five_hour':{'utilization':0,'resets_at':None},'seven_day':{'utilization':81,'resets_at':'2026-10-24T10:59:59Z'}}})
        self.assertEqual([w['remaining_percent'] for w in claude['windows']],[100,19])
        unavailable=account_usage.claude_meter({'rate_limits_available':False,'rate_limits':{'five_hour':{'utilization':0}}})
        self.assertEqual(unavailable['windows'],[]);self.assertEqual(unavailable['status'],'unavailable')

    async def test_exact_credential_deduplication_and_ttl_without_external_balance_fabrication(self):
        agents=[self.agent('one'),self.agent('same'),self.agent('other'),self.agent('gemini','openai-compatible','https://generativelanguage.googleapis.com/v1beta/openai')]
        keys={'one':'private-one','same':'private-one','other':'private-other','gemini':'private-gemini'}
        async def read(agent,key):return {'status':'ready','plan':'','windows':[],'balances':[{'currency':'CNY','total_balance':'12.50' if key=='private-one' else '8.00'}],'detail':'balance'}
        monitor=account_usage.UsageMonitor();settings={'agents':agents}
        with patch.object(preferences,'get_member_key',side_effect=lambda a:keys[a['id']]),patch.object(account_usage,'read_account',side_effect=read) as reader:
            tasks=monitor.request_refresh(settings);self.assertEqual(len(tasks),2)
            self.assertEqual(monitor.request_refresh(settings),tasks);await asyncio.gather(*tasks)
            state=monitor.snapshot(settings)['agents']
            self.assertEqual(reader.await_count,2)
            self.assertEqual(state['one']['balances'],state['same']['balances'])
            self.assertNotEqual(state['one']['balances'],state['other']['balances'])
            self.assertEqual(state['gemini']['status'],'external');self.assertEqual(state['gemini']['balances'],[])
            self.assertEqual(monitor.request_refresh(settings),[])
            await asyncio.gather(*monitor.request_refresh(settings,force=True));self.assertEqual(reader.await_count,4)
            self.assertTrue(all(k not in json.dumps(state) for k in keys.values()))
            with patch.object(account_usage,'read_account',side_effect=RuntimeError('private-one')):
                await asyncio.gather(*monitor.request_refresh(settings,force=True))
            failed=monitor.snapshot(settings)['agents']['one']
            self.assertEqual(failed['status'],'ready');self.assertTrue(failed['cached']);self.assertEqual(failed['balances'][0]['total_balance'],'12.50')
            self.assertNotIn('private-one',json.dumps(failed))
            keys['one']='rotated-private-key'
            self.assertEqual(monitor.snapshot(settings)['agents']['one']['balances'],[])
        await monitor.close()

    async def test_deepseek_balance_reads_only_official_endpoint_and_keeps_actual_zero(self):
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(200,json={'is_available':False,'balance_infos':[{'currency':'CNY','total_balance':'0.00','granted_balance':'0.00','topped_up_balance':'0.00'}]})
        real=httpx.AsyncClient
        with patch.object(account_usage.httpx,'AsyncClient',side_effect=lambda **kwargs:real(**kwargs,transport=httpx.MockTransport(handler))):
            result=await account_usage.read_account(self.agent(),'private-fixture')
        self.assertEqual(result['status'],'ready');self.assertEqual(result['balances'][0]['total_balance'],'0.00')
        self.assertEqual(calls[0].method,'GET');self.assertEqual(str(calls[0].url),'https://api.deepseek.com/user/balance')
        self.assertEqual(calls[0].headers['authorization'],'Bearer private-fixture')
        self.assertNotIn('private-fixture',json.dumps(result))

    async def test_usage_refresh_can_cancel_without_leaving_readers_running(self):
        started=asyncio.Event();cleaned=asyncio.Event();monitor=account_usage.UsageMonitor()
        async def reader(agent,key):
            started.set()
            try:await asyncio.Event().wait()
            finally:cleaned.set()
        with patch.object(preferences,'get_member_key',return_value='private'),patch.object(account_usage,'read_account',side_effect=reader):
            monitor.request_refresh({'agents':[self.agent()]});await asyncio.wait_for(started.wait(),2)
            await monitor.close();self.assertTrue(cleaned.is_set())


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
        from test_token_optimization import control,RULES
        from workflow_state import current_refs
        from preflight import check
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'project';root.mkdir()
            subprocess.run(['git','init'],cwd=root,check=True,capture_output=True)
            (root/'test.js').write_text("const fs=require('fs');if(fs.readFileSync('result.txt','utf8')!=='OK')process.exit(1)",encoding='utf-8')
            store=Store(Path(folder)/'state');self.addCleanup(store.db.close)
            rid=store.create('Supervisor test',str(root))['id'];work=workflows.Workflows(store);calls=[];step=0
            task={'goal':'Write OK','allowed_paths':['result.txt'],'acceptance_commands':['node test.js'],'context':'Write exactly OK'}
            async def reply(rid,provider,prompt,root,settings,phase,coding=False,**context):
                nonlocal step
                calls.append((provider,phase,coding,prompt))
                if provider=='supervisor':
                    state,_=store.load_workflow(rid);step+=1
                    if step==1:value=control({'type':'ask','kind':'propose','agents':['chatgpt','deepseek'],'instruction':'independent proposal','parallel':True},RULES)
                    elif step==2:value=control({'type':'adopt_plan','plan_id':'P1','message_id':next(m['id'] for m in store.messages(rid) if m['provider']=='chatgpt')})
                    elif step==3:value=control({'type':'request_approval','plan_ref':'P1@1'})
                    elif step==4:value=control({'type':'execute','agents':['claude'],'task':task,'plan_ref':'P1@1','approval_ids':state['approvals']})
                    else:value=control({'type':'finish','full_checks':[{'rule_id':'R1','status':'met','note':'Two proposals and actual Node acceptance passed','evidence_ids':['E01','E02','E03'],'bound_to':current_refs(state)}]})
                    return json.dumps(value)
                if coding:(root/'result.txt').write_text('OK',encoding='utf-8');answer='STATUS: DONE'
                else:answer='独立提案 '+provider
                store.message(rid,provider,answer);return answer
            work.reply=reply;selected=preferences.load();selected['executor']=None
            connections={p:{'ready':True} for p in ['chatgpt','claude','deepseek']}
            with patch('roster.connections',return_value=connections),patch('coordination.preflight',new=AsyncMock(return_value=[check('fixture',True,'ok','host')])):
                await work.start(rid,'Write OK','workflow','all',selected)
                self.assertEqual(store.room(rid)['status'],'awaiting_approval');self.assertFalse((root/'result.txt').exists())
                await work.start(rid,'同意','workflow','all',selected)
            self.assertEqual(store.room(rid)['status'],'complete',store.messages(rid))
            self.assertEqual((root/'result.txt').read_text(),'OK');self.assertTrue((root/'.tiered/REPORT.md').exists())
            worker_calls=[c for c in calls if c[0]!='supervisor']
            self.assertEqual([c[0] for c in worker_calls],['chatgpt','deepseek','claude']);self.assertTrue(worker_calls[-1][2])
            self.assertTrue(any(e['type']=='test' and e['exit_code']==0 for e in store.events(rid,0)))
            store.db.close()

    async def test_rule_update_during_monitor_reply_discards_old_arrangement(self):
        from test_token_optimization import control,RULES
        from workflow_state import current_refs
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=Store(root/'state');self.addCleanup(store.db.close)
            rid=store.create('Rules',str(root))['id'];work=workflows.Workflows(store);calls=[];n=0
            async def reply(rid,provider,prompt,root,settings,phase,coding=False,**context):
                nonlocal n
                calls.append(provider)
                if provider=='supervisor':
                    n+=1
                    if n==1:store.set_policy(rid,'新规则：只让 Claude 发言');return json.dumps(control({'type':'ask','kind':'clarify','instruction':'answer','agents':['deepseek']},RULES))
                    if n==2:return json.dumps(control({'type':'ask','kind':'clarify','instruction':'answer','agents':['claude']},RULES))
                    state,_=store.load_workflow(rid)
                    return json.dumps(control({'type':'finish','full_checks':[{'rule_id':'R1','status':'met','note':'Claude replied','evidence_ids':['E01'],'bound_to':current_refs(state)}]}))
                store.message(rid,provider,'已收到');return '已收到'
            work.reply=reply
            with patch('roster.connections',return_value={p:{'ready':True} for p in ['chatgpt','claude','deepseek']}):
                await work.start(rid,'检查规则','workflow','all',preferences.load())
            self.assertEqual(store.room(rid)['status'],'complete',store.messages(rid));self.assertNotIn('deepseek',calls);self.assertIn('claude',calls)
            store.db.close()

class ConfigurableTeamTests(unittest.IsolatedAsyncioTestCase):
    def custom(self,mid='qwen',name='自定义成员'):
        return dict(id=mid,name=name,backend='openai-compatible',model='custom-model',effort='high',base_url='https://example.invalid/v1',no_auth=False)

    async def test_v1_migration_retains_agent_ids_encrypted_keys_and_original_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with patch.object(preferences,'STATE',base),patch.object(preferences,'PREFS',base/'prefs.json'),patch.object(preferences,'PROFILE',base/'profile.toml'):
                old=json.loads(json.dumps(preferences.DEFAULTS));old['members']=[self.custom()]
                old['supervisor']['provider']='qwen';old['member_names']={'old-agent':'历史名字'}
                raw=json.dumps(old,ensure_ascii=False).encode('utf-8');preferences.PREFS.write_bytes(raw)
                preferences.save_member_key('qwen','migration-fixture-private')
                keyfile=base/'credentials/qwen.dpapi';encrypted=keyfile.read_bytes()
                migrated=preferences.save({})
                self.assertEqual(migrated['members'][0]['id'],'qwen')
                self.assertEqual(preferences.get_member_key(migrated['agents'][0]),'migration-fixture-private')
                self.assertEqual(keyfile.read_bytes(),encrypted)
                self.assertEqual((base/'backups/preferences-before-agent-library.json').read_bytes(),raw)
                stored=json.loads(preferences.PREFS.read_text(encoding='utf-8'))
                self.assertEqual(stored['schema_version'],2);self.assertNotIn('members',stored)
                self.assertNotIn('migration-fixture-private',json.dumps(stored))
                preferences.save({'team':[]})
                reloaded=preferences.load()
                self.assertEqual(reloaded['members'],[]);self.assertEqual(len(reloaded['agents']),1)
                self.assertEqual(reloaded['member_names']['old-agent'],'历史名字')
                self.assertTrue((await roster.connections(reloaded))['qwen']['ready'])

    async def test_previously_removed_builtin_returns_to_library_without_enabling_it(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with patch.object(preferences,'STATE',base),patch.object(preferences,'PREFS',base/'prefs.json'),patch.object(preferences,'PROFILE',base/'profile.toml'):
                old=json.loads(json.dumps(preferences.DEFAULTS));old['members']=preferences.default_members(old)[:2]
                old['member_names']={'deepseek':'我的 DeepSeek'}
                preferences.PREFS.write_text(json.dumps(old),encoding='utf-8')
                selected=preferences.save({})
                self.assertEqual([m['id'] for m in selected['members']],['chatgpt','claude'])
                recovered=next(a for a in selected['agents'] if a['id']=='deepseek')
                self.assertEqual(recovered['name'],'我的 DeepSeek')
                self.assertEqual(recovered['base_url'],'https://api.deepseek.com')

    async def test_vendor_grouping_does_not_merge_different_credentials(self):
        a=self.custom('gemini1');a['base_url']='https://generativelanguage.googleapis.com/v1beta/openai'
        b={**a,'id':'gemini2','name':'Gemini 第二个账号'}
        agents=preferences.validate_members([a,b])
        self.assertEqual(agents[0]['group'],'Google / Gemini')
        self.assertEqual(agents[0]['group'],agents[1]['group'])
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(preferences,'STATE',Path(folder)):
                preferences.save_member_key('gemini1','account-one-fixture')
                self.assertEqual(preferences.get_member_key(agents[1]),'')
                self.assertEqual(preferences.get_member_key(agents[0]),'account-one-fixture')

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
                preferences.save({'members':[b]})
                self.assertEqual(preferences.load()['supervisor']['provider'],'qwen')
                self.assertIn('qwen',[a['id'] for a in preferences.load()['agents']])
                with self.assertRaises(ValueError):preferences.save({'supervisor':{'provider':'unsaved'}})
                with self.assertRaises(ValueError):preferences.validate_members([a,a])
                a['base_url']='https://user:secret@example.invalid'
                with self.assertRaises(ValueError):preferences.validate_members([a])

    async def test_generic_api_routes_selected_url_model_and_private_key(self):
        m=self.custom();selected=preferences.load();selected['members']=[m];selected['agents']=[m];selected['supervisor'].update(provider='qwen',model='independent-monitor')
        requests=[]
        def handler(request):
            requests.append(request)
            chunk={'id':'fixture','object':'chat.completion.chunk','created':1,'model':'custom-model','choices':[{'index':0,'delta':{'content':'连接检查成功'},'finish_reason':'stop'}]}
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
        real=engines.AsyncOpenAI
        def client(**kwargs):return real(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder);rid=store.create('generic API',folder)['id']
            with patch('provider_sessions.AsyncOpenAI',side_effect=client),patch.object(preferences,'get_member_key',return_value='private-fixture-key'):
                await roster.reply(store,rid,'qwen','hello',Path(folder),selected,'讨论')
                selected['members']=[]
                with self.assertRaises(ValueError):await roster.reply(store,rid,'qwen','must not dispatch',Path(folder),selected,'讨论')
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
