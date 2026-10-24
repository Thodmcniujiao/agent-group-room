"""Independent audit fault variants, provenance and real budget-gate behavior (offline)."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock,patch
import unittest

import test_token_optimization as fixtures
from preflight import check,fingerprint
from context_settings import context_settings
from context import estimate
from budget_control import ContextBudgetExceeded
from workflow_state import json_patch,apply_patch,current_refs,record_vote
from usage import normalize,record
from supervision import validate_control
import provider_sessions


class AuditStateTests(fixtures.Fixture):
    def test_fault_identity_ignores_paths_pid_clock_but_keeps_error_codes_and_check(self):
        def fault(detail,name='executor_start'):return fingerprint([check(name,False,detail,'mcp')])
        self.assertEqual(fault('HTTP 403 at C:\\Temp\\room-123 pid 4567 2026-10-22T10:11:12Z'),fault('HTTP 403 at C:\\Temp\\room-456 pid 8901 2026-10-23T12:13:14Z'))
        self.assertNotEqual(fault('HTTP 403 at C:\\Temp\\x'),fault('HTTP 429 at C:\\Temp\\y'))
        self.assertNotEqual(fault('WinError 5'),fault('WinError 2'))
        self.assertNotEqual(fault('same','git_host'),fault('same','executor_start'))

    def test_nested_patch_roundtrip_changes_only_touched_checks_and_evidence(self):
        old={'checks':{'R1':{'status':'pending','note':'keep me'},'R2':{'note':'UNCHANGED'*1000}},'evidence':[{'id':'E01','status':'complete'},{'id':'E02','status':'failed'}],'step':1}
        new=copy.deepcopy(old);new['checks']['R1']['status']='met';new['evidence'].pop(0);new['evidence'].append({'id':'E03','status':'complete'});new['step']=2
        delta=json_patch(old,new)
        self.assertEqual(apply_patch(old,delta),new);self.assertNotIn('UNCHANGED',json.dumps(delta));self.assertNotIn('E02',delta['lists']['evidence']['items']['set'])
        self.assertEqual(old['checks']['R1']['status'],'pending')

    def test_wrong_author_cannot_cast_an_agreement_or_objection(self):
        plan=self.plan()
        for text in ['表态：同意 P2@1','表态：反对 P2@1：不合适']:
            mid=self.store.message(self.rid,'chatgpt',text)
            with self.assertRaises(ValueError):record_vote(self.wf,self.store,self.rid,'claude',plan['ref'],mid)
        self.assertEqual(self.wf['votes'],{})

    def test_missing_consensus_requires_real_user_decision_for_the_current_plan(self):
        self.plan();self.wf.update(rules=fixtures.RULES,code_hash='now',consensus_members=['chatgpt','claude'])
        self.wf['evidence']=[{'id':'E01','kind':'ask','status':'complete'}]
        full={'rule_id':'R1','status':'met','evidence_ids':['E01'],'note':'verified','bound_to':current_refs(self.wf)}
        decision=fixtures.control({'type':'finish','full_checks':[full]})
        with self.assertRaisesRegex(ValueError,'明确共识'):validate_control(decision,self.wf,{'chatgpt','claude'})
        self.wf['user_resolution']={'plan_ref':'P2@1','approval_id':'invented'}
        with self.assertRaises(ValueError):validate_control(decision,self.wf,{'chatgpt','claude'})
        from workflow_state import accept_user_approval
        self.wf['awaiting_approval']='P2@1';mid=self.store.message(self.rid,'you','同意');accept_user_approval(self.wf,self.store,self.rid,mid)
        validate_control(decision,self.wf,{'chatgpt','claude'})

    def test_missing_codex_cache_baseline_is_unknown_and_negative_attribution_is_estimate_error(self):
        usage,_=normalize('codex',{'total':{'inputTokens':200,'cachedInputTokens':80}},{'inputTokens':100})
        self.assertEqual(usage['input'],100);self.assertIsNone(usage['cached_read'])
        turn=self.turn('deepseek');turn.composition={'instructions':1000};record(turn,'openai_compatible',{'prompt_tokens':40},request_ref='attribution')
        composition=json.loads(self.store.usage(self.rid)[0]['composition'])
        self.assertEqual(composition['unattributed'],0);self.assertEqual(composition['estimate_overage'],960)

    def test_budget_selection_preserves_blocker_prefix_and_unread_remainder(self):
        self.settings['context']=context_settings({'soft_tokens':22000,'output_tokens':1024,'margin_tokens':512,'overflow':'pause'})
        mid=self.store.message(self.rid,'host','BLOCKER-DETAIL '+('x'*100000),status='host_error')
        peer=self.store.message(self.rid,'claude','OLD-ORDINARY-PEER')
        turn=self.turn('supervisor');turn.compose('check the blocker')
        selected=next(m for m in turn.selected if m['id']==mid)
        self.assertTrue(selected['partial']);self.assertIn('BLOCKER-DETAIL',turn.input)
        self.assertFalse(any(m['id']==peer for m in turn.selected));self.assertTrue(any(m['id']==mid for m in turn.omitted))
        turn.complete('blocked')
        from delivery import contains
        self.assertFalse(contains(turn.state['delivered'],selected['seq']))
        offset=selected['next_offset']
        while offset is not None:offset=turn.read('group_get_message',{'message_id':mid,'offset':offset})['next_offset']
        self.assertTrue(contains(turn.state['delivered'],selected['seq']))

    def test_calibration_changes_actual_cli_and_api_compose_gates_only_after_twenty_samples(self):
        for aid in ['deepseek','chatgpt']:
            with self.subTest(adapter=aid):
                self.settings['executor']=aid
                self.settings['context']=context_settings({'soft_tokens':14000,'output_tokens':1024,'margin_tokens':512,'overflow':'pause'})
                turn=self.turn(aid);turn.last_full_estimate=100
                adapter='codex' if aid=='chatgpt' else 'openai_compatible'
                for index in range(19):record(turn,adapter,{'total':{'inputTokens':100*(index+1)},'last':{'inputTokens':100}} if adapter=='codex' else {'prompt_tokens':100},request_ref=str(index))
                with self.assertRaises(ContextBudgetExceeded):turn.compose('汉'*10000)
                turn.last_full_estimate=100
                record(turn,adapter,{'total':{'inputTokens':2000},'last':{'inputTokens':100}} if adapter=='codex' else {'prompt_tokens':100},request_ref='19')
                turn.compose('汉'*10000)
                self.assertLess(turn.request_estimate(turn.input),estimate(turn.input));self.assertLessEqual(turn.request_estimate(turn.input)+512,turn.input_budget)

    def test_cli_initial_gate_includes_actual_supervisor_instructions(self):
        self.settings['context']=context_settings({'soft_tokens':4096,'output_tokens':2048,'margin_tokens':512,'overflow':'pause'})
        turn=self.turn('supervisor')
        with self.assertRaises(ContextBudgetExceeded):turn.compose('next')

    def test_public_report_export_excludes_native_sessions_and_unlisted_json(self):
        import sys,zipfile
        from pathlib import Path
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from export_test_reports import export
        root=self.base/'reports';(root/'acceptance/live-state/sessions').mkdir(parents=True)
        (root/'acceptance/live-state/sessions/private.json').write_text('PRIVATE-THOUGHT')
        (root/'README.md').write_text('public');(root/'unknown.json').write_text('PRIVATE-UNKNOWN')
        (root/'acceptance/live-task.json').write_text('{"status":"complete"}')
        target=self.base/'public.zip';export(root,target)
        with zipfile.ZipFile(target) as z:
            self.assertEqual(set(z.namelist()),{'README.md','acceptance/live-task.json','export-manifest.json'})
            self.assertNotIn(b'PRIVATE',b''.join(z.read(n) for n in z.namelist()))

    def test_executor_handoff_has_full_contract_state_and_recovery_but_inlet_sends_body_once(self):
        from workflows import contract
        task={'id':'T-01','goal':'SINGLE-FULL-CONTRACT','allowed_paths':['out.txt'],'acceptance_commands':[],'context':'self-contained'}
        text=contract(task);self.wf['pending_action']={'task':task,'contract_hash':'sha'};self.wf['recovery_records']=[{'path':'out.txt','sha256':'current','transaction_id':'recovery1'}]
        self.store.save_workflow(self.rid,self.wf,0)
        old=self.turn('deepseek');old.compose('project');old.complete('own public result')
        new=self.turn('deepseek',model='changed',contract_text=text)
        facts=new.state['handoff_package']['verified_public_state']
        self.assertEqual(facts['full_contract'],text);self.assertEqual(facts['recovery_records'][0]['transaction_id'],'recovery1');self.assertIn('execution_state',facts)
        extra=new.compose('project');self.assertEqual(json.dumps({'task':text,'context':extra}).count('SINGLE-FULL-CONTRACT'),1)
        self.assertFalse(new.tools);self.assertEqual(new.selected,[])


class AuditRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.RuntimeTests.asyncSetUp
    asyncTearDown=fixtures.RuntimeTests.asyncTearDown
    coordinator=fixtures.RuntimeTests.coordinator

    async def test_api_tool_loop_gate_uses_calibration_in_the_actual_send_path(self):
        import httpx
        from types import SimpleNamespace
        self.settings['executor']=None;self.settings['context']=context_settings({'soft_tokens':16000,'output_tokens':1024,'margin_tokens':512,'overflow':'pause'})
        turn=self.fixture.turn('deepseek');turn.compose('query');turn.last_full_estimate=100
        for index in range(19):record(turn,'openai_compatible',{'prompt_tokens':100},request_ref='cal-'+str(index))
        calls=[];real=provider_sessions.AsyncOpenAI
        def respond(request):
            calls.append(json.loads(request.content))
            chunk={'id':'fixture','object':'chat.completion.chunk','created':0,'model':turn.model,'choices':[{'index':0,'delta':{'role':'assistant','content':'done'},'finish_reason':'stop'}]}
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n')
        def client(**kwargs):return real(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        payload='汉'*10000
        with patch.object(provider_sessions,'AsyncOpenAI',side_effect=client):
            with self.assertRaises(ContextBudgetExceeded):await provider_sessions.api_conversation(turn,payload,'fixture','https://example.invalid/v1',lambda _:None)
            self.assertEqual(calls,[])
            turn.state['native']['messages']=[{'role':'system','content':turn.instructions()}];turn.last_full_estimate=100
            record(turn,'openai_compatible',{'prompt_tokens':100},request_ref='cal-19')
            await provider_sessions.api_conversation(turn,payload,'fixture','https://example.invalid/v1',lambda _:None)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['messages'][-1]['content'],payload)

    async def test_claude_snapshot_is_explicit_on_initial_and_resumed_native_calls(self):
        from types import SimpleNamespace
        import engines
        turn=self.fixture.turn('claude');turn.tools=False
        captured=[]
        def proc(*args,**kwargs):
            captured.append(args)
            return SimpleNamespace(stdin=SimpleNamespace(write=lambda _:None,drain=AsyncMock(),close=lambda:None),stdout=SimpleNamespace(readline=AsyncMock(side_effect=[json.dumps({'type':'result','result':'ok','usage':{}}).encode(),b''])),wait=AsyncMock(),returncode=0)
        with patch.object(engines,'find_cli',return_value='fixture-cli'),patch.object(asyncio,'create_subprocess_exec',new=AsyncMock(side_effect=proc)),patch.object(engines,'kill_tree',new=AsyncMock()):
            for _ in range(2):await provider_sessions.claude_conversation(turn,'fixture',self.root,'high',lambda _:None)
        self.assertIn('--session-id',captured[0]);self.assertIn('--resume',captured[1])
        for args in captured:
            self.assertEqual(args[args.index('--system-prompt-snapshot')+1],'on')
            self.assertEqual(args.count('--append-system-prompt'),1)
            self.assertEqual(args[args.index('--append-system-prompt')+1],turn.instructions())
    async def test_scope_restored_continues_exact_contract_before_any_monitor_call(self):
        from orchestrator import ExecutionScopeViolation
        plan,aid=self.fixture.approved();self.wf=self.fixture.wf;c=self.coordinator();await c.bindings()
        task={'goal':'write file','allowed_paths':['out.txt'],'acceptance_commands':[]}
        action=c.prepare({'type':'execute','agents':['deepseek'],'task':task,'plan_ref':plan['ref'],'approval_ids':[aid]})
        (self.root/'outside.txt').write_text('outside')
        c.work.execute_supervised=AsyncMock(side_effect=ExecutionScopeViolation('scope',{'agent':'deepseek','kind':'execute','status':'failed'},{'outside.txt':None}))
        await c.execute(action,checked=True)
        (self.root/'outside.txt').unlink();order=[]
        async def execute(*args):order.append('execute');self.assertEqual(args[2],action['task']);return {'agent':'deepseek','kind':'execute','status':'complete'}
        async def monitor(*args,**kwargs):order.append('monitor');return json.dumps(fixtures.control({'type':'wait'},fixtures.RULES))
        c.work.execute_supervised=execute;c.work.reply=monitor
        self.store.message(self.rid,'you','继续')
        with patch('coordination.preflight',new=AsyncMock(return_value=[check('executor_start',True,'ok','mcp')])):await c.work.supervised(self.rid,'继续',self.root,self.settings)
        self.assertEqual(order,['execute','monitor'])
        state,_=self.store.load_workflow(self.rid);self.assertEqual(state['evidence'][0]['status'],'failed');self.assertIsNone(state['pending_action'])

    async def test_changed_temp_path_and_pid_still_causes_zero_model_calls_on_recheck(self):
        c=self.coordinator();c.host_error([check('executor_start',False,'HTTP 403 C:\\Temp\\one-123 pid 456','mcp')]);c.work.reply=AsyncMock(side_effect=AssertionError('model must not run'))
        self.store.message(self.rid,'you','继续')
        with patch('coordination.preflight',new=AsyncMock(return_value=[check('executor_start',False,'HTTP 403 C:\\Temp\\two-789 pid 1011','mcp')])):await c.work.supervised(self.rid,'继续',self.root,self.settings)
        c.work.reply.assert_not_awaited()

    async def test_vote_material_contains_unresolved_checks_and_opposition(self):
        self.fixture.plan();self.wf=self.fixture.wf;c=self.coordinator()
        c.state['checks']={'R1':{'status':'pending','note':'MISSING-TEST','evidence_ids':[]}}
        c.state['votes']={'P2@1':{'claude':{'agree':False,'reason':'REAL-OBJECTION'}}};c.save();captured=[]
        async def reply(rid,agent,material,*args,**kwargs):
            turn=self.fixture.turn(agent);captured.append(material(turn));text='表态：同意 P2@1';self.store.message(rid,agent,text);return text
        c.work.reply=reply
        await c.ask({'kind':'vote','agents':['chatgpt'],'plan_ref':'P2@1','instruction':'vote'},self.policy)
        self.assertIn('MISSING-TEST',captured[0]);self.assertIn('REAL-OBJECTION',captured[0])

    async def test_isolated_executor_consultation_does_not_get_peer_open_questions(self):
        self.fixture.plan();self.wf=self.fixture.wf;c=self.coordinator()
        c.state['votes']={'P2@1':{'claude':{'agree':False,'reason':'DO-NOT-SEND-PEER-REASON'}}};c.save();captured=[]
        async def reply(rid,agent,material,*args,**kwargs):
            turn=self.fixture.turn(agent);captured.append(material(turn));text='表态：同意 P2@1';self.store.message(rid,agent,text);return text
        c.work.reply=reply
        await c.ask({'kind':'vote','agents':['deepseek'],'plan_ref':'P2@1','instruction':'只核对独立要求'},self.policy)
        self.assertNotIn('DO-NOT-SEND-PEER-REASON',captured[0]);self.assertNotIn('UNIQUE-PLAN-BODY',captured[0])
