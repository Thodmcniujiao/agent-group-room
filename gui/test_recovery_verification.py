"""Committed recovery bookkeeping and PowerShell encoding without model calls."""
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from recovery_verification import verified_records,reconcile
from windows_scripts import compatible_text
import test_git_bridge as bridge


class RecoveryVerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.path=self.root/'a.txt';self.path.write_bytes(b'confirmed')
        self.digest=hashlib.sha256(b'confirmed').hexdigest()
        self.record=dict(path='a.txt',transaction_id='a'*32,status='committed',sha256=self.digest,warnings=[])
    def tearDown(self):self.tmp.cleanup()

    def test_only_matching_committed_in_scope_execution_evidence_is_auto_confirmable(self):
        self.assertEqual(verified_records(self.root,[self.record],['a.txt'],{'a.txt':self.digest}),[self.record])
        for edit in [{'status':'uncertain'},{'warnings':['post-commit warning']},{'sha256':'0'*64},{'path':'../outside.txt'}]:
            self.assertEqual(verified_records(self.root,[{**self.record,**edit}],['a.txt'],{'a.txt':self.digest}),[])
        self.assertEqual(verified_records(self.root,[self.record],[],{'a.txt':self.digest}),[])
        self.assertEqual(verified_records(self.root,[self.record],['a.txt'],{}),[])
        self.path.write_bytes(b'changed')
        self.assertEqual(verified_records(self.root,[self.record],['a.txt'],{'a.txt':self.digest}),[])

    async def test_acknowledges_exact_verified_ids_and_rereads_remaining_records(self):
        uncertain={**self.record,'transaction_id':'b'*32,'status':'uncertain'};calls=[]
        class Session:
            async def call_tool(inner,name,args):
                calls.append((name,args))
                if name=='acknowledge_deepseek_mutations':return json.dumps({'ok':True})
                return json.dumps({'ok':True,'pending':[uncertain]})
        remaining,confirmed=await reconcile(Session(),self.root,[self.record,uncertain],['a.txt'],{'a.txt':self.digest},lambda r:r)
        self.assertEqual(calls[0][1],{'transaction_ids':['a'*32]});self.assertEqual(len(calls),2)
        self.assertEqual(confirmed,[self.record]);self.assertEqual(remaining,[uncertain])

    async def test_failed_ack_never_claims_success(self):
        class Session:
            async def call_tool(inner,*args):return '{"ok":false}'
        with self.assertRaises(RuntimeError):await reconcile(Session(),self.root,[self.record],['a.txt'],{'a.txt':self.digest},lambda r:r)

    def test_coordinator_requires_real_current_user_approval_before_auto_recovery(self):
        from types import SimpleNamespace
        from coordination import Coordinator
        from store import Store
        from workflow_state import new_state,adopt,accept_user_approval
        store=Store(self.root/'state')
        try:
            rid=store.create('Recovery',str(self.root))['id'];policy=store.policy(rid,'only approved writes')
            mid=store.message(rid,'you','work');state=new_state('work',mid,policy)
            original=store.message(rid,'worker','完整方案')
            plan=adopt(state,store,rid,{'plan_id':'P1','message_id':original},{'worker'})
            state['awaiting_approval']=plan['ref'];mid=store.message(rid,'you','同意');approval=accept_user_approval(state,store,rid,mid)
            state['evidence']=[dict(id='E01',kind='execute',agent='worker',status='failed',bound_to={'plan':plan['ref']},file_hashes={'a.txt':self.digest,'outside.txt':'0'*64})]
            store.save_workflow(rid,state,0)
            settings={'members':[{'id':'worker','name':'Worker'}]};c=Coordinator(SimpleNamespace(store=store),rid,self.root,settings)
            action=dict(agents=['worker'],plan_ref=plan['ref'],approval_ids=[approval],policy_revision=policy['revision'],allowed_paths=['a.txt'])
            scoped=c.recovery_settings('worker',action)
            self.assertEqual(scoped['_recovery_verified_hashes'],{'a.txt':self.digest})
            for edit in [{'approval_ids':['invented']},{'policy_revision':-1},{'agents':['someone-else']}]:
                self.assertNotIn('_recovery_verified_hashes',c.recovery_settings('worker',{**action,**edit}))
        finally:store.db.close()

    async def test_preflight_reconciles_verified_records_in_same_workspace_session(self):
        import preflight
        from engines import preferences,read_response
        pending=[self.record];calls=[]
        class Reply:
            isError=False
            def __init__(inner,value):inner.content=[type('Block',(),{'type':'text','text':json.dumps(value)})()]
        class Session:
            async def call_tool(inner,name,args):
                calls.append(name)
                if name=='executor_health':return Reply({'ok':True,'max_output_tokens':32768,'max_turns':99})
                if name=='acknowledge_deepseek_mutations':pending.clear();return Reply({'ok':True})
                return Reply({'ok':True,'pending':list(pending)})
        @asynccontextmanager
        async def session(*args,**kwargs):yield Session()
        settings={'_recovery_allowed_paths':['a.txt'],'_recovery_verified_hashes':{'a.txt':self.digest}}
        with patch.object(preflight,'deepseek_session',session):
            rows=await preflight.executor_checks(self.root,{'backend':'deepseek','model':'fixture'},settings)
        self.assertTrue(all(r['ok'] for r in rows));self.assertEqual(rows[-1]['acknowledged_records'],[self.record])
        self.assertEqual(calls,['executor_health','get_deepseek_recovery','acknowledge_deepseek_mutations','get_deepseek_recovery'])


class PowerShellWriteTests(unittest.TestCase):
    def test_bom_is_idempotent_and_other_file_types_are_unchanged(self):
        content="# 中文注释\nWrite-Output '正确'\n"
        self.assertEqual(compatible_text('install.PS1',content),'\ufeff'+content)
        self.assertEqual(compatible_text('install.ps1','\ufeff'+content),'\ufeff'+content)
        self.assertEqual(compatible_text('source.py',content),content)


class HardenedPowerShellWriteTests(unittest.TestCase):
    setUp=bridge.ExecutorBridgeTests.setUp
    tearDown=bridge.ExecutorBridgeTests.tearDown
    call=bridge.ExecutorBridgeTests.call

    def test_real_isolated_write_commits_bom_and_journal_matches_actual_file(self):
        from deepseek_mcp.config import Config
        from deepseek_mcp.transaction_recovery import query_with_lease,acknowledge_with_lease
        from deepseek_mcp.provider_retry import MutationOutcomeError
        text="# 中文注释\nWrite-Output '正确'\n"
        try:self.call(self.root,'Write',{'path':'install.ps1','content':text})
        except MutationOutcomeError:pass  # A committed outcome still requires exact host acknowledgement.
        raw=(self.root/'install.ps1').read_bytes();self.assertEqual(raw,b'\xef\xbb\xbf'+text.encode('utf-8'))
        cfg=Config('unused-no-network',self.root.resolve())
        records=query_with_lease(cfg)
        self.assertTrue(records);self.assertTrue(all(r['status']=='committed' and r['sha256']==hashlib.sha256(raw).hexdigest() for r in records))
        acknowledge_with_lease(cfg,[r['transaction_id'] for r in records])
        try:self.call(self.root,'Edit',{'path':'install.ps1','old_string':'正确','new_string':'已修复'})
        except MutationOutcomeError:pass
        edited=(self.root/'install.ps1').read_bytes()
        self.assertEqual(edited,b'\xef\xbb\xbf'+text.replace('正确','已修复').encode('utf-8'))
        records=query_with_lease(cfg)
        self.assertTrue(records);self.assertTrue(all(r['status']=='committed' and r['sha256']==hashlib.sha256(edited).hexdigest() for r in records))
        acknowledge_with_lease(cfg,[r['transaction_id'] for r in records])
        if __import__('os').name=='nt':
            cmd="$tokens=$null;$errors=$null;[System.Management.Automation.Language.Parser]::ParseFile('install.ps1',[ref]$tokens,[ref]$errors)|Out-Null;if($errors){exit 1}"
            parsed=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',cmd],cwd=self.root,capture_output=True)
            self.assertEqual(parsed.returncode,0,parsed.stderr)
