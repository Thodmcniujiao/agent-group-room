"""User-policy-driven supervision; no fixed planner/reviewer graph."""
import asyncio
import json
from pathlib import Path
import re

from engines import (NAMES,auth_status,build_snapshot,deepseek_session,read_response,preferences)
from supervision import validate_decision,supervisor_request
import roster


def describe_error(error):
    """Show the real causes inside exception groups (anyio/MCP TaskGroup) instead of their summary."""
    if isinstance(error,BaseExceptionGroup):
        return '；'.join(describe_error(e) for e in error.exceptions)
    text=str(error).strip()
    return type(error).__name__+(': '+text if text else '')


class ExecutionScopeViolation(RuntimeError):
    def __init__(self,message,record,before):
        super().__init__(message);self.record=record;self.before=before


class SupervisorWorkflow:
    usage=None  # account_usage.UsageMonitor, attached by the GUI host

    async def quota(self,rid,settings,wait=8):
        """Fresh-enough quota for the supervisor and every member (same data the UI shows)."""
        from account_usage import participants,team_quota,quota_text
        if self.usage is None:return None
        ids={aid for _,aid,_,_ in participants(settings)}
        try:
            pending=self.usage.request_refresh(settings,ids)
            if pending:await asyncio.wait(pending,timeout=wait)
            quota=team_quota(settings,self.usage.snapshot(settings))
        except Exception as exc:
            self.store.event(rid,{'type':'activity','provider':'system','text':'额度读取失败：'+str(exc)[:200]})
            return None
        self.store.event(rid,{'type':'quota','quota':quota})
        self.store.event(rid,{'type':'activity','provider':'system','text':'各方额度：'+'；'.join(
            r['name']+' '+({'exhausted':'已耗尽','low':'偏低','unknown':'未知','ok':'正常'}[r['level']]) for r in quota['participants'])})
        return quota

    async def supervised(self,rid,goal,root,settings):
        from coordination import coordinate
        await coordinate(self,rid,goal,root,settings)

    async def execute_supervised(self,rid,agent,task,root,settings):
        from optimization_settings import refresh_for_call
        settings=refresh_for_call(self.store,rid,settings)
        from workflows import git,changed_hashes,contract,verify_commands,confirm_recovery
        result={'agent':agent,'kind':'execute','status':'failed','model':roster.member(settings,agent)['model'],
                'instruction':task['goal'],'allowed_paths':task['allowed_paths'],'acceptance_commands':task['acceptance_commands'],'tests_run':False}
        before=None;answer='';error='';labels=roster.names(settings);source=roster.member(settings,agent);started_execution=False;stage='preparation'
        contract_text=contract(task)
        try:
            gitroot=(await git(root,'rev-parse','--show-toplevel')).strip()
            if Path(gitroot).resolve()!=root: raise ValueError('编码需要选择 Git 仓库根目录。')
            for name in task['allowed_paths']:
                if not (root/name).resolve().is_relative_to(root): raise ValueError('允许文件实际指向项目外路径。')
            tiered=root/'.tiered'
            if not (tiered/'tasks').resolve().is_relative_to(root): raise ValueError('工作流目录越出项目。')
            (tiered/'tasks').mkdir(parents=True,exist_ok=True)
            (tiered/'tasks'/(task['id']+'.md')).write_text(contract_text,encoding='utf-8')
            self.store.event(rid,{'type':'plan','tasks':[task]})
            self.store.event(rid,{'type':'phase','phase':labels[agent]+' · 执行契约','provider':agent})
            before=await changed_hashes(root)
            if source['backend'] in ['deepseek','openai-compatible']:
                if source.get('google_billing'):
                    from google_billing import prepare
                    source=await prepare(source)
                    self.store.event(rid,{'type':'activity','provider':agent,'text':'Google 执行渠道：'+source['_google_route']})
                from context import ContextTurn
                execution=ContextTurn(self.store,rid,agent,source,source['model'],settings,task['id']+' 执行',coding=True,contract_text=contract_text)
                from workflows import project_context
                project=project_context(root)
                wf,_=self.store.load_workflow(rid)
                state={'code_hash':(wf or {}).get('code_hash'),'execution_evidence':[e for e in (wf or {}).get('evidence',[]) if e.get('kind')=='execute'][-4:]}
                execution.contract_text=contract_text;execution.parts={'contract':contract_text,'project':project,'state':json.dumps(state,ensure_ascii=False)}
                extra=execution.compose('项目规则与路径：\n'+project+'\n执行状态（核对实际文件）：\n'+json.dumps(state,ensure_ascii=False))
                async with deepseek_session(root,roster.member(settings,agent)['model'],self.store,rid,member=source,context_turn=execution) as session:
                    from collaboration_log import emit
                    emit(execution,'executor_task',{'task':contract_text,'context':extra,'model':'flash','instructions':execution.instructions()})
                    pending=json.loads(read_response(await session.call_tool('get_deepseek_recovery',{})))
                    if not pending.get('ok'):raise RuntimeError(pending.get('error','恢复记录读取失败。'))
                    if pending.get('pending'): raise RuntimeError('项目有待核实的写入记录。请先从“恢复记录”核对并确认。')
                    mid=self.store.message(rid,agent,model=roster.member(settings,agent)['model'],phase=task['id']+' 执行',status='streaming')
                    job=None
                    try:
                        # A lost start response does not prove that the job never started.
                        stage='starting';started_execution=True
                        started=json.loads(read_response(await session.call_tool('start_deepseek',{'task':contract_text,'context':extra,'model':'flash'})))
                        if not started.get('ok'):
                            started_execution=False
                            raise RuntimeError(started.get('error','执行启动失败。'))
                        job=started['job_id'];stage='execution'
                        while True:
                            await asyncio.sleep(1)
                            data=json.loads(read_response(await session.call_tool('get_deepseek_result',{'job_id':job})))
                            if not data.get('ok'):raise RuntimeError('执行结果读取失败：'+str(data.get('error','MCP 返回异常。')))
                            if data.get('ready'): break
                        answer=data.get('result',{}).get('final_message','') if isinstance(data.get('result'),dict) else ''
                        if data.get('status')!='completed': raise RuntimeError('执行未完成：'+str(data.get('error') or data.get('status')))
                        self.store.update(mid,answer or '执行者没有返回总结。','complete')
                        execution.complete(answer)
                        if getattr(execution,'audit',None):execution.audit.outcome=('complete',answer,mid)
                        stage='verification';result.update(await self.verify_execution(rid,root,task,before,answer))
                        stage='recovery'
                        await confirm_recovery(session,root,set(task['allowed_paths']))
                    except BaseException as exc:
                        interrupted=isinstance(exc,asyncio.CancelledError)
                        self.store.update(mid,answer or '执行尚未完成；已发生的修改与恢复记录会保留。','interrupted' if interrupted else 'error')
                        if getattr(execution,'audit',None):execution.audit.outcome=('interrupted' if interrupted else 'failed',answer,mid)
                        if job:
                            try: await asyncio.shield(session.call_tool('cancel_deepseek',{'job_id':job}))
                            except BaseException: pass
                        raise
            else:
                snapshot,_,_=await asyncio.to_thread(build_snapshot,root,[p for p in task['allowed_paths'] if (root/p).is_file()])
                prompt=('你是 '+labels[agent]+' 编码执行者。严格按以下契约修改项目文件，不超出允许范围。'
                    '不启动其他代理，不联网，不安装依赖，不 commit/push，不改控制配置。测试将由本机独立执行。'
                    '如果权限拒绝或无法完成，返回 STATUS: BLOCKED 及具体原因；不能口头假装完成。'
                    '完成时返回 STATUS: DONE、FILES、TESTS（未运行则说明）。\n\n'+contract(task)+'\n\n项目文件快照：\n'+snapshot)
                started_execution=True;stage='execution'
                answer=await self.reply(rid,agent,prompt,root,settings,task['id']+' 执行',coding=True,allowed_paths=task['allowed_paths'],contract_text=contract_text)
                stage='verification'
                result.update(await self.verify_execution(rid,root,task,before,answer))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            full_error=describe_error(exc);error=full_error[:1800]
            from execution_faults import runtime_fault
            # Exceptions describe the runtime/transport. Semantic worker failures and
            # failing acceptance are returned as evidence, without raising exceptions.
            result.update(status='failed',error=error,host_error=True,
                runtime_fault=runtime_fault(full_error,stage=stage,started=started_execution))
        finally:
            if before is not None:
                after=await changed_hashes(root);missing=object()
                changed=[p for p in set(before)|set(after) if before.get(p,missing)!=after.get(p,missing)]
                result['changed_files']=changed
                import hashlib
                result['file_hashes']={name:hashlib.sha256((root/name).read_bytes()).hexdigest() if (root/name).is_file() else None for name in task['allowed_paths']}
                outside=[p for p in changed if p not in task['allowed_paths']]
                if outside:
                    message='发现契约范围外变更，已停止整个协作，不允许监工口头放行：'+', '.join(outside)
                    result.update(status='failed',error=message,reply=answer)
                    raise ExecutionScopeViolation(message,result,{p:before.get(p) for p in outside})
        result['reply']=answer
        result['started_execution']=started_execution
        if result['status']!='complete' and not result.get('host_error'):
            self.store.message(rid,'system',labels[agent]+' 本轮执行未通过，交由监工检查纠偏。'+(error or ''),phase='执行证据')
        return result

    async def verify_execution(self,rid,root,task,before,answer):
        from workflows import verify_commands
        evidence=await verify_commands(self.store,rid,root,task['acceptance_commands'])
        done=bool(re.search(r'STATUS\s*:\s*DONE',answer,re.I))
        tested=bool(evidence)
        passed=all(e['exit_code']==0 for e in evidence)
        self.store.message(rid,'system',('本机验收已运行，退出码：'+', '.join(str(e['exit_code']) for e in evidence)) if tested else '本轮未请求自动验收，未运行测试。监工不能将其当作测试已通过。',phase='实际验收')
        return {'status':'complete' if done and passed else 'failed','tests_run':tested,'tests':evidence,
                'worker_reported_done':done}

    @staticmethod
    def supervision_report(goal,policy,settings,records,decision):
        return '# 监工协作报告\n\n## 用户目标\n'+goal+'\n\n## 用户合作方式\n'+policy['text']+\
            '\n\n## 监工配置\n'+json.dumps(settings['supervisor'],ensure_ascii=False,indent=2)+\
            '\n\n## 实际协作记录\n'+json.dumps(records,ensure_ascii=False,indent=2)+\
            '\n\n## 最终逐项检查\n'+json.dumps(decision,ensure_ascii=False,indent=2)
