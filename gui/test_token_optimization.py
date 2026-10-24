"""Fault replay, approval boundaries, delivery gaps, request accounting and handoff."""
import asyncio
from contextlib import asynccontextmanager
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch,AsyncMock

from engines import preferences
from store import Store,WorkflowConflict
from context import ContextTurn
from context_settings import context_settings
from coordination import Coordinator
from workflows import Workflows
from workflow_state import new_state,adopt,accept_user_approval,approvals_cover,refresh,current_refs,record_vote,ledger
from supervision import validate_control,supervisor_payload
from preflight import check,fingerprint
from usage import normalize,record
from calibration import estimate_v2,state as calibration_state
from delivery import contains
import provider_sessions


def control(action,rules=None,patches=None):
    value={'public_message':'公开安排与证据','control':{'action':action,'checks_patch':patches or []}}
    if rules is not None:value['control']['rules']=rules
    return value


RULES=[{'id':'R1','text':'保留真实证据','criterion':'核对宿主证据'}]


class UsageTests(unittest.TestCase):
    def test_codex_total_deltas_and_reset_do_not_sum_the_cumulative_counter(self):
        a,total=normalize('codex',{'total':{'inputTokens':100,'cachedInputTokens':20,'outputTokens':15}})
        b,total=normalize('codex',{'total':{'inputTokens':260,'cachedInputTokens':60,'outputTokens':25}},total)
        self.assertEqual(a['input']+b['input'],260);self.assertEqual(b['cached_read'],40)
        c,_=normalize('codex',{'total':{'inputTokens':12,'cachedInputTokens':0,'outputTokens':2}},total)
        self.assertEqual(c['input'],12);self.assertEqual(c['cached_read'],0);self.assertIsNone(c['cache_write'])

    def test_claude_cache_counts_and_missing_fields_stay_unknown(self):
        u,_=normalize('claude',dict(input_tokens=20,cache_read_input_tokens=80,cache_creation_input_tokens=10,output_tokens=9))
        self.assertEqual(u['input'],110);self.assertEqual(u['cached_read'],80);self.assertIsNone(u['reasoning'])
        self.assertIsNone(normalize('claude',dict(input_tokens=20))[0]['input'])
        self.assertTrue(all(v is None for v in normalize('openai_compatible',{})[0].values()))

    def test_gemini_raw_cache_and_reasoning_are_not_dropped(self):
        u,_=normalize('gemini',dict(total_input_tokens=100,total_cached_tokens=70,total_output_tokens=30,total_thought_tokens=12))
        self.assertEqual((u['input'],u['output'],u['cached_read'],u['reasoning']),(100,42,70,12))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.root=self.base/'project';self.root.mkdir()
        subprocess.run(['git','init',str(self.root)],capture_output=True,check=True)
        self.store=Store(self.base/'state');self.rid=self.store.create('Optimization',str(self.root))['id']
        self.settings=preferences.load();self.settings['executor']='deepseek';self.settings['resume_words']=['继续','已修复']
        self.policy=self.store.policy(self.rid,'use evidence')
        mid=self.store.message(self.rid,'you','编写并验证程序')
        self.wf=new_state('编写并验证程序',mid,self.policy)
        self.patch=patch('model_catalog.connection_context',return_value=('fixture-connection','fixture-key'));self.patch.start()
    def tearDown(self):self.patch.stop();self.store.db.close();self.temp.cleanup()
    def turn(self,identity='claude',model=None,**kwargs):
        source=next(a for a in self.settings['agents'] if a['id']==('chatgpt' if identity=='supervisor' else identity))
        return ContextTurn(self.store,self.rid,identity,source,model or source['model'],self.settings,'fixture',**kwargs)
    def plan(self):
        mid=self.store.message(self.rid,'claude','方案原文 UNIQUE-PLAN-BODY')
        return adopt(self.wf,self.store,self.rid,{'plan_id':'P2','message_id':mid},{'claude'})
    def approved(self):
        plan=self.plan();self.wf['awaiting_approval']=plan['ref']
        mid=self.store.message(self.rid,'you','同意')
        aid=accept_user_approval(self.wf,self.store,self.rid,mid)
        return plan,aid


class StateTests(Fixture):
    def test_legacy_ids_are_migrated_without_reusing_old_numbers_or_copying_reply_bodies(self):
        a=self.store.message(self.rid,'claude','old one');b=self.store.message(self.rid,'claude','old two')
        for mid in [a,b]:self.store.event(self.rid,{'type':'evidence','record':{'id':'E07','agent':'claude','kind':'ask','status':'complete','message_id':mid,'reply':'do not copy body'}})
        records,next_id=self.store.legacy_evidence(self.rid)
        self.assertEqual([r['id'] for r in records],['E08','E09']);self.assertEqual(next_id,10)
        self.assertTrue(all('reply' not in r and r['legacy_id']=='E07' for r in records))

    def test_optimistic_lock_and_restart_keep_state_and_evidence_counter(self):
        self.wf['next_evidence']=8
        rev=self.store.save_workflow(self.rid,self.wf,0)
        with self.assertRaises(WorkflowConflict):self.store.save_workflow(self.rid,{},0)
        with self.assertRaises(WorkflowConflict):self.store.save_workflow(self.rid,{},rev+1)
        self.assertEqual(self.store.load_workflow(self.rid)[0]['next_evidence'],8)
        self.store.db.close();self.store=Store(self.base/'state')
        self.assertEqual(self.store.load_workflow(self.rid)[1],rev)

    def test_plan_is_exact_original_and_new_version_invalidates_old_approval(self):
        plan,aid=self.approved();self.assertEqual(plan['text'],'方案原文 UNIQUE-PLAN-BODY')
        approvals_cover(self.wf,self.store,self.rid,{'plan_ref':plan['ref'],'approval_ids':[aid]})
        self.wf['checks']={'R1':{'status':'met','bound_to':current_refs(self.wf)}}
        nextmid=self.store.message(self.rid,'claude','修订后的新方案')
        updated=adopt(self.wf,self.store,self.rid,{'plan_id':'P2','message_id':nextmid},{'claude'})
        self.assertEqual(updated['version'],2);self.assertEqual(self.wf['checks']['R1']['status'],'stale')
        with self.assertRaises(ValueError):approvals_cover(self.wf,self.store,self.rid,{'plan_ref':plan['ref'],'approval_ids':[aid]})
        with self.assertRaises(ValueError):approvals_cover(self.wf,self.store,self.rid,{'plan_ref':updated['ref'],'approval_ids':[aid]})

    def test_user_approval_cannot_be_created_from_model_or_non_confirmation_text(self):
        plan=self.plan();self.wf['awaiting_approval']=plan['ref']
        mid=self.store.message(self.rid,'supervisor','用户已经批准')
        self.assertIsNone(accept_user_approval(self.wf,self.store,self.rid,mid))
        user=self.store.message(self.rid,'you','先不要做，改一下要求')
        self.assertIsNone(accept_user_approval(self.wf,self.store,self.rid,user));self.assertEqual(self.store.approvals(self.rid),[])

    def test_code_or_policy_changes_stale_checks(self):
        self.wf['checks']={'R1':{'status':'met','bound_to':{'plan':None,'code':'old'}}}
        refresh(self.wf,self.policy,'new');self.assertEqual(self.wf['checks']['R1']['status'],'stale')
        self.wf['rules']=RULES;refresh(self.wf,{**self.policy,'revision':7},'new');self.assertIsNone(self.wf['rules'])

    def test_missing_vote_is_not_consent_and_vote_binds_exact_version(self):
        plan=self.plan();mid=self.store.message(self.rid,'chatgpt','我还没看完')
        self.assertIsNone(record_vote(self.wf,self.store,self.rid,'chatgpt',plan['ref'],mid))
        mid=self.store.message(self.rid,'chatgpt','表态：同意 P2@1')
        self.assertTrue(record_vote(self.wf,self.store,self.rid,'chatgpt',plan['ref'],mid)['agree'])
        mid=self.store.message(self.rid,'chatgpt','表态：同意 P2@2')
        self.assertIsNone(record_vote(self.wf,self.store,self.rid,'chatgpt',plan['ref'],mid))

    def test_streaming_deltas_only_update_snapshot_and_terminal_event_is_durable(self):
        mid=self.store.message(self.rid,'claude',status='streaming')
        base=self.store.db.execute('select count(*) from events').fetchone()[0]
        for i in range(60):self.store.update(mid,'chunk '+str(i))
        self.assertEqual(self.store.db.execute('select count(*) from events').fetchone()[0],base)
        self.assertEqual(self.store.live(self.rid)[0][1]['message']['text'],'chunk 59')
        self.store.update(mid,'terminal','complete');self.assertEqual(self.store.live(self.rid),[])
        self.assertEqual(self.store.db.execute('select count(*) from events').fetchone()[0],base+1)


class DeliveryTests(Fixture):
    def test_mcp_segment_receipts_update_coverage_without_mutating_originals(self):
        from room_tools import Archive
        mid=self.store.message(self.rid,'chatgpt','0123456789'*2500)
        turn=self.turn();turn.compose('read');before=self.store.message_by_id(mid)['text']
        with patch.dict(os.environ,{'ROOM_DATABASE':str(self.base/'state/rooms.sqlite3')}):archive=Archive()
        try:
            offset=0
            while True:
                result=archive.read(turn.key,self.rid,'group_get_message',{'message_id':mid,'offset':offset})
                offset=result['next_offset']
                if offset is None:break
            turn.import_receipts();seq=result['seq'];self.assertTrue(contains(turn.state['delivered'],seq))
            self.assertEqual(self.store.message_by_id(mid)['text'],before)
            isolated=self.turn('deepseek');isolated.compose('contract')
            with self.assertRaises(ValueError):archive.read(isolated.key,self.rid,'group_get_message',{'message_id':mid})
        finally:archive.db.close();archive.receipts.close()

    def test_maintenance_threshold_rotates_without_slicing_signed_history(self):
        turn=self.turn('deepseek');turn.state['last_request_input']=80000
        turn.state['native']={'messages':[{'role':'assistant','content':'own','signature':'OPAQUE'}]};branch=turn.state['branch']
        turn.compose('next contract');self.assertNotEqual(turn.state['branch'],branch)
        self.assertEqual(turn.state['native'],{});self.assertNotIn('OPAQUE',turn.input)

    def test_gap_survives_complete_and_partial_reads_do_not_claim_full_delivery(self):
        mid=self.store.message(self.rid,'chatgpt','large-'+'中文'*60000)
        self.store.message(self.rid,'chatgpt','small')
        turn=self.turn();turn.compose('read');self.assertTrue(any(m['id']==mid for m in turn.omitted))
        turn.complete('answer');seq=next(m['seq'] for m in self.store.originals(self.rid) if m['id']==mid)
        self.assertFalse(contains(turn.state['delivered'],seq));self.assertTrue(contains(turn.state['gaps'],seq))
        first=turn.read('group_get_message',{'message_id':mid});self.assertFalse(contains(turn.state['delivered'],seq))
        offset=first['next_offset']
        while offset is not None:offset=turn.read('group_get_message',{'message_id':mid,'offset':offset})['next_offset']
        self.assertTrue(contains(turn.state['delivered'],seq));self.assertFalse(contains(turn.state['gaps'],seq))

    def test_changed_model_uses_public_handoff_and_keeps_opaque_history_private(self):
        self.plan();self.store.save_workflow(self.rid,self.wf,0)
        old=self.turn();old.compose('first');old.state['native']={'session_id':'PRIVATE-NATIVE','signature':'PRIVATE-SIGNATURE'};old.complete('公开的本人答复')
        for i in range(14):self.store.message(self.rid,'chatgpt','peer-'+str(i))
        self.settings['context']={'handoff_recent':2}
        new=self.turn(model='another-model')
        self.assertIn('UNIQUE-PLAN-BODY',new.state['handoff_text']);self.assertIn('公开的本人答复',new.state['handoff_text'])
        self.assertNotIn('PRIVATE',new.state['handoff_text']);self.assertEqual(len(new.state['handoff_included']),2)
        self.assertTrue(new.state['gaps']);self.assertEqual(new.state['native'],{})

    def test_executor_handoff_excludes_all_group_originals(self):
        self.store.message(self.rid,'claude','FOREIGN-GROUP-SECRET')
        old=self.turn('deepseek');old.compose('contract');old.complete('my execution report')
        new=self.turn('deepseek',model='new-model');value=new.compose('contract next')
        self.assertNotIn('FOREIGN-GROUP-SECRET',value);self.assertFalse(new.tools);self.assertEqual(new.state['gaps'],[])

    def test_shadow_calibration_requires_twenty_consecutive_accurate_samples(self):
        turn=self.turn('deepseek');turn.compose('test');turn.last_full_estimate=100
        for i in range(19):record(turn,'openai_compatible',{'prompt_tokens':100,'completion_tokens':1},request_ref=str(i))
        self.assertEqual(calibration_state(turn)['streak'],19)
        record(turn,'openai_compatible',{'prompt_tokens':100,'completion_tokens':1},request_ref='19')
        self.assertEqual(calibration_state(turn)['streak'],20)
        # Replay of the same request cannot manufacture calibration samples.
        record(turn,'openai_compatible',{'prompt_tokens':100,'completion_tokens':1},request_ref='19')
        self.assertEqual(calibration_state(turn)['samples'],20)
        self.assertEqual(estimate_v2('汉字'),2)


class ProtocolTests(Fixture):
    def test_rules_only_first_turn_and_finish_rejects_stale_evidence(self):
        first=validate_control(control({'type':'wait'},RULES),self.wf,{'claude'})
        self.wf['rules']=first['rules']
        with self.assertRaises(ValueError):validate_control(control({'type':'wait'},RULES),self.wf,{'claude'})
        self.wf['code_hash']='now';self.wf['evidence']=[{'id':'E01','kind':'execute','status':'complete','bound_to':{'plan':None,'code':'before'}}]
        full={'rule_id':'R1','status':'met','evidence_ids':['E01'],'note':'done','bound_to':current_refs(self.wf)}
        with self.assertRaises(ValueError):validate_control(control({'type':'finish','full_checks':[full]}),self.wf,{'claude'})
        self.wf['evidence'][0]['bound_to']=current_refs(self.wf)
        validate_control(control({'type':'finish','full_checks':[full]}),self.wf,{'claude'})

    def test_ledger_is_full_on_new_branch_then_patch_and_evidence_directory_is_bounded(self):
        turn=self.turn('supervisor')
        first=json.loads(supervisor_payload(turn,self.wf,{},'project',{},None));self.assertIn('ledger',first)
        turn.compose('first');turn.complete('answer')
        self.wf['step']=1
        second=json.loads(supervisor_payload(turn,self.wf,{},'project',{},None));self.assertNotIn('ledger',second);self.assertNotIn('project',second)
        self.assertEqual(second['ledger_patch']['set']['step'],1)
        self.wf['evidence']=[{'id':f'E{i:02d}','kind':'ask'} for i in range(300)]
        view=ledger(self.wf,self.settings);self.assertEqual(len(view['evidence']),8);self.assertEqual(view['evidence_directory']['older_count'],292)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=Fixture();self.fixture.setUp();self.__dict__.update({k:v for k,v in self.fixture.__dict__.items() if k in ['root','base','store','rid','settings','wf','policy']})
    async def asyncTearDown(self):self.fixture.tearDown()

    def coordinator(self):
        work=Workflows(self.store);c=Coordinator(work,self.rid,self.root,self.settings);c.state=self.wf;c.save();return c

    async def test_missing_repository_is_initialized_by_host_and_original_approval_resumes(self):
        plan,aid=self.fixture.approved();self.wf=self.fixture.wf
        self.root=self.base/'new-project';self.root.mkdir()
        c=self.coordinator();await c.bindings()
        task={'goal':'write one file','allowed_paths':['out.txt'],'acceptance_commands':[]}
        action=c.prepare({'type':'execute','agents':['deepseek'],'task':task,'plan_ref':plan['ref'],'approval_ids':[aid]})
        c.host_error([check('git_host',False,'not a git repository','git_host')],action)
        c.work.execute_supervised=AsyncMock(return_value={'kind':'execute','agent':'deepseek','status':'complete','tests_run':False})
        c.work.reply=AsyncMock(return_value=json.dumps(control({'type':'wait'},RULES)))
        c.work.quota=AsyncMock(return_value=None)
        with patch('preflight.preferences.STATE',self.base/'host-state'),patch('preflight.executor_checks',new=AsyncMock(return_value=[check('executor_start',True,'ok','mcp')])):
            await c.work.supervised(self.rid,'继续',self.root,self.settings)
        self.assertTrue((self.root/'.git').is_dir())
        c.work.execute_supervised.assert_awaited_once()
        self.assertEqual(c.work.execute_supervised.await_args.args[2],action['task'])
        state,_=self.store.load_workflow(self.rid)
        self.assertIsNone(state['pending_action']);self.assertIsNone(state['blocker'])
        messages=self.store.messages(self.rid)
        self.assertEqual(sum(m['phase']=='项目环境准备' for m in messages),1)
        self.assertEqual(c.work.reply.await_count,1)  # Result check only, no replanning or reapproval.
        self.assertEqual(state['current_plan'],plan['ref'])

    async def test_repeated_environment_fault_uses_zero_models_and_resume_keeps_contract(self):
        plan,aid=self.fixture.approved();self.wf=self.fixture.wf
        c=self.coordinator();await c.bindings()
        task={'goal':'write one file','allowed_paths':['out.txt'],'acceptance_commands':[]}
        action=c.prepare({'type':'execute','agents':['deepseek'],'task':task,'plan_ref':plan['ref'],'approval_ids':[aid]})
        failed=[check('executor_start',False,'unsafe owner or DACL','mcp')];c.host_error(failed,action)
        c.work.reply=AsyncMock(side_effect=AssertionError('must not call a model'))
        for text in ['继续','已修复','继续']:
            self.store.message(self.rid,'you',text)
            with patch('coordination.preflight',new=AsyncMock(return_value=failed)):await c.work.supervised(self.rid,text,self.root,self.settings)
        c.work.reply.assert_not_awaited()
        persisted,_=self.store.load_workflow(self.rid);self.assertEqual(persisted['pending_action']['contract_hash'],action['contract_hash'])
        calls=[]
        async def executed(rid,agent,task,root,settings):
            calls.append(task);return {'kind':'execute','agent':agent,'status':'complete','tests_run':False}
        c.work.execute_supervised=executed
        c.work.reply=AsyncMock(return_value=json.dumps(control({'type':'wait'},RULES)))
        self.store.message(self.rid,'you','继续')
        with patch('coordination.preflight',new=AsyncMock(return_value=[check('executor_start',True,'ok','mcp')])):
            await c.work.supervised(self.rid,'继续',self.root,self.settings)
        self.assertEqual(calls,[action['task']]);self.assertEqual(c.work.reply.await_count,1)
        state,_=self.store.load_workflow(self.rid);self.assertEqual(state['evidence'][-1]['id'],'E01')
        self.assertIsNone(state['pending_action'])

    async def test_missing_vote_is_reasked_once_and_tolerance_is_enforced_by_host(self):
        self.fixture.plan();self.wf=self.fixture.wf;c=self.coordinator();self.settings['discussion_tolerance']=1
        replies=['需要再想想','还是没有格式表态'];calls=[]
        async def reply(rid,agent,prompt,root,settings,phase,**kwargs):
            text=replies.pop(0);calls.append(phase);self.store.message(rid,agent,text);return text
        c.work.reply=reply
        action={'type':'ask','kind':'vote','agents':['chatgpt'],'plan_ref':'P2@1','instruction':'表态'}
        await c.ask(action,self.policy);self.assertEqual(len(calls),2)
        self.assertIsNone(c.state['votes']['P2@1']['chatgpt']['agree'])
        with self.assertRaises(ValueError):await c.ask(action,self.policy)
        self.assertEqual(len(calls),2)

    async def test_monitor_raw_control_is_archived_but_not_added_to_group_originals(self):
        turn=self.fixture.turn('supervisor');text=json.dumps(control({'type':'wait'},RULES))
        with patch('provider_sessions.codex_conversation',new=AsyncMock(return_value=(text,{}))),patch('model_catalog.effective_effort',new=AsyncMock(return_value=None)):
            await provider_sessions.reply(turn,'decide',self.root,None,turn.source,publish=False)
        self.assertFalse(any(m['provider']=='supervisor' for m in self.store.originals(self.rid)))
        self.assertEqual(self.store.db.execute('select text from raw_responses').fetchone()[0],text)
        self.assertEqual(turn.state['last_answer'],'公开安排与证据')

    async def test_codex_usage_notifications_refine_one_request_without_double_counting(self):
        from types import SimpleNamespace
        turn=self.fixture.turn('chatgpt');turn.compose('measure');events=[]
        for input_,cached,out in [(100,50,10),(100,50,15),(300,100,35)]:
            events.append(json.dumps({'method':'thread/tokenUsage/updated','params':{'threadId':'t','tokenUsage':{'total':{'inputTokens':input_,'cachedInputTokens':cached,'outputTokens':out},'last':{'inputTokens':100 if input_==100 else 200}}}}).encode())
        events.extend([json.dumps({'method':'turn/completed','params':{'turn':{'status':'completed'}}}).encode(),b''])
        proc=SimpleNamespace(stdout=SimpleNamespace(readline=AsyncMock(side_effect=events)))
        rpc=provider_sessions.CodexRPC(proc,turn,lambda _:None);rpc.request_active=True
        await rpc.consume();rows=self.store.usage(self.rid)
        self.assertEqual(len(rows),2);self.assertEqual(sum(r['input'] for r in rows),300);self.assertEqual(sum(r['output'] for r in rows),35)
        self.assertEqual(sum(r['cached_read'] for r in rows),100);self.assertEqual(turn.state['last_request_input'],200)
        replay=provider_sessions.CodexRPC(type('P',(),{'stdout':type('S',(),{'readline':AsyncMock(side_effect=[events[2],events[3],b''])})()})(),turn,lambda _:None)
        replay.request_active=True;await replay.consume()
        self.assertEqual(sum(r['input'] for r in self.store.usage(self.rid)),300)

    async def test_native_windows_quotes_and_spaced_batch_paths_are_preserved(self):
        from workflows import verify_commands
        path=self.root/'args with spaces.cmd'
        path.write_text('@echo off\nif "%~1"=="hello world" (echo ARGS_OK& exit /b 0) else (exit /b 7)\n')
        results=await verify_commands(self.store,self.rid,self.root,['"args with spaces.cmd" "hello world"','cmd.exe /d /c "echo nested quotes"'])
        self.assertEqual([r['exit_code'] for r in results],[0,0]);self.assertIn('ARGS_OK',results[0]['output']);self.assertIn('nested quotes',results[1]['output'])

    async def test_scope_violation_is_durable_and_recheck_cannot_bypass_it(self):
        from orchestrator import ExecutionScopeViolation
        plan,aid=self.fixture.approved();self.wf=self.fixture.wf;c=self.coordinator();await c.bindings()
        task={'goal':'write','allowed_paths':['out.txt'],'acceptance_commands':[]}
        action=c.prepare({'type':'execute','agents':['deepseek'],'task':task,'plan_ref':plan['ref'],'approval_ids':[aid]})
        (self.root/'outside.txt').write_text('unauthorized')
        c.work.execute_supervised=AsyncMock(side_effect=ExecutionScopeViolation('scope failure',{'agent':'deepseek','kind':'execute','status':'failed','changed_files':['outside.txt']},{'outside.txt':None}))
        await c.execute(action,checked=True);state,_=self.store.load_workflow(self.rid)
        self.assertEqual(state['scope_violation']['before'],{'outside.txt':None})
        c.work.reply=AsyncMock(side_effect=AssertionError('no model'))
        self.store.message(self.rid,'you','继续')
        with patch('coordination.preflight',new=AsyncMock(return_value=[check('healthy',True,'ok','host')])):
            await c.work.supervised(self.rid,'继续',self.root,self.settings)
        c.work.reply.assert_not_awaited();self.assertEqual(self.store.room(self.rid)['status'],'blocked')
        (self.root/'outside.txt').unlink();self.assertTrue(c.scope_check()['ok'])

    async def test_failed_reviews_do_not_spend_discussion_rounds_and_formatted_review_counts(self):
        plan=self.fixture.plan();self.wf=self.fixture.wf;c=self.coordinator()
        action={'type':'ask','kind':'review','agents':['claude'],'plan_ref':plan['ref'],'instruction':'review'}
        c.work.reply=AsyncMock(side_effect=RuntimeError('CLI startup failed'))
        await c.ask(action,self.policy);self.assertEqual(c.state['discussion_rounds'],{})
        async def answer(*a,**kw):
            text='表态：同意 '+plan['ref'];self.store.message(self.rid,'claude',text);return text
        c.work.reply=answer;await c.ask(action,self.policy)
        self.assertEqual(c.state['discussion_rounds']['P2'],1);self.assertTrue(c.state['votes'][plan['ref']]['claude']['agree'])

    async def test_host_acceptance_uses_approved_original_by_index_after_failure(self):
        plan,aid=self.fixture.approved();self.wf=self.fixture.wf;c=self.coordinator()
        c.state['evidence']=[{'id':'E01','kind':'execute','agent':'deepseek','status':'failed','acceptance_commands':['cmd.exe /d /c "echo VERIFIED"'],'bound_to':current_refs(c.state)}]
        c.state['next_evidence']=2;c.save()
        await c.host_check({'checks':['acceptance:1']})
        self.assertEqual(c.state['evidence'][-1]['status'],'complete')
        self.assertIn('VERIFIED',c.state['evidence'][-1]['output'][0]['detail'])

    async def test_only_complete_current_host_acceptance_can_verify_an_interrupted_delivery(self):
        plan,aid=self.fixture.approved();self.wf=self.fixture.wf;c=self.coordinator();await c.bindings();c.state['rules']=RULES
        c.state['evidence']=[{'id':'E01','kind':'execute','agent':'deepseek','status':'failed','acceptance_commands':['echo ONE','echo TWO'],'bound_to':current_refs(c.state)}]
        c.state['next_evidence']=2;c.save()
        await c.host_check({'checks':['acceptance:1']});self.assertNotIn('verifies_execution',c.state['evidence'][-1])
        await c.host_check({'checks':['acceptance:1','acceptance:2']});verified=c.state['evidence'][-1]
        self.assertEqual(verified['verifies_execution'],'E01');self.assertEqual(c.state['evidence'][0]['status'],'failed')
        full={'rule_id':'R1','status':'met','note':'host independently verified delivery','evidence_ids':[verified['id']],'bound_to':current_refs(c.state)}
        value=control({'type':'finish','full_checks':[full]})
        validate_control(value,c.state,{'claude'})
        c.state['scope_violation']={'before':{'outside.txt':None}}
        with self.assertRaises(ValueError):validate_control(value,c.state,{'claude'})
        c.state['scope_violation']=None;c.state['code_hash']='changed-after-verification'
        with self.assertRaises(ValueError):validate_control(value,c.state,{'claude'})

    async def test_failed_http_call_records_unknown_usage_instead_of_zero(self):
        import httpx
        source=next(a for a in self.settings['agents'] if a['id']=='deepseek').copy()
        source['base_url']='https://generativelanguage.googleapis.com/v1beta/openai';source['backend']='openai-compatible'
        turn=ContextTurn(self.store,self.rid,'deepseek',source,'gemini-fixture',self.settings,'failure');prompt=turn.compose('contract')
        real=httpx.AsyncClient
        with patch.object(provider_sessions.httpx,'AsyncClient',side_effect=lambda **kw:real(**kw,transport=httpx.MockTransport(lambda r:httpx.Response(403,json={'error':'private header omitted'})))):
            with self.assertRaises(RuntimeError):await provider_sessions.api_conversation(turn,prompt,'fixture-key',source['base_url'],lambda _:None)
        row=self.store.usage(self.rid)[0];self.assertEqual(row['status'],'failed');self.assertIsNone(row['input']);self.assertIsNone(row['output'])

    async def test_host_recheck_route_is_csrf_protected_and_does_not_call_models(self):
        import httpx,server
        from room_settings import RoomSettings
        failed=[check('executor_start',False,'same fixture fault','mcp')];c=self.coordinator();c.host_error(failed)
        c.work.reply=AsyncMock(side_effect=AssertionError('no model'))
        with patch.object(server,'STORE',self.store),patch.object(server,'WORK',c.work),patch.object(server,'ROOMS',RoomSettings(self.store)),patch('coordination.preflight',new=AsyncMock(return_value=failed)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url=server.ORIGIN) as client:
                path='/api/rooms/'+self.rid+'/recheck'
                self.assertEqual((await client.post(path,json={})).status_code,403)
                response=await client.post(path,json={},headers={'X-Tiered-Token':server.TOKEN})
                self.assertEqual(response.status_code,200,response.text)
                await c.work.running[self.rid]
        c.work.reply.assert_not_awaited();self.assertEqual(self.store.room(self.rid)['status'],'blocked')
        self.assertFalse(any(m['provider']=='you' and m['text']=='__host_recheck__' for m in self.store.messages(self.rid)))
        self.assertEqual(self.store.host_actions(self.rid)[0]['kind'],'recheck')

    async def test_approve_route_rejects_stale_buttons_and_records_exact_user_source(self):
        import httpx,server
        from room_settings import RoomSettings
        plan=self.fixture.plan();self.wf=self.fixture.wf;self.wf['awaiting_approval']=plan['ref'];c=self.coordinator()
        c.work.reply=AsyncMock(return_value=json.dumps(control({'type':'wait'},RULES)))
        with patch.object(server,'STORE',self.store),patch.object(server,'WORK',c.work),patch.object(server,'ROOMS',RoomSettings(self.store)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url=server.ORIGIN) as client:
                path='/api/rooms/'+self.rid+'/approve';headers={'X-Tiered-Token':server.TOKEN}
                self.assertEqual((await client.post(path,json={'plan_ref':'P2@99'},headers=headers)).status_code,400)
                pending=(await client.get('/api/rooms/'+self.rid)).json()['approval_document']
                response=await client.post(path,json={'plan_ref':plan['ref'],'document_id':pending['id'],'document_sha256':pending['document_sha256']},headers=headers)
                self.assertEqual(response.status_code,200,response.text);await c.work.running[self.rid]
                audit=(await client.get('/api/rooms/'+self.rid+'/workflow-audit')).json()
        self.assertEqual(len(audit['approvals']),1);approval=audit['approvals'][0]
        self.assertEqual(approval['kind'],'user');self.assertEqual(approval['source'],'button')
        self.assertIsNone(approval['message_id']);self.assertTrue(approval['action_id'])
        self.assertEqual(audit['host_actions'][0]['id'],approval['action_id'])
        self.assertFalse(any(m['provider']=='you' and m['text'].startswith('批准 ') for m in self.store.messages(self.rid)))

    async def test_executor_receives_contract_once_and_never_group_history(self):
        work=Workflows(self.store);self.store.message(self.rid,'claude','DO-NOT-SEND-PEER-HISTORY');requests=[]
        class Reply:
            isError=False
            def __init__(self,value):self.content=[type('Block',(),{'type':'text','text':json.dumps(value)})()]
        class Session:
            async def call_tool(inner,name,args):
                if name=='get_deepseek_recovery':return Reply({'ok':True,'pending':[]})
                if name=='start_deepseek':
                    requests.append(args);(self.root/'out.txt').write_text('actual delivery');return Reply({'ok':True,'job_id':'job'})
                return Reply({'ok':True,'ready':True,'status':'completed','result':{'final_message':'STATUS: DONE'}})
        @asynccontextmanager
        async def session(*args,**kwargs):yield Session()
        task={'id':'T-01','goal':'UNIQUE-CONTRACT-MARKER','allowed_paths':['out.txt'],'acceptance_commands':[],'context':'self-contained'}
        with patch('orchestrator.deepseek_session',session):result=await work.execute_supervised(self.rid,'deepseek',task,self.root,self.settings)
        self.assertEqual(result['status'],'complete');self.assertEqual(result['changed_files'],['out.txt'])
        body=json.dumps(requests[0]);self.assertEqual(body.count('UNIQUE-CONTRACT-MARKER'),1);self.assertNotIn('DO-NOT-SEND',body)


if __name__=='__main__':unittest.main()
