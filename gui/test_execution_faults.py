"""Replay failed coding jobs locally: no paid provider and no supervisor recovery."""
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
import json
import threading
import unittest
from unittest.mock import AsyncMock,patch

import test_token_optimization as fixtures
from execution_faults import runtime_fault
from preflight import check,RESUME_BUTTON
from workflow_state import delivery_verified


class HostRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.RuntimeTests.asyncSetUp
    asyncTearDown=fixtures.RuntimeTests.asyncTearDown
    coordinator=fixtures.RuntimeTests.coordinator

    def prepared(self):
        plan,aid=self.fixture.approved();self.wf=self.fixture.wf;c=self.coordinator()
        action=c.prepare({'type':'execute','agents':['deepseek'],'plan_ref':plan['ref'],'approval_ids':[aid],
            'task':{'goal':'write one file','allowed_paths':['out.txt'],'acceptance_commands':['type out.txt']}})
        c.work.reply=AsyncMock(side_effect=AssertionError('host recovery must not call a model'))
        c.work.quota=AsyncMock(return_value=None)
        return c,action

    def checks(self,pending=False):
        recovery=check('recovery',not pending,'pending writes' if pending else 'no pending writes','mcp')
        recovery['records']=[{'path':'out.txt','transaction_id':'tx','sha256':'old-hash'}] if pending else []
        return [check('executor_start',True,'output=32768; tool_rounds=16','mcp'),recovery]

    def session(self,error,*,write=False,completed=False,start_error=None,lost_start=False,answer='STATUS: DONE'):
        calls=[];root=self.root
        class Reply:
            isError=False
            def __init__(self,value):self.content=[type('Block',(),{'type':'text','text':json.dumps(value)})()]
        class Session:
            async def call_tool(self,name,args):
                calls.append(name)
                if name=='get_deepseek_recovery':return Reply({'ok':True,'pending':[]})
                if name=='start_deepseek':
                    if lost_start:raise ConnectionError('start response lost')
                    if start_error:return Reply({'ok':False,'error':start_error})
                    if write:(root/'out.txt').write_text('verified delivery',encoding='utf-8')
                    return Reply({'ok':True,'job_id':'local-job'})
                if name=='get_deepseek_result':
                    return Reply({'ok':True,'ready':True,'status':'completed' if completed else 'failed',
                        'result':{'final_message':answer} if completed else None,'error':error})
                return Reply({'ok':True})
        @asynccontextmanager
        async def opened(*args,**kwargs):yield Session()
        return opened,calls

    async def test_output_truncation_and_repeated_rechecks_never_call_supervisor_or_resubmit(self):
        c,a=self.prepared();session,calls=self.session('DeepSeek model response incomplete: category=output_truncated')
        execute=AsyncMock(wraps=c.work.execute_supervised);c.work.execute_supervised=execute
        with patch('orchestrator.deepseek_session',session),patch('coordination.preflight',new=AsyncMock(return_value=self.checks())):
            self.assertFalse(await c.execute(a,checked=True))
            for text in ['继续','已修复',RESUME_BUTTON]:
                await c.work.supervised(self.rid,text,self.root,self.settings)
        c.work.reply.assert_not_awaited();self.assertEqual(execute.await_count,1)
        self.assertEqual(calls.count('start_deepseek'),1)
        state,_=self.store.load_workflow(self.rid);execution=state['evidence'][0]
        self.assertTrue(execution['host_error']);self.assertEqual(execution['runtime_fault']['code'],'output_truncated')
        self.assertEqual(execution['status'],'failed');self.assertTrue(state['pending_action']['started'])
        self.assertEqual(state['blocker']['runtime']['execution_id'],execution['id'])
        self.assertFalse(any('交由监工' in m['text'] for m in self.store.messages(self.rid)))

    async def test_mutation_and_turn_limit_run_host_acceptance_preserving_files_and_original_failure(self):
        c,a=self.prepared();session,calls=self.session('workspace mutation requires review; DO NOT RETRY; Agent loop exceeded max_turns (16)',write=True)
        with patch('orchestrator.deepseek_session',session),patch('coordination.preflight',new=AsyncMock(return_value=self.checks(True))):
            self.assertFalse(await c.execute(a,checked=True))
        state,_=self.store.load_workflow(self.rid);execution=state['evidence'][0]
        self.assertEqual(execution['runtime_fault']['causes'],['mutation_review','tool_round_limit'])
        self.assertEqual(execution['changed_files'],['out.txt']);self.assertEqual(execution['status'],'failed')
        self.assertTrue(delivery_verified(state,execution));self.assertEqual(state['recovery_records'][0]['sha256'],'old-hash')
        before=(self.root/'out.txt').read_bytes()
        with patch('coordination.preflight',new=AsyncMock(return_value=self.checks(True))),patch('workflows.verify_commands',new=AsyncMock(side_effect=AssertionError('passing acceptance must not rerun'))):
            await c.work.supervised(self.rid,RESUME_BUTTON,self.root,self.settings)
        c.work.reply.assert_not_awaited();self.assertEqual((self.root/'out.txt').read_bytes(),before)
        self.assertNotIn('acknowledge_deepseek_mutations',calls)

    async def test_cleared_recovery_completes_host_review_without_model_or_faking_worker_done(self):
        c,a=self.prepared();session,_=self.session('Agent loop exceeded max_turns (16)',write=True)
        with patch('orchestrator.deepseek_session',session),patch('coordination.preflight',new=AsyncMock(return_value=self.checks(True))):await c.execute(a,checked=True)
        with patch('coordination.preflight',new=AsyncMock(return_value=self.checks())),patch('workflows.verify_commands',new=AsyncMock(side_effect=AssertionError('already passed'))):
            await c.work.supervised(self.rid,RESUME_BUTTON,self.root,self.settings)
        state,_=self.store.load_workflow(self.rid)
        self.assertIsNone(state['blocker']);self.assertIsNone(state['pending_action'])
        self.assertIn('E01',state['runtime_resolutions']);self.assertEqual(state['evidence'][0]['status'],'failed')
        self.assertFalse(state['evidence'][0].get('worker_reported_done'));c.work.reply.assert_not_awaited()

    async def test_unknown_inflight_error_is_also_host_owned(self):
        c,a=self.prepared();session,_=self.session('unexpected internal failure')
        with patch('orchestrator.deepseek_session',session),patch('coordination.preflight',new=AsyncMock(return_value=self.checks())):await c.execute(a,checked=True)
        state,_=self.store.load_workflow(self.rid)
        self.assertEqual(state['evidence'][0]['runtime_fault']['code'],'executor_runtime');c.work.reply.assert_not_awaited()

    async def test_lost_start_response_is_not_treated_as_safe_to_retry(self):
        c,a=self.prepared();session,_=self.session('',lost_start=True)
        with patch('orchestrator.deepseek_session',session),patch('coordination.preflight',new=AsyncMock(return_value=self.checks())):await c.execute(a,checked=True)
        state,_=self.store.load_workflow(self.rid)
        self.assertTrue(state['pending_action']['started']);self.assertEqual(state['evidence'][0]['runtime_fault']['stage'],'starting')
        self.assertFalse(state['evidence'][0]['runtime_fault']['automatic_retry']);c.work.reply.assert_not_awaited()

    async def test_explicit_start_rejection_keeps_original_prestart_resume_path(self):
        c,a=self.prepared();session,_=self.session('',start_error='workspace busy')
        with patch('orchestrator.deepseek_session',session):await c.execute(a,checked=True)
        state,_=self.store.load_workflow(self.rid)
        self.assertFalse(state['pending_action']['started']);self.assertNotIn('runtime',state['blocker'])
        self.assertEqual(state['evidence'],[]);c.work.reply.assert_not_awaited()

    async def test_semantic_blocked_reply_stays_normal_worker_evidence(self):
        c,a=self.prepared();session,_=self.session('',completed=True,answer='STATUS: BLOCKED\nNeed a design decision')
        with patch('orchestrator.deepseek_session',session):self.assertTrue(await c.execute(a,checked=True))
        self.assertEqual(c.state['evidence'][0]['status'],'failed');self.assertFalse(c.state['evidence'][0].get('host_error'))
        self.assertIsNone(c.state['blocker'])

    async def test_acceptance_runner_exception_does_not_start_model_recovery_or_immediate_test_retry(self):
        c,a=self.prepared();session,_=self.session('',write=True,completed=True)
        with patch('orchestrator.deepseek_session',session),patch('workflows.verify_commands',new=AsyncMock(side_effect=RuntimeError('command runner unavailable'))) as tests,patch('coordination.preflight',new=AsyncMock(return_value=self.checks())):
            await c.execute(a,checked=True)
        self.assertEqual(tests.await_count,1);self.assertEqual(c.state['evidence'][0]['runtime_fault']['stage'],'verification')
        self.assertEqual(c.state['evidence'][0]['status'],'failed');c.work.reply.assert_not_awaited()

    async def test_legacy_false_host_error_flag_is_reclassified_before_any_model_call(self):
        c,a=self.prepared();await c.bindings();(self.root/'out.txt').write_text('legacy delivery',encoding='utf-8');await c.bindings()
        execution=c.evidence({'kind':'execute','agent':'deepseek','status':'failed','host_error':False,
            'error':'workspace mutation requires review; Agent loop exceeded max_turns (16)',
            'acceptance_commands':['type out.txt'],'allowed_paths':['out.txt'],
            'file_hashes':{'out.txt':hashlib.sha256((self.root/'out.txt').read_bytes()).hexdigest()}})
        c.evidence({'kind':'check','agent':'host','status':'complete','verifies_execution':execution['id'],
            'tests':[{'command':'type out.txt','exit_code':0,'output':'legacy delivery'}],'tests_run':True})
        c.work.execute_supervised=AsyncMock(side_effect=AssertionError('legacy job must not be repeated'))
        with patch('coordination.preflight',new=AsyncMock(return_value=self.checks(True))),patch('workflows.verify_commands',new=AsyncMock(side_effect=AssertionError('already verified'))):
            await c.work.supervised(self.rid,'继续',self.root,self.settings)
        state,_=self.store.load_workflow(self.rid)
        self.assertEqual(state['blocker']['runtime']['execution_id'],execution['id'])
        self.assertFalse(state['evidence'][0]['host_error'])  # Preserve historical evidence.
        c.work.reply.assert_not_awaited();c.work.execute_supervised.assert_not_awaited()

    async def test_changed_environment_fault_on_resume_also_stays_host_owned(self):
        c,a=self.prepared();c.host_error([check('executor_start',False,'HTTP 403','mcp')],a)
        with patch('coordination.preflight',new=AsyncMock(return_value=[check('executor_start',False,'HTTP 429','mcp')])):
            await c.work.supervised(self.rid,RESUME_BUTTON,self.root,self.settings)
        c.work.reply.assert_not_awaited()

    async def test_real_mcp_health_accepts_room_budget_above_16k_without_generation(self):
        from engines import deepseek_session,read_response
        from context_settings import context_settings
        source=next(m for m in self.settings['members'] if m['id']=='deepseek')
        source={**source,'base_url':'http://127.0.0.1:1/v1','no_auth':True}
        with patch('engines.preferences.get_member_key',return_value='fixture'),patch('model_catalog.effective_effort',new=AsyncMock(return_value=None)):
            async with deepseek_session(self.root,source['model'],member=source,execution_budget=context_settings()) as session:
                health=json.loads(read_response(await session.call_tool('executor_health',{})))
        self.assertTrue(health['ok'],health);self.assertEqual(health['max_output_tokens'],32768)
        self.assertEqual(health['max_turns'],16)

    async def test_real_coding_mcp_and_isolated_http_child_send_room_output_budget(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                body=json.dumps({'id':'local-fixture','choices':[{'message':{'role':'assistant','content':'STATUS: DONE\nTESTS: host verifies'},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':10,'completion_tokens':5}}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            c,a=self.prepared();source=next(m for m in self.settings['members'] if m['id']=='deepseek')
            source.update(base_url='http://127.0.0.1:'+str(http.server_port)+'/v1',no_auth=True)
            (self.root/'out.txt').write_text('protocol fixture',encoding='utf-8')
            with patch('engines.preferences.get_member_key',return_value='fixture'),patch('model_catalog.effective_effort',new=AsyncMock(return_value=None)):
                result=await c.work.execute_supervised(self.rid,'deepseek',a['task'],self.root,self.settings)
            self.assertEqual(result['status'],'complete',result.get('error'));self.assertEqual(len(received),1)
            self.assertEqual(received[0]['max_tokens'],32768);c.work.reply.assert_not_awaited()
        finally:http.shutdown();http.server_close();thread.join(timeout=2)


class FaultIdentityTests(unittest.TestCase):
    def test_unknown_semantic_text_is_not_used_for_legacy_reclassification(self):
        self.assertIsNone(runtime_fault('the algorithm does not support this case',known_only=True))


if __name__=='__main__':unittest.main()
