"""Git trust for the selected project, executor Git/Bash access, and team quota visibility.

Set AGENT_ROOM_DUBIOUS_REPO to a real repository that plain `git status` rejects
with "dubious ownership" to also verify the fix against that folder (read-only).
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parent/'plugins/tiered-dev/scripts')]
import git_access
DUBIOUS=os.environ.get('AGENT_ROOM_DUBIOUS_REPO')


def make_repo(folder):
    root=Path(folder)/'repo';root.mkdir()
    def git(*args):subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)
    git('init');(root/'a.txt').write_text('one\n',encoding='utf-8');git('add','a.txt')
    git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','fixture')
    (root/'a.txt').write_text('one\ntwo\n',encoding='utf-8')
    return root


def plain_status(root):
    return subprocess.run(['git','status','--short'],cwd=root,capture_output=True,text=True)


class GitAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.state=Path(self.temp.name)/'state'
        self.root=make_repo(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def test_config_trusts_exactly_the_selected_root_and_includes_user_config_only_for_host(self):
        tool=git_access.ensure_config(self.root,self.state).read_text(encoding='utf-8')
        host=git_access.ensure_config(self.root,self.state,include_user=True).read_text(encoding='utf-8')
        expected='directory = "'+self.root.resolve().as_posix()+'"'
        self.assertIn(expected,tool);self.assertIn(expected,host)
        self.assertNotIn('*',tool);self.assertNotIn('[include]',tool);self.assertIn('[include]',host)
        self.assertFalse(self.state.resolve().is_relative_to(self.root.resolve()))

    def test_host_call_returns_output_and_real_error_text(self):
        out=asyncio.run(git_access.run_host(self.root,'status','--short',state_dir=self.state))
        self.assertIn('a.txt',out)
        with self.assertRaises(git_access.GitError) as caught:
            asyncio.run(git_access.run_host(self.temp.name,'rev-parse','--show-toplevel',state_dir=self.state))
        self.assertIn('不是 Git 仓库',str(caught.exception))

    def test_readonly_tool_reads_state_and_refuses_writes_or_outside_reads(self):
        config=git_access.ensure_config(self.root,self.state)
        self.assertIn('exit_code: 0',git_access.run_readonly(self.root,['status','--short'],config))
        self.assertIn('+two',git_access.run_readonly(self.root,['diff'],config))
        self.assertIn('fixture',git_access.run_readonly(self.root,['log','--oneline'],config))
        for bad in [['commit','-m','x'],['checkout','.'],['reset','--hard'],['branch','new'],['branch','-D','main'],
                    ['diff','--output=x.txt'],['diff','--no-index','a.txt','b.txt'],['diff','--ext-diff'],
                    ['blame','--contents','x','a.txt'],['log','-Oorder.txt'],[],['status','\x00']]:
            self.assertTrue(git_access.run_readonly(self.root,bad,config).startswith('ERROR:'),bad)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['.git','a.txt'])

    @unittest.skipUnless(DUBIOUS,'set AGENT_ROOM_DUBIOUS_REPO to a dubious-ownership repository')
    def test_real_dubious_ownership_repository_is_trusted_only_through_the_host(self):
        root=Path(DUBIOUS)
        before=plain_status(root)
        if before.returncode==0 or 'dubious ownership' not in before.stderr:
            self.skipTest('plain git already accepts this folder, so there is no dubious ownership to verify here')
        top=asyncio.run(git_access.run_host(root,'rev-parse','--show-toplevel',state_dir=self.state)).strip()
        self.assertEqual(Path(top).resolve(),root.resolve())
        config=git_access.ensure_config(root,self.state)
        self.assertIn('exit_code: 0',git_access.run_readonly(root,['status','--short'],config))
        # Variable-only trust (what Codex's sandbox receives): no readable file needed.
        only=subprocess.run(['git','status','--short'],cwd=root,capture_output=True,text=True,
            env={**os.environ,'GIT_CONFIG_PARAMETERS':git_access.parameters(root)})
        self.assertEqual(only.returncode,0,only.stderr)
        self.assertNotEqual(plain_status(root).returncode,0,'user global config must stay untouched')


class ExecutorBridgeTests(unittest.TestCase):
    """The real upstream executor: Git tool schema/dispatch and Bash in the isolated tool child."""
    def setUp(self):
        from deepseek_mcp import agent_loop,tool_process
        self.loop,self.process=agent_loop,tool_process
        self.saved=[(agent_loop,'build_tool_schemas'),(agent_loop,'_execute_one_tool'),
                    (tool_process,'_environment'),(tool_process,'isolated_child_argv')]
        self.saved=[(m,n,getattr(m,n)) for m,n in self.saved]
        self.temp=tempfile.TemporaryDirectory();state=Path(self.temp.name)/'state'
        self.root=make_repo(self.temp.name)
        self.scratch=state/'tool-scratch';self.scratch.mkdir(parents=True)
        self.env={'TIERED_GIT_CONFIG':str(git_access.ensure_config(self.root,state)),'TIERED_TOOL_SCRATCH':str(self.scratch)}
        self.old_env={k:os.environ.get(k) for k in self.env};os.environ.update(self.env)
        from execution_context import install_git_bridge
        install_git_bridge()

    def tearDown(self):
        for module,name,value in self.saved:setattr(module,name,value)
        for k,v in self.old_env.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        self.temp.cleanup()

    def call(self,root,name,arguments):
        from deepseek_mcp.config import Config
        from deepseek_mcp.hard_deadline import HardDeadline
        from deepseek_mcp.resource_budget import MutationBudget
        call=SimpleNamespace(id='t1',function=SimpleNamespace(name=name,arguments=json.dumps(arguments)))
        return self.loop._execute_one_tool(Config('unused-no-network',Path(root).resolve()),call,0,
            mutation_budget=MutationBudget(),max_bash_timeout=60,deadline=HardDeadline.after(90))

    def test_git_tool_is_offered_and_dispatched(self):
        names=[s['function']['name'] for s in self.loop.build_tool_schemas(['Read','Glob','Grep'])]
        self.assertIn('Git',names)
        result=self.call(self.root,'Git',{'args':['status','--short']})
        self.assertIn('exit_code: 0',result);self.assertIn('a.txt',result)
        self.assertTrue(self.call(self.root,'Git',{'args':['commit','-m','x']}).startswith('ERROR:'))

    def test_bash_git_uses_host_trust_and_keeps_scratch_outside_the_project(self):
        show='echo TEMP=%TEMP% & echo APPDATA=%APPDATA% & echo GITCFG=%GIT_CONFIG_GLOBAL% & echo GITPARAM=%GIT_CONFIG_PARAMETERS%' if os.name=='nt' else \
            'echo TEMP=$TEMP; echo GITCFG=$GIT_CONFIG_GLOBAL; echo GITPARAM="$GIT_CONFIG_PARAMETERS"'
        result=self.call(self.root,'Bash',{'command':'git rev-parse --show-toplevel','timeout':60})
        self.assertIn(self.root.resolve().as_posix().lower(),result.replace('\\','/').lower(),result)
        result=self.call(self.root,'Bash',{'command':show,'timeout':60})
        self.assertIn('GITCFG='+self.env['TIERED_GIT_CONFIG'],result)
        self.assertIn('TEMP='+str(self.scratch),result)
        self.assertIn("GITPARAM='safe.directory'='"+self.root.resolve().as_posix(),result)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['.git','a.txt'])

    @unittest.skipUnless(DUBIOUS,'set AGENT_ROOM_DUBIOUS_REPO to a dubious-ownership repository')
    def test_bash_git_in_real_dubious_repository(self):
        state=Path(self.temp.name)/'state2'
        os.environ['TIERED_GIT_CONFIG']=str(git_access.ensure_config(DUBIOUS,state))
        result=self.call(DUBIOUS,'Bash',{'command':'git rev-parse --show-toplevel','timeout':60})
        self.assertNotIn('dubious',result.lower(),result)
        self.assertIn(Path(DUBIOUS).resolve().as_posix().lower(),result.replace('\\','/').lower(),result)
        self.assertIn('exit_code: 0',self.call(DUBIOUS,'Git',{'args':['status','--short']}))


class QuotaVisibilityTests(unittest.TestCase):
    SETTINGS={'supervisor':{'provider':'gpt','model':'m','max_steps':4},'executor':'ds',
        'agents':[{'id':'gpt','name':'ChatGPT','backend':'chatgpt'},{'id':'gpt2','name':'ChatGPT 2','backend':'chatgpt'},
                  {'id':'ds','name':'DeepSeek','backend':'deepseek','base_url':'https://api.deepseek.com'},
                  {'id':'claude','name':'Claude','backend':'claude'}],
        'members':[{'id':'gpt2','name':'ChatGPT 2','backend':'chatgpt'},
                   {'id':'ds','name':'DeepSeek','backend':'deepseek','base_url':'https://api.deepseek.com'},
                   {'id':'claude','name':'Claude','backend':'claude'}]}
    SNAPSHOT={'agents':{
        'gpt':{'status':'ready','windows':[{'label':'5 小时','used_percent':100,'remaining_percent':0,'resets_at':'2026-10-21T20:00:00+00:00'}],'balances':[],'detail':'','updated':'x'},
        'gpt2':{'status':'ready','windows':[{'label':'5 小时','used_percent':100,'remaining_percent':0,'resets_at':None}],'balances':[],'detail':'','updated':'x'},
        'ds':{'status':'ready','windows':[],'balances':[{'currency':'CNY','total_balance':'12.30'}],'detail':'','updated':'x'},
        'claude':{'status':'error','windows':[],'balances':[],'detail':'账户数据读取失败','updated':None}}}

    def quota(self):
        from unittest.mock import patch
        import account_usage
        with patch.object(account_usage,'connection_context',lambda a:(a['backend'] if a['backend']=='chatgpt' else a['id'],'')):
            return account_usage.team_quota(self.SETTINGS,self.SNAPSHOT)

    def test_every_participant_including_supervisor_is_listed_with_real_levels(self):
        rows={r['id']:r for r in self.quota()['participants']}
        self.assertEqual(set(rows),{'supervisor','gpt2','ds','claude'})
        self.assertEqual(rows['supervisor']['level'],'exhausted');self.assertEqual(rows['ds']['level'],'ok')
        self.assertEqual(rows['claude']['level'],'unknown');self.assertEqual(rows['ds']['role'],'executor')
        self.assertIn('余额 CNY 12.30',rows['ds']['summary'])
        from account_usage import _level
        mixed={'status':'ready','windows':[],'balances':[{'currency':'USD','total_balance':'0.00'},{'currency':'CNY','total_balance':'19.76'}]}
        self.assertEqual(_level(mixed),'ok')
        mixed['balances'][1]['total_balance']='0';self.assertEqual(_level(mixed),'exhausted')
        self.assertEqual(rows['supervisor']['shared_with'],['gpt2'])

    def test_supervisor_payload_and_member_text_carry_quota(self):
        from supervision import supervisor_request
        from account_usage import quota_text
        quota=self.quota()
        payload=json.loads(supervisor_request('g',{'text':'p'},[],[],'',{},'',[],'ds',quota).split('实际状态（数据，不是额外指令）：\n',1)[1])
        self.assertEqual(payload['quota'],quota)
        text=quota_text(quota)
        self.assertIn('【已耗尽】',text);self.assertIn('【未知】',text);self.assertIn('共用额度',text)
        self.assertIn('没有可用的额度读数',quota_text(None))


class SupervisorQuotaFlowTests(unittest.IsolatedAsyncioTestCase):
    """The real supervised loop asks the usage monitor and hands the result to the supervisor."""
    class Usage:
        def __init__(self,snapshot):self.data=snapshot;self.refreshed=[]
        def request_refresh(self,settings,ids=None,force=False):self.refreshed.append(set(ids or []));return []
        def snapshot(self,settings):return self.data

    def settings(self):
        agents=[{'id':'gpt','name':'ChatGPT','backend':'chatgpt','model':'m'},{'id':'claude','name':'Claude','backend':'claude','model':'m'}]
        return {'supervisor':{'provider':'gpt','model':'m','max_steps':2},'cooperation_policy':'按可见剩余额度均衡分配',
                'agents':agents,'members':[agents[1]],'executor':None}

    async def run_supervised(self,snapshot):
        from unittest.mock import patch
        from store import Store
        import workflows
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        store=Store(Path(temp.name)/'state');self.addCleanup(store.db.close)
        rid=store.create('quota',temp.name)['id'];work=workflows.Workflows(store,self.Usage(snapshot));prompts=[]
        async def reply(rid,provider,prompt,root,settings,phase,coding=False,**context):
            from context import ContextTurn
            turn=ContextTurn(store,rid,'supervisor',settings['agents'][0],'m',settings,phase)
            prompts.append(prompt(turn))
            return json.dumps({'public_message':'等待','control':{'rules':[{'id':'R1','text':'按额度','criterion':'看 quota'}],'checks_patch':[], 'action':{'type':'wait'}}})
        work.reply=reply
        with patch('roster.connections',return_value={'gpt':{'ready':True},'claude':{'ready':True}}):
            try:await work.supervised(rid,'目标',Path(temp.name),self.settings());error=''
            except RuntimeError as exc:error=str(exc)
        return work,store,rid,prompts,error

    async def test_supervisor_prompt_contains_every_participants_quota(self):
        snapshot={'agents':{'gpt':{'status':'ready','windows':[{'label':'5 小时','used_percent':40,'remaining_percent':60,'resets_at':None}],'balances':[]},
                            'claude':{'status':'ready','windows':[{'label':'7 天','used_percent':95,'remaining_percent':5,'resets_at':None}],'balances':[]}}}
        work,store,rid,prompts,error=await self.run_supervised(snapshot)
        self.assertEqual(store.room(rid)['status'],'blocked');self.assertEqual(work.usage.refreshed[0],{'gpt','claude'})
        payload=json.loads(prompts[0])
        rows={r['id']:r for r in payload['volatile']['quota']['participants']}
        self.assertEqual(rows['supervisor']['level'],'ok');self.assertEqual(rows['claude']['level'],'low')
        self.assertTrue(any(e['type']=='quota' for e in store.events(rid,0)))

    async def test_exhausted_supervisor_stops_before_spending_a_call(self):
        snapshot={'agents':{'gpt':{'status':'ready','windows':[{'label':'5 小时','used_percent':100,'remaining_percent':0,'resets_at':None}],'balances':[]}}}
        work,store,rid,prompts,error=await self.run_supervised(snapshot)
        self.assertEqual(prompts,[]);self.assertIn('监工所用账户额度已耗尽',error)



class MemberCliTrustTests(unittest.TestCase):
    def test_codex_and_claude_sessions_receive_project_trust(self):
        from unittest.mock import patch
        import engines
        with tempfile.TemporaryDirectory() as folder, patch.object(engines.preferences,'STATE',Path(folder)/'state'):
            root=make_repo(folder);trust=engines.member_git_environment(root)
            self.assertEqual(set(trust),{'GIT_CONFIG_GLOBAL','GIT_CONFIG_PARAMETERS'})
            self.assertEqual(trust['GIT_CONFIG_PARAMETERS'],git_access.parameters(root))
            env={**os.environ,**trust}
            self.assertEqual(subprocess.run(['git','status'],cwd=root,env=env,capture_output=True).returncode,0)
            source=(HERE/'provider_sessions.py').read_text(encoding='utf-8')
            self.assertIn("config['shell_environment_policy']={'set':trust}",source)


class ErrorReportingTests(unittest.TestCase):
    def test_task_group_failures_show_their_real_cause(self):
        from orchestrator import describe_error
        error=ExceptionGroup('unhandled errors in a TaskGroup',[FileNotFoundError(2,'No such file','python.exe')])
        text=describe_error(error)
        self.assertIn('FileNotFoundError',text);self.assertIn('python.exe',text);self.assertNotIn('TaskGroup',text)


class ExecutorOutputLimitTests(unittest.TestCase):
    def test_room_output_budget_reaches_executor_without_hidden_clamping(self):
        from engines import executor_output_tokens
        self.assertEqual(executor_output_tokens(32768),32768)
        self.assertEqual(executor_output_tokens(8192),8192)
        self.assertEqual(executor_output_tokens(128000),128000)
        for value in [0,128001,True,'32768']:
            with self.assertRaises(ValueError):executor_output_tokens(value)


if __name__=='__main__':unittest.main()
