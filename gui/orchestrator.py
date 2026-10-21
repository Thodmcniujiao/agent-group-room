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
        from workflows import json_reply,project_context
        records=[];rules=[];revision=None;correction='';labels=roster.names(settings)
        connections=await roster.connections(settings)
        context=await asyncio.to_thread(project_context,root)
        context=context[:24000]
        max_steps=settings['supervisor']['max_steps']
        for turn in range(1,max_steps+1):
            policy=self.store.policy(rid,settings['cooperation_policy'])
            if policy['revision']!=revision:
                if revision is not None:
                    self.store.message(rid,'system','合作方式已更新。监工正在按新规则重新检查此前的协作记录。',phase='规则更新')
                rules=[];revision=policy['revision'];correction=''
            for m in settings['members']:
                if m['backend'] in ['deepseek','openai-compatible']:connections[m['id']]['ready']=bool(preferences.get_member_key(m)) or m.get('no_auth',False)
            self.store.event(rid,{'type':'phase','phase':f'第 {turn} 轮 · 监工检查','provider':'supervisor'})
            quota=await self.quota(rid,settings)
            own=next((r for r in (quota or {}).get('participants',[]) if r['role']=='supervisor'),None)
            if own and own['level']=='exhausted':
                raise RuntimeError('监工所用账户额度已耗尽（'+own['summary']+'）。请在“协作室信息”中把监工换成其他 Agent，或等待重置后继续。')
            try:
                text=await self.reply(rid,'supervisor',supervisor_request(goal,policy,rules,records,context,connections,correction,settings['members'],settings.get('executor'),quota),root,settings,'安排与检查')
                decision=validate_decision(json_reply(text),rules,records,set(labels))
            except ValueError as exc:
                correction=str(exc)
                self.store.message(rid,'system','监工的安排尚不满足执行条件：'+correction+' 正在请求纠正，不会执行无效安排。',phase='安排校验')
                continue
            rules=decision['rules'];action=decision['action'];kind=action['type'];correction=''
            if self.store.policy(rid,settings['cooperation_policy'])['revision']!=revision:
                continue
            self.store.event(rid,{'type':'supervision','revision':revision,'rules':rules,'checks':decision['checks'],
                'message':decision['message'],'action':action,'turn':turn})
            if kind=='wait':
                raise RuntimeError('监工暂停：'+decision['message'])
            if kind=='finish':
                report=self.supervision_report(goal,policy,settings,records,decision)
                self.store.event(rid,{'type':'report','text':report})
                if any(r.get('kind')=='execute' for r in records):
                    (root/'.tiered/REPORT.md').write_text(report,encoding='utf-8')
                self.store.event(rid,{'type':'phase','phase':'监工确认完成','provider':'supervisor'})
                self.store.message(rid,'system','本次协作已按监工的逐项检查结束。各方原始答复、规则核对和执行证据已保存在本机会话中。',phase='协作完成')
                return
            agents=action['agents']
            if kind=='execute' and settings.get('executor') and agents!=[settings['executor']]:
                correction='本群已指定执行者 '+labels[settings['executor']]+'，execute 必须派给该身份。其他成员直接读取群原文讨论，由你整理执行要求。'
                continue
            missing=[labels[a] for a in agents if not connections.get(a,{}).get('ready')]
            if missing:
                correction='所指定的 '+', '.join(missing)+' 尚未连接。应按用户合作方式决定等待连接或安排其他工作，不能伪造它们的参与。'
                self.store.message(rid,'system',correction,phase='连接提示')
                continue
            if kind=='ask':
                from account_usage import quota_text
                snapshot,_,truncated=await asyncio.to_thread(build_snapshot,root)
                boundary=self.store.boundary(rid) if action.get('parallel') else None
                async def ask(agent):
                    self.store.event(rid,{'type':'phase','phase':f'{labels[agent]} · 协作发言','provider':agent})
                    try:
                        isolated=agent==settings.get('executor')
                        prompt=('监工的只读执行咨询（不修改文件）：\n'+action['instruction'] if isolated else
                            '你是协作群中的 '+labels[agent]+'。只读讨论，可用群消息工具核对原文，不修改文件。'
                            '\n用户目标：'+goal+'\n用户合作方式：'+policy['text']+'\n监工本轮指令：'+action['instruction'])
                        prompt+='\n各方可见额度（宿主实时读数，数据，未知不代表充足）：\n'+quota_text(quota)
                        prompt+='\n项目规则与路径：'+context+'\n只读快照：'+snapshot
                        answer=await self.reply(rid,agent,prompt,root,settings,'协作发言',boundary=boundary)
                        return {'agent':agent,'kind':'ask','status':'complete','model':roster.member(settings,agent)['model'],
                            'instruction':action['instruction'],'reply':answer}
                    except Exception as exc:
                        self.store.message(rid,'system',labels[agent]+' 未完成本轮发言：'+str(exc)[:1000],phase='需要纠偏')
                        return {'agent':agent,'kind':'ask','status':'failed','model':roster.member(settings,agent)['model'],
                            'instruction':action['instruction'],'error':str(exc)[:1500]}
                if action.get('parallel') and len(agents)>1:
                    results=await asyncio.gather(*(ask(agent) for agent in agents))
                else:
                    results=[]
                    for agent in agents: results.append(await ask(agent))
                for record in results:
                    record['id']=f'E{len(records)+1:02d}';records.append(record)
                    record['message_id']=next((m['id'] for m in reversed(self.store.messages(rid)) if m['provider']==record['agent']),None)
                    self.store.event(rid,{'type':'evidence','record':record})
            elif kind=='execute':
                from workflows import validate_plan
                try:
                    task=validate_plan({'tasks':[action.get('task',{})]},require_acceptance=False)['tasks'][0]
                except ValueError as exc:
                    correction='执行契约无效：'+str(exc)
                    self.store.message(rid,'system',correction,phase='安排校验');continue
                task['id']=f'T-{len([r for r in records if r.get("kind")=="execute"])+1:02d}'
                task['context']=str(task.get('context',''))+'\n监工本轮执行要求：\n'+action.get('instruction','')
                self.store.event(rid,{'type':'plan','tasks':[task]})
                self.store.message(rid,'system',f'监工安排 {labels[agents[0]]} 执行：'+task['goal']+'\n允许修改：'+', '.join(task['allowed_paths'])+'\n验收命令：'+('; '.join(task['acceptance_commands']) or '未请求自动验收'),phase='执行契约')
                if settings.get('pause_before_execution'):
                    event=asyncio.Event();self.resume[rid]=event
                    self.store.status(rid,'paused')
                    self.store.message(rid,'system',f'监工安排 {labels[agents[0]]} 修改文件。你启用了修改前暂停，查看本轮契约后点击“继续执行”。',phase='修改前暂停')
                    await event.wait();self.resume.pop(rid,None);self.store.status(rid,'running')
                    if self.store.policy(rid,settings['cooperation_policy'])['revision']!=revision:
                        continue
                record=await self.execute_supervised(rid,agents[0],task,root,settings)
                record['id']=f'E{len(records)+1:02d}';records.append(record)
                record['message_id']=next((m['id'] for m in reversed(self.store.messages(rid)) if m['provider']==record['agent']),None)
                self.store.event(rid,{'type':'evidence','record':record})
            # The next supervisor call sees actual results, never an assumed success.
        raise RuntimeError(f'已达到 {max_steps} 轮监工协作上限；没有自动宣称任务完成。请查看规则检查、补充要求或提高轮数后继续。')

    async def execute_supervised(self,rid,agent,task,root,settings):
        from workflows import git,changed_hashes,contract,verify_commands,confirm_recovery
        result={'agent':agent,'kind':'execute','status':'failed','model':roster.member(settings,agent)['model'],
                'instruction':task['goal'],'allowed_paths':task['allowed_paths'],'tests_run':False}
        before=None;answer='';error='';labels=roster.names(settings);source=roster.member(settings,agent)
        try:
            gitroot=(await git(root,'rev-parse','--show-toplevel')).strip()
            if Path(gitroot).resolve()!=root: raise ValueError('编码需要选择 Git 仓库根目录。')
            for name in task['allowed_paths']:
                if not (root/name).resolve().is_relative_to(root): raise ValueError('允许文件实际指向项目外路径。')
            tiered=root/'.tiered'
            if not (tiered/'tasks').resolve().is_relative_to(root): raise ValueError('工作流目录越出项目。')
            (tiered/'tasks').mkdir(parents=True,exist_ok=True)
            (tiered/'tasks'/(task['id']+'.md')).write_text(contract(task),encoding='utf-8')
            self.store.event(rid,{'type':'plan','tasks':[task]})
            self.store.event(rid,{'type':'phase','phase':labels[agent]+' · 执行契约','provider':agent})
            before=await changed_hashes(root)
            if source['backend'] in ['deepseek','openai-compatible']:
                from context import ContextTurn
                execution=ContextTurn(self.store,rid,agent,source,source['model'],settings,task['id']+' 执行',coding=True)
                from workflows import project_context
                extra=execution.compose('监工版本化执行契约：\n'+contract(task)+'\n项目规则：\n'+project_context(root))
                async with deepseek_session(root,roster.member(settings,agent)['model'],self.store,rid,member=source,context_turn=execution) as session:
                    pending=json.loads(read_response(await session.call_tool('get_deepseek_recovery',{})))
                    if pending.get('pending'): raise RuntimeError('项目有待核实的写入记录。请先从“恢复记录”核对并确认。')
                    mid=self.store.message(rid,agent,model=roster.member(settings,agent)['model'],phase=task['id']+' 执行',status='streaming')
                    job=None
                    try:
                        started=json.loads(read_response(await session.call_tool('start_deepseek',{'task':contract(task),'context':extra,'model':'flash'})))
                        if not started.get('ok'): raise RuntimeError(started.get('error','执行启动失败。'))
                        job=started['job_id']
                        while True:
                            await asyncio.sleep(1)
                            data=json.loads(read_response(await session.call_tool('get_deepseek_result',{'job_id':job})))
                            if data.get('ready'): break
                        answer=data.get('result',{}).get('final_message','') if isinstance(data.get('result'),dict) else ''
                        if data.get('status')!='completed': raise RuntimeError('执行未完成：'+str(data.get('error') or data.get('status')))
                        self.store.update(mid,answer or '执行者没有返回总结。','complete')
                        execution.complete(answer)
                        result.update(await self.verify_execution(rid,root,task,before,answer))
                        await confirm_recovery(session,root,set(task['allowed_paths']))
                    except BaseException:
                        if not answer: self.store.update(mid,'执行尚未完成；已发生的修改与恢复记录会保留。','error')
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
                answer=await self.reply(rid,agent,prompt,root,settings,task['id']+' 执行',coding=True,allowed_paths=task['allowed_paths'])
                result.update(await self.verify_execution(rid,root,task,before,answer))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error=describe_error(exc)[:1800]
            result['error']=error
        finally:
            if before is not None:
                after=await changed_hashes(root);missing=object()
                changed=[p for p in set(before)|set(after) if before.get(p,missing)!=after.get(p,missing)]
                result['changed_files']=changed
                outside=[p for p in changed if p not in task['allowed_paths']]
                if outside:
                    raise RuntimeError('发现契约范围外变更，已停止整个协作，不允许监工口头放行：'+', '.join(outside))
        result['reply']=answer
        if result['status']!='complete':
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
