"""Durable supervisor state machine; provider conversations stay in their adapters."""
import asyncio
import copy
import difflib
import json
from pathlib import Path

import roster
from engines import build_snapshot
from preflight import preflight,code_hash,fingerprint,render,is_resume_only,RESUME_BUTTON,check
from supervision import validate_control,supervisor_payload
from workflow_state import (new_state,add_evidence,refresh,current_refs,adopt,valid_plan,approvals_cover,
    accept_user_approval,accept_button_approval,record_vote,open_questions,TOLERANCE,digest,delivery_verified)
from store import now,WorkflowConflict
from google_billing import BillingUnavailable


class Coordinator:
    def __init__(self,work,rid,root,settings):
        self.work,self.store,self.rid,self.root,self.settings=work,work.store,rid,root,settings
        self.state,self.revision=self.store.load_workflow(rid);self.saved=copy.deepcopy(self.state)
        self.labels=roster.names(settings)

    def save(self):
        for _ in range(3):
            try:
                self.revision=self.store.save_workflow(self.rid,self.state,self.revision)
                self.saved=copy.deepcopy(self.state);return
            except WorkflowConflict:
                current,revision=self.store.load_workflow(self.rid)
                changes={k:v for k,v in self.state.items() if not self.saved or self.saved.get(k)!=v}
                if any(current.get(k)!=(self.saved or {}).get(k) and current.get(k)!=v for k,v in changes.items()):raise
                self.state={**current,**changes};self.revision=revision;self.saved=copy.deepcopy(current)
        raise WorkflowConflict('工作流反复更新，请重试。')

    async def bindings(self):
        policy=self.store.policy(self.rid,self.settings['cooperation_policy'])
        refresh(self.state,policy,await code_hash(self.root));self.state['policy_text']=policy['text']
        self.state['consensus_members']=[m['id'] for m in self.settings['members'] if m['id']!=self.settings.get('executor')]
        self.save();return policy

    def evidence(self,record):
        record['bound_to']=current_refs(self.state)
        result=add_evidence(self.state,self.store,self.rid,record);self.save();return result

    def host_error(self,checks,action=None,runtime=None):
        failed=[c for c in checks if not c['ok']]
        for c in checks:
            if c['name']=='recovery' and 'records' in c:self.state['recovery_records']=c['records']
        self.state['blocker']={'checks':checks,'fingerprint':fingerprint(failed),'since':now()}
        if runtime is not None:self.state['blocker']['runtime']=runtime
        if action is not None:self.state['pending_action']=action
        self.state['status']='blocked';self.save()
        self.store.message(self.rid,'host',render(checks),phase='宿主执行故障' if runtime else '宿主环境错误',status='host_error',
            actions=[{'id':'recheck','label':'仅复查宿主' if runtime else '复查并继续'}])
        self.store.status(self.rid,'blocked')

    def billing_block(self,error,undo_round=False):
        if undo_round:self.state['step']=max(0,self.state['step']-1)
        checks=[check('google_billing',False,str(error),'account')]
        previous=self.state.get('blocker') or {}
        if previous.get('billing') and previous.get('fingerprint')==fingerprint(checks):
            self.save();self.store.status(self.rid,'blocked');return
        self.host_error(checks)
        self.state['blocker']['billing']=True;self.save()

    def billing_ready(self,identities=None):
        try:roster.check_billing(self.settings,identities)
        except BillingUnavailable as exc:self.billing_block(exc);return False
        return True

    def balance_notices(self,quota):
        seen=self.state.setdefault('balance_notice_ids',[])
        for alert in (quota or {}).get('alerts',[]):
            if alert['id'] in seen:continue
            self.store.message(self.rid,'system','、'.join(alert['agents'])+'：'+alert['text']+
                ' 上次成功更新时间：'+str(alert.get('updated') or '未知'),phase='余额缓存提醒')
            seen.append(alert['id'])
        self.state['balance_notice_ids']=seen[-100:];self.save()

    def submit_approval(self,plan,judgements=(),notice=None):
        from approval_documents import create,action as document_action
        self.state['awaiting_approval']=plan['ref'];self.state['status']='awaiting_approval'
        document=create(self.store,self.rid,self.state,self.settings,judgements)
        self.state['approval_document_id']=document['id'];self.save()
        self.store.message(self.rid,'system',notice or '方案审批文档已生成。请打开文档核对完整方案后批准。',phase='确认方案',actions=[document_action(document)])
        self.store.status(self.rid,'awaiting_approval')

    async def execution_fault(self,execution,action=None,*,recheck=False):
        """Inspect a stopped attempt locally. Never resubmit its job or ask a model to recover it."""
        from execution_faults import runtime_fault,describe_fault
        import hashlib
        fault=execution.get('runtime_fault') or runtime_fault(execution.get('error',''))
        runtime={'execution_id':execution['id'],'fault':fault}
        rows=[]
        try:
            rows=await preflight(self.root,self.recovery_settings(execution['agent'],action),agent=execution['agent'])
            self.record_environment_checks(rows)
            scope=self.scope_check()
            if scope:rows.append(scope)
            same=True
            for name,expected in execution.get('file_hashes',{}).items():
                path=(self.root/name).resolve()
                if not path.is_relative_to(self.root):same=False;break
                actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                if actual!=expected:same=False
            rows.append(check('execution_files',same,'当前文件与执行结束时的哈希一致' if same else '文件已变化；原执行证据不能证明当前交付','host'))
            expected=execution.get('acceptance_commands',[]);tests=execution.get('tests',[])
            verified=delivery_verified(self.state,execution)
            # Reuse host acceptance already run by verify_execution before a later
            # recovery/transport failure. Do not run the same passing tests again.
            if same and not verified and expected and len(tests)==len(expected) and {t['command'] for t in tests}==set(expected) and all(t['exit_code']==0 for t in tests) and execution.get('bound_to')==current_refs(self.state):
                self.evidence({'kind':'check','agent':'host','status':'complete','tests':tests,'tests_run':True,
                    'verifies_execution':execution['id'],'source':'recorded_host_acceptance'})
                verified=True
            names=['git_status','git_diff_stat']
            if same and not verified and expected and (recheck or fault['stage']!='verification'):
                names+=['acceptance:'+str(i+1) for i in range(len(expected))]
            diagnostics=await self.host_check({'checks':names})
            rows.extend(c for c in diagnostics['output'] if not c['ok'])
            verified=delivery_verified(self.state,execution)
            rows.append(check('delivery_acceptance',verified,'原契约全部验收已由宿主核实；执行器异常原样保留' if verified else '交付尚未通过原契约全部验收','host'))
            if same and verified and all(c['ok'] for c in rows):
                self.state.setdefault('runtime_resolutions',{})[execution['id']]={'bound_to':current_refs(self.state),'at':now()}
                self.state.update(blocker=None,pending_action=None,status='waiting');self.save()
                self.store.message(self.rid,'host',render(rows)+'\n宿主核对完成。本轮未调用模型；执行器原有失败记录保留，后续可继续正常协作。',phase='宿主故障核对完成')
                self.store.status(self.rid,'blocked');return
        except Exception as exc:
            from orchestrator import describe_error
            rows.append(check('host_diagnostics',False,describe_error(exc)[:1800],'host'))
        rows.append(check('executor_runtime',False,describe_fault(fault)+'\n原始错误：'+execution.get('error','执行器运行异常'),'host'))
        if action is not None:action.update(started=True,attempt_state='host_runtime_blocked')
        self.host_error(rows,action,runtime)

    def record_environment_checks(self,checks):
        self.state['environment_checks']=checks
        for c in checks:
            if c['name']=='recovery' and 'records' in c:self.state['recovery_records']=c['records']
            if c.get('initialized'):
                self.store.message(self.rid,'host',c['detail'],phase='项目环境准备')
            if c.get('acknowledged_records'):
                self.store.message(self.rid,'host','已核对并确认成功落盘的文件：'+', '.join(r['path'] for r in c['acknowledged_records'])+'。文件哈希与批准范围及上次执行证据一致；这仅确认写入结果，不代表功能验收通过。',phase='写入恢复核对')
        self.save()

    def recovery_settings(self,agent,action=None):
        action=action or self.state.get('pending_action')
        if not action or action.get('agents')!=[agent] or action.get('policy_revision')!=self.state['policy_revision']:return self.settings
        try:approvals_cover(self.state,self.store,self.rid,action)
        except (ValueError,KeyError):return self.settings
        previous=next((e for e in reversed(self.state['evidence']) if e.get('kind')=='execute' and e.get('agent')==agent and e.get('status')=='failed' and e.get('bound_to',{}).get('plan')==self.state.get('current_plan')),None)
        if not previous or not previous.get('file_hashes'):return self.settings
        allowed=action.get('allowed_paths',[])
        hashes={name:digest for name,digest in previous['file_hashes'].items() if name in allowed and digest}
        return {**self.settings,'_recovery_allowed_paths':allowed,'_recovery_verified_hashes':hashes}

    def pending_valid(self):
        from workflows import contract
        a=self.state.get('pending_action')
        if not a or a.get('started') or a.get('policy_revision')!=self.state['policy_revision']:return False
        if self.settings.get('executor') and a['agents']!=[self.settings['executor']]:return False
        try:
            approvals_cover(self.state,self.store,self.rid,a)
            source=roster.member(self.settings,a['agents'][0])
            from model_catalog import connection_context
            connection,_=connection_context(source)
            return a['contract_hash']==digest(contract(a['task'])) and a['allowed_paths']==a['task']['allowed_paths'] and a['executor_binding']==digest([source['id'],source['model'],source.get('effort'),connection])
        except (ValueError,KeyError):return False

    def scope_check(self):
        violation=self.state.get('scope_violation')
        if not violation:return None
        remaining=[]
        for name,expected in violation['before'].items():
            path=(self.root/name).resolve()
            if not path.is_relative_to(self.root):remaining.append(name);continue
            actual=None
            if path.is_file():
                import hashlib
                actual=hashlib.sha256(path.read_bytes()).hexdigest()
            if actual!=expected:remaining.append(name)
        if remaining:return check('contract_scope',False,'需恢复契约外变更后再继续：'+', '.join(remaining),'host')
        self.state['scope_violation']=None
        pending=self.state.get('pending_action')
        if pending:
            pending['started']=False;pending['attempt_state']='scope_restored'
        self.save();return check('contract_scope',True,'契约外变更已恢复','host')

    async def execute(self,a,quota=None,checked=False):
        scope=self.scope_check()
        if scope and not scope['ok']:self.host_error([scope],a);return False
        if not checked:
            checks=await preflight(self.root,self.recovery_settings(a['agents'][0],a),quota,a['agents'][0])
            self.record_environment_checks(checks)
            if any(not c['ok'] for c in checks):self.host_error(checks,a);return False
        self.state['pending_action']=a;self.state['blocker']=None;self.save()
        if self.settings.get('pause_before_execution'):
            event=asyncio.Event();self.work.resume[self.rid]=event
            self.store.status(self.rid,'paused')
            self.store.message(self.rid,'system','契约已准备，点击“继续执行”开始修改。',phase='修改前暂停')
            await event.wait();self.work.resume.pop(self.rid,None);self.store.status(self.rid,'running')
            await self.bindings()
            if not self.pending_valid():raise ValueError('暂停期间契约、批准或设置已变化，请重新安排。')
        a['started']=True;self.save()
        from orchestrator import ExecutionScopeViolation
        try:result=await self.work.execute_supervised(self.rid,a['agents'][0],a['task'],self.root,self.settings)
        except ExecutionScopeViolation as exc:
            await self.bindings();self.evidence(exc.record)
            # Only this known, stopped attempt is retryable after exact scope restoration.
            # Arbitrary interrupted/started calls remain ineligible for automatic rerun.
            a['started']=False;a['attempt_state']='scope_blocked'
            self.state['scope_violation']={'before':exc.before};self.save()
            self.host_error([self.scope_check()],a);return False
        if result.get('host_error'):
            if not result.get('started_execution',result.get('runtime_fault',{}).get('started',False)):
                a['started']=False
                self.host_error([check('executor_start',False,result.get('error','执行器启动失败'),'mcp')],a);return False
            await self.bindings()
            result['message_id']=next((m['id'] for m in reversed(self.store.messages(self.rid)) if m['provider']==result['agent']),None)
            record=self.evidence(result)
            await self.execution_fault(record,a);return False
        await self.bindings()
        result['message_id']=next((m['id'] for m in reversed(self.store.messages(self.rid)) if m['provider']==result['agent']),None)
        self.evidence(result);self.state['pending_action']=None;self.save();return True

    def prepare(self,action):
        from workflows import validate_plan,contract
        plan=approvals_cover(self.state,self.store,self.rid,action)
        if self.settings.get('executor') and action['agents']!=[self.settings['executor']]:raise ValueError('必须派给本群指定执行者。')
        task=copy.deepcopy(validate_plan({'tasks':[action.get('task',{})]},require_acceptance=False)['tasks'][0])
        task['id']=f"T-{1+sum(e['kind']=='execute' for e in self.state['evidence']):02d}"
        task['context']=str(task.get('context',''))+'\n监工本轮要求：'+action.get('instruction','')+'\n方案 '+plan['ref']+'（原文）：\n'+plan['text']
        source=roster.member(self.settings,action['agents'][0])
        from model_catalog import connection_context
        connection,_=connection_context(source)
        return {**action,'task':task,'contract_hash':digest(contract(task)),'allowed_paths':task['allowed_paths'][:],
            'policy_revision':self.state['policy_revision'],'executor_binding':digest([source['id'],source['model'],source.get('effort'),connection]),'started':False}

    def plan_for(self,turn,ref):
        plan=valid_plan(self.state,self.store,self.rid,ref);key='plan:'+plan['plan_id']
        seen=turn.asset(key,ref)
        if seen==ref:return '当前方案 '+ref+'（本分支已收到全文，sha '+plan['sha256']+'）'
        if seen:
            old=self.store.plan(self.rid,seen)
            return '方案差异 '+seen+' → '+ref+'：\n'+''.join(difflib.unified_diff(old['text'].splitlines(True),plan['text'].splitlines(True),fromfile=seen,tofile=ref))
        return '方案 '+ref+'（sha '+plan['sha256']+'）：\n'+plan['text']

    async def ask(self,action,policy,*,phase='协作发言'):
        from discussion_resolution import FINAL_PHASE
        current=self.state.get('current_plan')
        if current and self.state['discussion_rounds'].get(current.split('@')[0],0)>=TOLERANCE[self.settings.get('discussion_tolerance',3)][0] and phase!=FINAL_PHASE:
            raise ValueError('讨论上限已到，宿主将强制由最后反对者提交完整方案并直接送审，不能再开启新一轮讨论。')
        kind=action['kind'];ref=action.get('plan_ref');cap=None
        if kind in ['review','vote']:
            plan=valid_plan(self.state,self.store,self.rid,ref);pid=plan['plan_id']
            maximum,cap=TOLERANCE[self.settings.get('discussion_tolerance',3)]
            if self.state['discussion_rounds'].get(pid,0)>=maximum:raise ValueError('已达讨论容忍度：只能指定一位成员整合最终版（propose），或把分歧交用户。')
        elif self.state.get('current_plan') and self.state['discussion_rounds'].get(self.state['current_plan'].split('@')[0],0)>=TOLERANCE[self.settings.get('discussion_tolerance',3)][0] and kind=='propose' and len(action['agents'])!=1:
            raise ValueError('达到讨论上限后只能由一位成员整合最终版。')
        files=action.get('files',[])
        from workflows import validate_plan,project_context
        if not isinstance(files,list):raise ValueError('files 必须是相对文件列表。')
        if files:validate_plan({'tasks':[{'goal':'Read materials','allowed_paths':files,'acceptance_commands':[]}]},False)
        snapshot=''
        if kind=='propose' or kind=='review' and files:
            snapshot,_,_=await asyncio.to_thread(build_snapshot,self.root,files or None)
        project=await asyncio.to_thread(project_context,self.root) if kind=='propose' else ''
        boundary=self.store.boundary(self.rid) if action.get('parallel') else None
        self.state['last_ask']={'agents':action['agents'],'boundary':self.store.boundary(self.rid),'kind':kind,'plan_ref':ref};self.save()
        counted_round=False;billing_errors=[]

        async def one(agent):
            nonlocal counted_round
            isolated=agent==self.settings.get('executor')
            def materials(turn):
                turn.kind=kind
                from optimization_settings import active
                if active(turn.settings,'summary'):
                    from member_summary import critical_ids
                    turn.required_message_ids=sorted(critical_ids(self.state)|set(action.get('refs',[])))
                prompt='监工的只读咨询：'+action['instruction'] if isolated else '用户目标：'+self.state['goal']['text']+'\n本轮 '+kind+'：'+action['instruction']
                if ref and not isolated:prompt+='\n'+self.plan_for(turn,ref)
                if kind in ['review','vote']:
                    if not isolated:prompt+='\n尚未解决的问题（宿主记录）：'+json.dumps(open_questions(self.state),ensure_ascii=False)
                    prompt+='\n请最后单独写：表态：同意 '+ref+' 或 表态：反对 '+ref+'：理由'
                if cap:prompt+='\n本轮修订不超过 '+str(cap)+' 字；只写差异和理由。'
                if project and turn.asset('rules',digest(project))!=digest(project):prompt+='\n项目规则与索引：'+project
                if snapshot:prompt+='\n只读快照：'+snapshot
                for mid in action.get('refs',[]):
                    if isolated:raise ValueError('执行者只接受自足指令，不可展开群原文引用。')
                    msg=self.store.message_by_id(mid,self.rid)
                    if msg['status'] not in ['complete','host_error']:raise ValueError('引用原文尚未完成。')
                    prompt+='\n引用 '+mid+'（'+msg['provider']+'）：'+msg['text']
                turn.parts={'project':project+snapshot};return prompt
            try:
                answer=await self.work.reply(self.rid,agent,materials,self.root,self.settings,phase,boundary=boundary)
                msg=next(m for m in reversed(self.store.messages(self.rid)) if m['provider']==agent and m['status']=='complete')
                status='over_limit' if cap and len(answer)>cap else 'complete'
                if kind in ['review','vote'] and not counted_round:
                    counted_round=True;self.state['discussion_rounds'][pid]=self.state['discussion_rounds'].get(pid,0)+1;self.save()
                record={'agent':agent,'kind':'ask','ask_kind':kind,'status':status,'message_id':msg['id'],'plan_ref':ref}
                self.evidence(record)
                if kind in ['review','vote'] and status=='complete':
                    vote=record_vote(self.state,self.store,self.rid,agent,ref,msg['id'])
                    if vote is None and kind=='vote':
                        follow=msg;key=ref+':'+agent
                        if not self.state.setdefault('vote_reasks',{}).get(key):
                            self.state['vote_reasks'][key]=True;self.save()
                            text=await self.work.reply(self.rid,agent,'请只补一行：表态：同意 '+ref+' 或 表态：反对 '+ref+'：理由',self.root,self.settings,'补充表态')
                            follow=next(m for m in reversed(self.store.messages(self.rid)) if m['provider']==agent and m['status']=='complete')
                            vote=record_vote(self.state,self.store,self.rid,agent,ref,follow['id'],'format_after_reask')
                        if vote is None:self.state['votes'].setdefault(ref,{})[agent]={'agree':None,'source':'pending_supervisor','message_id':follow['id']}
                    self.save()
            except BillingUnavailable as exc:billing_errors.append(exc)
            except (ValueError,RuntimeError) as exc:self.evidence({'agent':agent,'kind':'ask','status':'failed','error':str(exc)[:1200],'plan_ref':ref})
        if action.get('parallel'):await asyncio.gather(*(one(a) for a in action['agents']))
        else:
            for a in action['agents']:await one(a)
        if billing_errors:raise billing_errors[0]

    async def host_check(self,action):
        from workflows import verify_commands
        import git_access
        from engines import preferences
        names=action.get('checks')
        if not isinstance(names,list) or not 1<=len(names)<=12:raise ValueError('宿主检查需列出 1 至 12 项。')
        pending=self.state.get('pending_action') or {};approved_commands=pending.get('task',{}).get('acceptance_commands',[])
        if not approved_commands:
            last=next((e for e in reversed(self.state['evidence']) if e['kind']=='execute'),{})
            if last.get('bound_to',{}).get('plan')!=self.state.get('current_plan'):raise ValueError('上一份验收契约已过期。')
            approved_commands=last.get('acceptance_commands') or [t['command'] for t in last.get('tests',[])]
        rows=[];actual_tests=[]
        for name in names:
            if name in ['git_status','git_diff_stat']:
                args=['status','--short'] if name=='git_status' else ['diff','--stat']
                try:rows.append(check(name,True,await git_access.run_host(self.root,*args,state_dir=preferences.STATE),'host'))
                except git_access.GitError as exc:rows.append(check(name,False,str(exc),'host'))
            elif name=='executor_start':rows.extend(await preflight(self.root,self.settings,agent=self.settings.get('executor') or action.get('agent')))
            elif isinstance(name,str) and name.startswith('file_exists:'):
                relative=name.split(':',1)[1];path=(self.root/relative).resolve()
                if not path.is_relative_to(self.root) or path==self.root:raise ValueError('文件检查越出项目。')
                rows.append(check(name,path.is_file(),'存在' if path.is_file() else '不存在','host'))
            elif isinstance(name,str) and name.startswith('acceptance:'):
                command=name.split(':',1)[1]
                if command.isdigit():
                    index=int(command)-1
                    if not 0<=index<len(approved_commands):raise ValueError('验收命令序号不存在。')
                    command=approved_commands[index]
                if command not in approved_commands:raise ValueError('只能重跑已获批执行契约中的验收命令。')
                result=await verify_commands(self.store,self.rid,self.root,[command]);actual_tests.extend(result)
                rows.append(check(name,result[0]['exit_code']==0,json.dumps(result,ensure_ascii=False),'host'))
            else:raise ValueError('不支持的宿主检查。')
        await self.bindings();mid=self.store.message(self.rid,'system',render(rows),phase='宿主检查')
        record={'kind':'check','agent':'host','status':'complete' if all(r['ok'] for r in rows) else 'failed','message_id':mid,'output':rows,'tests':actual_tests,'tests_run':bool(actual_tests)}
        execution=next((e for e in reversed(self.state['evidence']) if e['kind']=='execute'),None)
        if execution and record['status']=='complete' and execution.get('bound_to')==current_refs(self.state):
            expected=execution.get('acceptance_commands',[])
            if expected and len(actual_tests)==len(expected) and {t['command'] for t in actual_tests}==set(expected):
                record['verifies_execution']=execution['id']
                self.store.message(self.rid,'system','宿主已独立运行全部已批准验收并验证当前文件；执行器原有异常记录保留，未改写为成功。',phase='交付独立核实')
        return self.evidence(record)

    async def run(self,text):
        from workflows import json_reply,project_context
        policy=self.store.policy(self.rid,self.settings['cooperation_policy'])
        action_id=self.settings.get('_host_action_id')
        user=None if action_id else next((m for m in reversed(self.store.messages(self.rid)) if m['provider']=='you'),None)
        if self.state is None:
            goal=user
            if is_resume_only(text,self.settings):goal=next((m for m in self.store.messages(self.rid) if m['provider']=='you'),user)
            self.state=new_state(goal['text'] if goal else text,goal['id'] if goal else None,policy)
            legacy,next_id=self.store.legacy_evidence(self.rid)
            self.state.update(evidence=legacy,next_evidence=next_id,task_start=next_id)
            self.save()
        elif self.state.get('status')=='complete' and not is_resume_only(text,self.settings):
            previous=self.state
            self.state=new_state(text,user['id'] if user else None,policy)
            self.state.update(evidence=previous['evidence'],next_evidence=previous['next_evidence'],task_start=previous['next_evidence'])
            self.save()
        await self.bindings()
        for e in self.state['evidence']:
            if e.get('ask_kind') in ['review','vote'] and e.get('status')=='complete' and e.get('plan_ref')==self.state.get('current_plan') and e.get('message_id') and e.get('agent') not in self.state.get('votes',{}).get(e['plan_ref'],{}):
                record_vote(self.state,self.store,self.rid,e['agent'],e['plan_ref'],e['message_id'])
        self.save()
        if action_id:
            event=self.store.host_action(action_id,self.rid)
            if event['kind']=='approve' and accept_button_approval(self.state,self.store,self.rid,action_id):self.save()
            trigger={'type':'host_action','kind':event['kind'],'action_id':action_id}
        else:
            if user and accept_user_approval(self.state,self.store,self.rid,user['id']):self.save()
            trigger={'type':'user_input','text':text,'message_id':user['id'] if user else None}
        # Older releases incorrectly marked in-flight runtime failures as ordinary
        # worker failures. Catch the latest attempt before any supervisor call.
        last=next((e for e in reversed(self.state['evidence']) if e['kind']=='execute'),None)
        if is_resume_only(text,self.settings) and not self.state.get('blocker') and last and last['status']=='failed' and last.get('bound_to',{}).get('plan')==self.state.get('current_plan') and last['id'] not in self.state.get('runtime_resolutions',{}) and last['id'] not in self.state.get('runtime_superseded',[]):
            from execution_faults import runtime_fault
            known=last.get('runtime_fault') or runtime_fault(last.get('error',''),known_only=True)
            if known and known['started']:
                await self.execution_fault(last,self.state.get('pending_action'));return
        if (self.state.get('blocker') or {}).get('discussion_limit'):
            self.state['blocker']=None;self.save()
            await self.supervisor_loop({'type':'discussion_limit_resume','text':text});return
        if (self.state.get('blocker') or {}).get('billing'):
            if not self.billing_ready():return
            self.state['blocker']=None;self.save()
            await self.supervisor_loop({'type':'billing_restored'});return
        if self.state.get('blocker'):
            runtime=self.state['blocker'].get('runtime')
            if runtime and is_resume_only(text,self.settings):
                execution=next(e for e in self.state['evidence'] if e['id']==runtime['execution_id'])
                await self.execution_fault(execution,self.state.get('pending_action'),recheck=True);return
            if runtime:
                # A new explicit instruction may change the plan. A resume/recheck
                # never does, and a started contract is never automatically retried.
                self.state.setdefault('runtime_superseded',[]).append(runtime['execution_id'])
                self.state.update(blocker=None,pending_action=None);self.save()
                trigger['type']='new_instruction_after_runtime_fault'
            else:
                trigger=await self.resume_environment(text,trigger)
                if trigger is None:return
        elif text==RESUME_BUTTON:
            self.store.message(self.rid,'system','当前没有可复查的宿主阻断。',phase='宿主检查');self.store.status(self.rid,'blocked');return
        await self.supervisor_loop(trigger)

    async def resume_environment(self,text,trigger):
        a=self.state.get('pending_action');quota=await self.work.quota(self.rid,self.settings)
        aid=a['agents'][0] if a else self.settings.get('executor')
        checks=await preflight(self.root,self.recovery_settings(aid,a),quota,agent=aid)
        scope=self.scope_check()
        if scope:checks.append(scope)
        failed=[c for c in checks if not c['ok']]
        self.record_environment_checks(checks)
        if failed and is_resume_only(text,self.settings):self.host_error(checks);return None
        if not failed and is_resume_only(text,self.settings) and self.pending_valid():
            if not await self.execute(a,checked=True):return None
            trigger={'type':'execution_result','resumed_contract':a['contract_hash']}
        else:
            self.state['blocker']=None if not failed else {'checks':checks,'fingerprint':fingerprint(failed),'since':now()};self.save()
            trigger.update(type='blocker_changed' if failed else 'user_input',checks=checks)
        return trigger

    async def supervisor_loop(self,trigger):
        from workflows import json_reply,project_context
        correction=''
        for _ in range(self.settings['supervisor']['max_steps']):
            policy=await self.bindings();revision=policy['revision']
            context=(await asyncio.to_thread(project_context,self.root))[:24000]
            connections=await roster.connections(self.settings)
            quota=await self.work.quota(self.rid,self.settings)
            self.balance_notices(quota)
            from discussion_resolution import resolve
            if await resolve(self,policy,trigger,connections,quota):return
            if not self.billing_ready():return
            own=next((r for r in (quota or {}).get('participants',[]) if r['role']=='supervisor'),None)
            if own and own['level']=='exhausted':raise RuntimeError('监工所用账户额度已耗尽，请更换连接或等待重置。')
            self.state['step']+=1;self.save();monitor_answered=False
            self.store.event(self.rid,{'type':'phase','phase':f"第 {self.state['step']} 轮 · 监工检查",'provider':'supervisor'})
            before_state=digest({k:v for k,v in self.state.items() if k!='step'})
            before_usage=self.store.last_usage_id(self.rid)
            try:
                request=lambda turn:supervisor_payload(turn,self.state,{**trigger,'correction':correction},context,connections,quota)
                raw=await self.work.reply(self.rid,'supervisor',request,self.root,self.settings,'安排与检查',publish=False)
                monitor_answered=True
                d=validate_control(json_reply(raw),self.state,set(self.labels));action=d['action'];kind=action['type']
                if self.store.policy(self.rid,self.settings['cooperation_policy'])['revision']!=revision:continue
                if kind in ['ask','execute']:
                    missing=[self.labels[a] for a in action['agents'] if not connections.get(a,{}).get('ready')]
                    if missing:raise ValueError('这些身份未连接，不能伪造参与：'+', '.join(missing)+'。请等待连接或按用户合作方式调整安排。')
                    exhausted=[r['name'] for r in (quota or {}).get('participants',[]) if r['id'] in action['agents'] and r['level']=='exhausted']
                    if exhausted and kind=='ask':raise ValueError('所选账户额度已耗尽：'+', '.join(exhausted))
                self.state['rules']=d['rules']
                for patch in d['checks_patch']:self.state['checks'][patch['rule_id']]=patch
                self.save();mid=self.store.message(self.rid,'supervisor',d['public_message'],model=self.settings['supervisor']['model'],phase='安排与检查')
                self.store.event(self.rid,{'type':'control','message_id':mid,'control':{'action':action,'checks_patch':d['checks_patch']}})
                self.store.event(self.rid,{'type':'supervision','revision':revision,'rules':d['rules'],'checks':list(self.state['checks'].values()),'message':d['public_message'],'action':action,'turn':self.state['step']})
                correction=''
                if kind=='wait':
                    self.state['status']='waiting';self.save();self.store.status(self.rid,'blocked');return
                if kind=='finish':
                    self.state['status']='complete';self.state['pending_action']=None;self.state['checks']={c['rule_id']:c for c in d['full_checks']};self.save()
                    report=self.work.supervision_report(self.state['goal']['text'],policy,self.settings,self.state['evidence'],d)
                    self.store.event(self.rid,{'type':'report','text':report})
                    if any(e['kind']=='execute' for e in self.state['evidence']):
                        path=self.root/'.tiered/REPORT.md'
                        if not path.resolve().is_relative_to(self.root):raise ValueError('报告路径越出项目。')
                        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(report,encoding='utf-8')
                    self.store.status(self.rid,'complete');return
                if kind=='adopt_plan':
                    adopt(self.state,self.store,self.rid,action,set(self.labels)-{self.settings.get('executor')});self.save()
                elif kind=='request_approval':
                    plan=valid_plan(self.state,self.store,self.rid,action.get('plan_ref'))
                    judgement_notes=[]
                    for j in action.get('judgements',[]):
                        prior=self.state.get('votes',{}).get(plan['ref'],{}).get(j.get('agent'))
                        if not prior or prior.get('source')!='pending_supervisor' or j.get('message_id')!=prior.get('message_id') or type(j.get('agree')) is not bool or not j.get('reason'):raise ValueError('监工判定必须绑定那位成员待判定的原话，不能覆盖格式表态。')
                        prior.update(agree=j['agree'],source='supervisor',reason=j['reason']);judgement_notes.append(self.labels[j['agent']]+'：'+j['reason']+'（此项由监工判定）')
                    self.submit_approval(plan,judgement_notes);return
                elif kind=='ask':
                    await self.ask(action,policy)
                    if await resolve(self,policy,{'type':'action_result'},connections,quota):return
                elif kind=='check':await self.host_check(action)
                elif kind=='execute':
                    prepared=self.prepare(action)
                    self.store.event(self.rid,{'type':'plan','tasks':[prepared['task']]})
                    if not await self.execute(prepared,quota):return
                trigger={'type':'action_result','action':kind}
            except BillingUnavailable as exc:
                self.billing_block(exc,undo_round=not monitor_answered);return
            except ValueError as exc:
                correction=str(exc);trigger={'type':'correction'}
                self.store.message(self.rid,'system','安排校验：'+correction,phase='安排校验')
            finally:
                after_state=digest({k:v for k,v in self.state.items() if k!='step'})
                self.store.mark_usage_effect(self.rid,'supervisor',before_usage,after_state!=before_state)
        raise RuntimeError('已达本次监工轮数上限，状态与证据已保存，可继续。')


async def coordinate(work,rid,text,root,settings):
    await Coordinator(work,rid,root,settings).run(text)
