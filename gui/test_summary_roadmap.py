"""Rollout boundaries, actual delivery receipts, full output capture and inclusive A1 totals."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch,AsyncMock

from test_token_optimization import Fixture
from optimization_settings import optimization_settings,experiment_status
from member_summary import extract,pack_directory,critical_ids
from context import ContextTurn,estimate
from delivery import contains
from budget_control import ContextBudgetExceeded
from output_archive import present,read as read_output
from usage_report import report
from usage import record
from room_settings import RoomSettings


class SummaryFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.settings['optimization']=optimization_settings({'enabled':True,'summary':{'min_chars':100}})
    def authored(self,body=None,provider='claude',stance='有条件'):
        text=(body or ('原文细节保留，不能仅凭摘要批准。'*100))+'\n摘要：立场='+stance+'｜要点=核对原文｜未决=保留关键异议'
        mid=self.store.message(self.rid,provider,text)
        self.store.set_summary(mid,extract(text,self.settings))
        return self.store.message_by_id(mid)


class SettingsTests(SummaryFixture):
    def test_independent_switches_persist_and_active_changes_rotate(self):
        rooms=RoomSettings(self.store);rooms.seed(self.rid,self.settings,legacy=True)
        original=rooms.load(self.rid,self.settings)
        self.assertFalse(original['optimization']['enabled'])
        with patch('room_settings.preferences.load',return_value=self.settings):
            changed=rooms.update(self.rid,{'optimization':self.settings['optimization']})
            disabled=rooms.update(self.rid,{'optimization':{'enabled':False,'summary':{'uses':{'handoff':False}}}})
        self.assertGreater(changed['context_epoch'],original['context_epoch'])
        self.assertGreater(disabled['context_epoch'],changed['context_epoch'])
        self.assertFalse(disabled['optimization']['summary']['uses']['handoff'])
        self.assertTrue(disabled['optimization']['summary']['uses']['catch_up'])
        self.assertEqual(disabled['optimization']['summary']['min_chars'],100)
    def test_research_and_invalid_settings_never_silently_enable(self):
        for item in experiment_status():
            self.assertFalse(item['available']);self.assertFalse(item['enabled'])
            with self.assertRaisesRegex(ValueError,'日后测试'):optimization_settings({'experiments':{item['id']:True}})
        for value in [{'enabled':1},{'summary':{'budget_ratio':float('nan')}},{'summary':{'unknown':True}},
                {'output_preview':{'max_chars':999}},{'summary':{'max_entries':False}}]:
            with self.assertRaises(ValueError):optimization_settings(value)


class SummaryDeliveryTests(SummaryFixture):
    def test_parse_preserves_original_and_vote_authority(self):
        text='细节'*200+'\n摘要：立场=支持｜要点=原文可核对｜未决=无\n表态：反对 P2@1：存在风险'
        mid=self.store.message(self.rid,'claude',text);self.store.set_summary(mid,extract(text,self.settings))
        message=self.store.message_by_id(mid)
        self.assertEqual(message['text'],text);self.assertTrue(message['summary_vote_mismatch'])
        original=next(m for m in self.store.originals(self.rid) if m['id']==mid)
        self.assertEqual(original['hash'],hashlib.sha256(text.encode()).hexdigest())
        with self.assertRaises(ValueError):self.store.update(mid,text+'changed','complete')
    def test_malformed_quoted_fenced_or_overlong_summary_is_ignored(self):
        line='摘要：立场=支持｜要点=正文｜未决=无'
        for text in ['短文',('x'*200)+'\n> '+line,('x'*200)+'\n```\n'+line,
                ('x'*200)+'\n摘要：立场=支持｜要点='+('y'*500)+'｜未决=无',('x'*200)+'\n'+line+'\n其他正文']:
            self.assertIsNone(extract(text,self.settings))
    def test_directory_including_ids_and_continuation_respects_budget(self):
        self.authored();base=self.store.originals(self.rid)[-1]
        messages=[{**base,'id':str(index).zfill(32),'seq':index+1} for index in range(5000)]
        for budget in [0,50,200,800,1500]:
            packed=pack_directory(messages,budget,estimate,7)
            if packed:self.assertLessEqual(estimate(packed),budget)
            self.assertLessEqual(len(packed.get('entries',[])),7)
            self.assertEqual(packed.get('remaining',len(messages))+len(packed.get('entries',[])),len(messages))
    def test_menu_commits_only_after_success_and_does_not_repeat(self):
        msg=self.authored();seq=self.store.originals(self.rid)[-1]['seq'];turn=self.turn('supervisor')
        text=turn.compose('了解历史')
        self.assertIn('作者自述摘要',text);self.assertNotIn(msg['text'],text)
        self.assertFalse(contains(turn.state.get('digested',[]),seq))
        self.assertFalse(contains(turn.state['delivered'],seq))
        turn.complete('done')
        self.assertTrue(contains(turn.state['digested'],seq));self.assertFalse(contains(turn.state['delivered'],seq))
        turn.state['native']={'thread_id':'fixture'};turn.save()
        next_turn=self.turn('supervisor');again=next_turn.compose('继续')
        self.assertNotIn(msg['author_summary']['text'],again)
        self.assertNotIn(msg['id'],again)
    def test_chunk_reads_leave_gap_until_the_full_original_is_covered(self):
        msg=self.authored('长原文'*5000);seq=self.store.originals(self.rid)[-1]['seq'];turn=self.turn('supervisor')
        turn.compose('了解历史');turn.complete('done')
        first=turn.read('group_get_message',{'message_id':msg['id']})
        self.assertIsNotNone(first['next_offset']);self.assertFalse(contains(turn.state['delivered'],seq))
        offset=first['next_offset']
        while offset is not None:offset=turn.read('group_get_message',{'message_id':msg['id'],'offset':offset})['next_offset']
        self.assertTrue(contains(turn.state['delivered'],seq));self.assertFalse(contains(turn.state['digested'],seq))
    def test_directory_tool_does_not_create_zero_length_full_receipts(self):
        msg=self.authored();seq=self.store.originals(self.rid)[-1]['seq'];turn=self.turn('supervisor')
        turn.compose('list');result=turn.read('group_list_messages',{})
        self.assertIn(msg['id'],json.dumps(result));self.assertFalse(contains(turn.state['delivered'],seq))
        self.assertTrue(contains(turn.state['digested'],seq))
    def test_old_dissent_and_current_plan_are_pinned_as_full_originals(self):
        objection=self.authored('不能删除用户文件。'*100,stance='反对');plan=self.plan()
        self.wf['votes']={'P2@0':{'claude':{'agree':False,'message_id':objection['id'],'reason':'不可丢失'}}}
        self.store.save_workflow(self.rid,self.wf,0)
        turn=self.turn('supervisor');text=turn.compose('决策')
        originals=[m['text'] for m in json.loads(text.splitlines()[1])['messages']]
        self.assertIn(objection['text'],originals);self.assertIn(plan['text'],originals)
        self.assertIn(objection['id'],critical_ids(self.wf))
    def test_oversize_mandatory_original_pauses_instead_of_summarizing(self):
        self.store.message(self.rid,'you','必要约束'*20000)
        self.settings['context']={'soft_tokens':6000,'output_tokens':1024,'margin_tokens':512,'overflow':'pause'}
        with self.assertRaises(ContextBudgetExceeded):self.turn('supervisor').compose('决策')
    def test_handoff_records_only_summaries_that_are_actually_present(self):
        self.authored();self.settings['context']={'handoff_recent':0}
        turn=self.turn('supervisor');turn.rotate('fixture')
        text=turn.compose('handoff');count=text.count('摘要：立场=')
        self.assertEqual(count,1)
        turn.complete('done');self.assertEqual(sum(hi-lo+1 for lo,hi in turn.state['digested']),1)
    def test_supervisor_history_switch_and_full_mode_do_not_use_catch_up_as_a_backdoor(self):
        message=self.authored()
        for mode,enabled in [('full',True),('state',False)]:
            self.settings['supervisor_context']=mode
            self.settings['optimization']['summary']['uses']['supervisor_history']=enabled
            turn=self.turn('supervisor',branch=mode+str(enabled));payload=turn.compose('inspect')
            data=json.loads(payload.splitlines()[1])
            originals=data['messages']+turn.state.get('handoff_package',{}).get('recent_originals',[])
            self.assertIn(message['text'],[m['text'] for m in originals])
            self.assertEqual(turn.menu_entries,[])
    def test_executor_keeps_group_and_project_tools_absent(self):
        turn=self.turn('deepseek');text=turn.compose('唯一契约')
        self.assertEqual([t['name'] for t in turn.schemas()],['output_read'])
        self.assertNotIn('自述摘要',text)
        self.assertEqual(self.store.deliveries(self.rid)[0]['mode'],'仅接收监工执行契约')
        with self.assertRaises(ValueError):turn.read('group_read_messages',{})
    def test_disabled_controls_keep_the_deterministic_legacy_payload(self):
        self.settings['optimization']=optimization_settings();turn=self.turn('claude');baseline=turn.compose('固定任务')
        state=copy.deepcopy(turn.state);state.pop('dirty',None)
        self.store.save_session(turn.key,self.rid,turn.identity,turn.role,turn.model,state)
        self.settings['optimization']=optimization_settings({'enabled':False,'brevity':True,'summary':{'uses':{'handoff':False}}})
        self.assertEqual(self.turn('claude').compose('固定任务'),baseline)


class OutputAndProjectTests(SummaryFixture):
    def test_full_archive_and_bounded_preview_keep_tail_and_real_exit_code(self):
        text='first\n'+('x'*180000)+'\nLAST-ERROR'
        preview,reference=present(self.store,self.rid,'host','tests',7,text,self.settings,kind='acceptance')
        self.assertLessEqual(len(preview),6000);self.assertIn('LAST-ERROR',preview);self.assertIn(reference['id'],preview)
        turn=self.turn('deepseek');turn.compose('契约');offset=0;restored=''
        while offset is not None:
            part=turn.read('output_read',{'id':reference['id'],'offset':offset});restored+=part['text'];offset=part['next_offset']
        self.assertEqual(restored,text);self.assertEqual(part['exit_code'],7);self.assertTrue(part['capture_complete'])
    def test_archive_access_is_room_and_executor_scoped(self):
        turn=self.turn('deepseek');turn.compose('契约')
        _,other=present(self.store,self.rid,'another-session','cmd',0,'private',self.settings)
        with self.assertRaisesRegex(ValueError,'执行者'):turn.read('output_read',{'id':other['id']})
        second=self.store.create('other',str(self.root))['id']
        _,foreign=present(self.store,second,'host','cmd',0,'other-room',self.settings)
        with self.assertRaises(ValueError):turn.read('output_read',{'id':foreign['id']})
        with self.assertRaises(ValueError):turn.read('output_read',{'id':other['id'],'limit':999999})
    def test_archived_output_remains_readable_after_disabling_and_rotating(self):
        original=self.turn('deepseek');original.compose('first contract');original.complete('finished')
        _,reference=present(self.store,self.rid,original.key,'own command',0,'OLD-EXECUTION-OUTPUT',self.settings)
        self.settings['optimization']['enabled']=False;self.settings['context_epoch']=1
        renewed=self.turn('deepseek');renewed.compose('new contract')
        self.assertNotEqual(original.key,renewed.key)
        self.assertEqual([t['name'] for t in renewed.schemas()],['output_read'])
        self.assertEqual(renewed.read('output_read',{'id':reference['id']})['text'],'OLD-EXECUTION-OUTPUT')
    def test_executor_cannot_read_its_former_discussion_outputs_after_role_change(self):
        discussion=self.turn('claude');discussion.compose('discussion');discussion.complete('finished')
        _,reference=present(self.store,self.rid,discussion.key,'discussion command',0,'DISCUSSION-ONLY',self.settings)
        self.settings['optimization']['enabled']=False;self.settings['executor']='claude';self.settings['context_epoch']=1
        executor=self.turn('claude');executor.compose('execution contract')
        self.assertEqual(executor.schemas(),[])
        with self.assertRaises(ValueError):executor.read('output_read',{'id':reference['id']})
        with self.assertRaises(ValueError):read_output(self.store.db,self.store.lock,self.rid,executor.key,executor.state['branch'],{'id':reference['id']})
    def test_project_tools_return_bounded_text_and_exclude_secrets_and_escape(self):
        (self.root/'app.py').write_text('TOKEN_MARKER\nprint(1)\n',encoding='utf-8')
        (self.root/'.env').write_text('TOKEN_MARKER=PRIVATE',encoding='utf-8')
        turn=self.turn('claude');turn.compose('inspect')
        found=turn.read('project_search',{'query':'TOKEN_MARKER'})
        self.assertEqual([m['path'] for m in found['matches']],['app.py'])
        part=turn.read('project_read',{'path':'app.py','limit':1})
        self.assertEqual(part['text'],'TOKEN_MARKER');self.assertEqual(part['next_offset'],1)
        for path in ['../outside','.env','.git/config']:
            with self.assertRaises(ValueError):turn.read('project_read',{'path':path})
    def test_metadata_counts_are_not_added_to_token_estimate(self):
        turn=self.turn('claude');turn.compose('record')
        turn.composition={'instructions':10,'originals':20,'observations':{'feed_counts':{'menu':1000}},'feed_menu':1000}
        record(turn,'openai_compatible',{'prompt_tokens':35},request_ref='accounting')
        measured=json.loads(self.store.usage(self.rid)[-1]['composition'])
        self.assertEqual(measured['host_estimate'],30);self.assertEqual(measured['unattributed'],5)
    def test_report_cache_uses_inclusive_input_and_preserves_unknowns(self):
        turn=self.turn('claude');turn.compose('record')
        record(turn,'claude',{'input_tokens':10,'cache_read_input_tokens':80,'cache_creation_input_tokens':10,'output_tokens':5},request_ref='first')
        record(turn,'openai_compatible',{},request_ref='unknown')
        result=report(self.store.usage(self.rid),'role')['groups'][0]
        self.assertEqual(result['tokens']['input']['known_sum'],100);self.assertEqual(result['tokens']['input']['unknown_requests'],1)
        self.assertEqual(result['cache_hit_rate'],.8);self.assertEqual(result['cache_measured_requests'],1)
    def test_day_reports_use_london_date_including_summer_time(self):
        row={'identity':'a','role':'discussion','session_key':'s','branch':'b','adapter':'fixture',
            'created':'2026-10-22T23:30:00+00:00','input':100,'cached_read':80,'cache_write':0,'output':10,'reasoning':None,'composition':{}}
        self.assertEqual(report([row],'day')['groups'][0]['day'],'2026-10-23')
    def test_cli_report_is_read_only_and_does_not_interrupt_live_room(self):
        self.store.status(self.rid,'running')
        database=self.store.db.execute('PRAGMA database_list').fetchone()[2]
        result=subprocess.run([sys.executable,str(Path(__file__).parents[1]/'scripts/usage_report.py'),'--database',database,'--room',self.rid,'--json'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(self.store.room(self.rid)['status'],'running')
        self.assertIn('unverified',json.loads(result.stdout)['accounting']['net_savings'])


class ActualOutputBridgeTests(unittest.TestCase):
    from test_git_bridge import ExecutorBridgeTests as _Bridge
    call=_Bridge.call
    def setUp(self):
        self._Bridge.setUp(self)
        from store import Store
        self.store=Store(Path(self.temp.name)/'archives');self.rid=self.store.create('bridge',str(self.root))['id']
        self.store.save_session('executor',self.rid,'deepseek','executor','fixture',{'branch':'b','tool_names':['output_read']})
        self.output_env=patch.dict(os.environ,{'ROOM_DATABASE':self.store.db.execute('PRAGMA database_list').fetchone()[2],
            'ROOM_ID':self.rid,'ROOM_SESSION':'executor','ROOM_OUTPUT_PREVIEW':'6000'})
        self.output_env.start()
        (self.root/'a.txt').write_text(('CHANGED-LINE\n'*9000)+'TAIL-ERROR-SENTINEL\n',encoding='utf-8')
    def tearDown(self):
        self.output_env.stop();self.store.db.close();self._Bridge.tearDown(self)
    def test_actual_bash_child_archives_before_upstream_50000_character_truncation(self):
        result=self.call(self.root,'Bash',{'command':'git diff -- a.txt','timeout':60})
        self.assertLessEqual(len(result),6000);self.assertIn('TAIL-ERROR-SENTINEL',result)
        archive=self.store.db.execute('SELECT * FROM output_archives WHERE room=?',(self.rid,)).fetchone()
        self.assertIsNotNone(archive,result);self.assertGreater(len(archive['text']),50000);self.assertTrue(archive['complete'])
        self.assertIn('TAIL-ERROR-SENTINEL',archive['text'])
    def test_actual_git_bridge_archives_before_40000_character_truncation(self):
        result=self.call(self.root,'Git',{'args':['diff','--','a.txt']})
        self.assertLessEqual(len(result),6000);self.assertIn('TAIL-ERROR-SENTINEL',result)
        self.assertGreater(len(self.store.db.execute('SELECT text FROM output_archives').fetchone()[0]),40000)


class ActualAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_changes_apply_to_the_next_call_without_cancelling_the_current_turn(self):
        import roster
        fixture=SummaryFixture();fixture.setUp()
        try:
            rooms=RoomSettings(fixture.store);rooms.seed(fixture.rid,fixture.settings,legacy=True)
            with patch('room_settings.preferences.load',return_value=fixture.settings):
                active_settings=rooms.update(fixture.rid,{'optimization':fixture.settings['optimization']})
                current=fixture.turn('claude')
                rooms.update(fixture.rid,{'optimization':{'enabled':False}})
            captured=[]
            async def replied(turn,*a,**k):captured.append(turn);return 'fixture'
            with patch('provider_sessions.reply',new=replied):
                await roster.reply(fixture.store,fixture.rid,'claude','next',fixture.root,active_settings,'fixture')
            self.assertTrue(current.settings['optimization']['enabled'])
            self.assertFalse(captured[0].settings['optimization']['enabled'])
            self.assertNotEqual(current.key,captured[0].key)
        finally:fixture.tearDown()

    async def test_actual_mcp_exposes_only_the_executor_output_tool_and_discussion_read_tools(self):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        fixture=SummaryFixture();fixture.setUp()
        try:
            (fixture.root/'demo.py').write_text('MCP-PROJECT-DATA\n',encoding='utf-8')
            for identity in ['claude','deepseek']:
                turn=fixture.turn(identity);turn.compose('fixture')
                params=StdioServerParameters(command=sys.executable,args=[str(Path(__file__).with_name('room_tools.py'))],env={
                    'ROOM_DATABASE':fixture.store.db.execute('PRAGMA database_list').fetchone()[2],'ROOM_ID':fixture.rid,'ROOM_SESSION':turn.key})
                async with stdio_client(params) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize();names={t.name for t in (await session.list_tools()).tools}
                        if identity=='deepseek':self.assertEqual(names,{'output_read'})
                        else:
                            self.assertTrue({'project_read','project_search','group_list_messages','output_read'}<=names)
                            response=await session.call_tool('project_read',{'path':'demo.py'})
                            self.assertFalse(response.isError);self.assertIn('MCP-PROJECT-DATA',response.content[0].text)
        finally:fixture.tearDown()

    async def test_host_captures_tail_after_30000_bytes_and_keeps_nonzero_exit(self):
        fixture=SummaryFixture();fixture.setUp()
        try:
            from workflows import verify_commands
            command='"'+sys.executable+'" -c "print(\'X\'*50000); print(\'LAST-ERROR\'); raise SystemExit(3)"'
            result=await verify_commands(fixture.store,fixture.rid,fixture.root,[command],fixture.settings)
            self.assertEqual(result[0]['exit_code'],3);self.assertIn('LAST-ERROR',result[0]['output'])
            archived=fixture.store.db.execute('SELECT text FROM output_archives WHERE id=?',(result[0]['output_id'],)).fetchone()[0]
            self.assertGreater(len(archived),50000);self.assertIn('LAST-ERROR',archived)
        finally:fixture.tearDown()

    async def test_actual_api_tool_loop_and_published_author_summary(self):
        import httpx
        import provider_sessions
        from engines import preferences
        fixture=SummaryFixture();fixture.setUp()
        try:
            fixture.settings['executor']=None
            (fixture.root/'checked.py').write_text('WIRE-PROJECT-CONTENT\n',encoding='utf-8')
            turn=fixture.turn('deepseek');calls=[]
            answer='根据实际读取的内容核对。'*70+'\n摘要：立场=中立｜要点=已核对实际文件｜未决=无'
            def respond(request):
                calls.append(json.loads(request.content))
                delta={'role':'assistant','tool_calls':[{'index':0,'id':'read-1','type':'function','function':{
                    'name':'project_read','arguments':json.dumps({'path':'checked.py'})}}]} if len(calls)==1 else {'role':'assistant','content':answer}
                chunk={'id':'wire','object':'chat.completion.chunk','created':0,'model':turn.model,'choices':[{'index':0,'delta':delta,
                    'finish_reason':'tool_calls' if len(calls)==1 else 'stop'}],
                    'usage':{'prompt_tokens':1000,'completion_tokens':50,'total_tokens':1050}}
                return httpx.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n')
            real=provider_sessions.AsyncOpenAI
            def client(**kwargs):return real(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
            with patch('model_catalog.effective_effort',new=AsyncMock(return_value=None)),patch.object(preferences,'get_member_key',return_value='fixture'),patch.object(provider_sessions,'AsyncOpenAI',side_effect=client):
                result=await provider_sessions.reply(turn,'读取 checked.py',fixture.root,None,turn.source)
            self.assertEqual(result,answer);self.assertEqual(len(calls),2)
            self.assertIn('WIRE-PROJECT-CONTENT',calls[1]['messages'][-1]['content'])
            message=fixture.store.messages(fixture.rid)[-1]
            self.assertEqual(message['author_summary']['source'],'self');self.assertEqual(message['text'],answer)
            self.assertEqual(len(fixture.store.usage(fixture.rid)),2)
            prefix=json.loads(fixture.store.usage(fixture.rid)[-1]['composition'])['observations']['prefix']
            self.assertFalse(prefix['changed']);self.assertGreater(prefix['common_messages'],0)
        finally:fixture.tearDown()
