"""Opt-in live acceptance in an isolated project; never deploy the generated utility."""
import argparse
import asyncio
import copy
from datetime import datetime,timezone
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'gui'),str(ROOT/'plugins/tiered-dev/scripts')]
from engines import preferences
from store import Store
from workflows import Workflows
from workflow_state import delivery_verified

GOAL='部署一个开机自启动的双击桌面空白处切换桌面 show desktop icon的小脚本 开机时默认状态是取消显示'
SCOPE='''这是 plan.md 授权的隔离验收，使用上面 New folder 原始需求。仅交付临时项目内的源码和构建产物，
不得在这台电脑实际安装自启动、改注册表、修改真实桌面设置或运行常驻钩子。提供 --check 参数，仅做只读环境检查后退出。
仅允许修改四个文件：DesktopIconToggle/DesktopIconToggle.cs、DesktopIconToggle/app.manifest、DesktopIconToggle/build.cmd、DesktopIconToggle/README.md。
使用本机已有的 .NET Framework csc 编译器，不安装依赖。构建输出 DesktopIconToggle/bin/DesktopIconToggle.exe 是验收产物。
验收命令使用 cmd.exe /d /c DesktopIconToggle/build.cmd 和 cmd.exe /d /c "start /wait DesktopIconToggle/bin/DesktopIconToggle.exe --check"。
用户确认步骤由本自动化验收夹具模拟，所有原话与确认会明确标为测试数据。不要把它算作对真实用户协作室的批准。'''
POLICY='''这是独立的自动化验收协作室。ChatGPT 先提出一个足够自足的方案；Claude 和 Gemini 对该方案分别给出风险与明确表态。
若有分歧，保留反对原文，最多整合修订一次后再表态，不重复全文。由监工采纳版本、请求测试用户确认，再交给唯一执行者 DeepSeek。
执行者只接收完整契约，不接收群聊。真实构建与 --check 的退出码由宿主记录。不能以口头成功代替文件与命令证据。
所有检查在临时项目完成；不安装程序、注册启动项、修改真实桌面或发布。'''


def evaluate_delivery(report):
    """Evaluate retained real evidence without requiring another paid model call."""
    state=report.get('workflow') or {}
    execution=next((e for e in reversed(state.get('evidence',[])) if e.get('kind')=='execute'),None)
    tested=bool(execution and execution.get('status')=='complete' and execution.get('tests_run')
        and execution.get('tests') and all(t['exit_code']==0 for t in execution['tests']))
    independent=bool(execution and delivery_verified(state,execution))
    report['verification_source']='host_independent_acceptance' if independent else 'completed_tested_execution' if tested else None
    report['passed']=report.get('status')=='complete' and not state.get('scope_violation') and (tested or independent)
    return report['passed']


class ObservedStore(Store):
    def event(self,rid,value):
        result=super().event(rid,value)
        if value.get('type') in ['phase','room_status']:
            print(json.dumps({'event':value['type'],'value':value.get('phase',value.get('status'))},ensure_ascii=False),flush=True)
        return result


async def run(args):
    output=(ROOT/args.report_dir).resolve();output.mkdir(parents=True,exist_ok=True)
    store=ObservedStore(output/'live-state');settings=copy.deepcopy(preferences.load())
    if args.resume:
        room=store.rooms()[0];rid=room['id'];root=Path(room['project'])
        previous=json.loads((output/'live-task.json').read_text(encoding='utf-8'))
        index=1
        while (output/f'live-task-attempt{index}.json').exists():index+=1
        (output/f'live-task-attempt{index}.json').write_text(json.dumps(previous,ensure_ascii=False,indent=2),encoding='utf-8')
    else:
        root=Path(tempfile.mkdtemp(prefix='agent-room-live-acceptance-'))
        subprocess.run(['git','init',str(root)],capture_output=True,check=True)
        # Normal build outputs are fixture artifacts; no source paths are excluded.
        (root/'.git/info/exclude').write_text('DesktopIconToggle/bin/\nDesktopIconToggle/obj/\n',encoding='utf-8')
    _,settings['members']=preferences.resolve_team(settings['agents'],[{'agent_id':aid,'enabled':True} for aid in ['chatgpt','claude','gemini','deepseek']])
    settings.update(executor='deepseek',supervisor_context='state',resume_words=['继续'],discussion_tolerance=3,
        cooperation_policy=POLICY,pause_before_execution=False)
    settings['supervisor']['max_steps']=20
    if not args.resume:rid=store.create('LIVE TEST FIXTURE · not a user room',str(root))['id'];store.set_policy(rid,POLICY)
    work=Workflows(store);report={'started_utc':datetime.now(timezone.utc).isoformat(),'project':str(root),
        'room':rid,'synthetic_user_actions':[],'original_goal':GOAL,'models':{a['id']:{'model':a['model'],'effort':a.get('effort')} for a in settings['members']}}
    if args.resume:report['synthetic_user_actions']=previous.get('synthetic_user_actions',[])[:]
    try:
        prompt=GOAL+'\n'+SCOPE
        if args.resume:
            import os,hashlib
            from engines import deepseek_session,read_response
            from workflow_state import add_evidence
            assert root.parent==Path(os.environ['TEMP']) and root.name.startswith('agent-room-live-acceptance-')
            source=next(a for a in settings['members'] if a['id']=='deepseek')
            async with deepseek_session(root,source['model'],member=source) as session:
                recovery=json.loads(read_response(await session.call_tool('get_deepseek_recovery',{})))
                if not recovery.get('ok'):raise RuntimeError(recovery.get('error'))
                ids=[]
                reviews={}
                for entry in recovery.get('pending',[]):
                    path=(root/entry['path']).resolve()
                    if not path.is_relative_to(root) or not path.is_file():raise RuntimeError('Fixture recovery path is unavailable')
                    group=reviews.setdefault(entry['path'],{'actual_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'records':[]})
                    group['records'].append(entry)
                    ids.append(entry['transaction_id'])
                for name,review in reviews.items():
                    if not any(r['sha256']==review['actual_sha256'] for r in review['records']):raise RuntimeError('No journal intent matches the actual final file')
                    review['review']='Fixture controller verified the current final version; older differing intents remain explicitly uncertain in this archive, not claimed successful.'
                    target=output/'verified-final-files'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((root/name).read_bytes())
                if ids:
                    (output/f'recovery-review-attempt{index}.json').write_text(json.dumps(reviews,ensure_ascii=False,indent=2),encoding='utf-8')
                    ack=json.loads(read_response(await session.call_tool('acknowledge_deepseek_mutations',{'transaction_ids':ids})))
                    if not ack.get('ok'):raise RuntimeError('Fixture recovery acknowledgement failed')
            probe=root/'_probe.cmd'
            had_probe=probe.exists()
            if had_probe:
                (output/'scope-violation-probe.cmd.txt').write_bytes(probe.read_bytes());probe.unlink()
            state,rev=store.load_workflow(rid)
            if had_probe:add_evidence(state,store,rid,{'kind':'execute','agent':'deepseek','status':'failed','error':'First live fixture attempt created unauthorized _probe.cmd; archived, verified and removed by the test host.',
                'changed_files':['_probe.cmd'],'bound_to':{'plan':state.get('current_plan'),'code':None}})
            # Migrate this in-development fixture's old counters, preserving every failed call.
            if any(e.get('agent')=='claude' and e['status']=='failed' for e in state['evidence']):
                state['discussion_rounds']['P1']=sum(e.get('ask_kind') in ['review','vote'] and e['status']=='complete' for e in state['evidence'])
            # Backfill known approved command metadata produced before the host-check fix.
            pending=state.get('pending_action') or {};commands=pending.get('task',{}).get('acceptance_commands',[])
            for e in reversed(state['evidence']):
                if e['kind']=='execute':
                    if commands:e.setdefault('acceptance_commands',commands)
                    break
            state['blocker']=None;store.save_workflow(rid,state,rev)
            prompt='继续隔离验收，夹具进程已重启，宿主引号实现已加载修复。现明确更新并授权验收命令为 cmd.exe /d /c DesktopIconToggle\\build.cmd 与 DesktopIconToggle\\bin\\DesktopIconToggle.exe --check；它们取代原先不适用于 Windows cmd 的正斜杠写法与多余 start 包装。若方案包含旧命令，请由一位成员整合完整方案新版本再请求夹具确认。失败记录和不确定中间写入状态全部留档，夹具已经核查并归档当前最终文件（每个文件至少匹配一条 journal intent），确认该最终版本后清除待审记录；不声称旧中间写入全部成功。只处理仍未通过的部分；宿主完整验收已经通过时，用宿主 check 再核实并生成 verifies_execution，引用真实证据结束；不要为获取 STATUS: DONE 再唤醒执行者。不再环境探测，不新增辅助文件，不降低 high 推理强度；验收由宿主独立执行。'
            report['synthetic_user_actions'].append({'type':'restore_failed_fixture','fixture_only':True,'text':prompt})
        task=work.start(rid,prompt,'workflow','all',settings)
        for attempt in range(4):
            await asyncio.wait_for(asyncio.shield(task),1500)
            room=store.room(rid);wf,_=store.load_workflow(rid)
            if room['status']=='awaiting_approval':
                ref=wf['awaiting_approval'];report['synthetic_user_actions'].append({'type':'approve','plan_ref':ref,'text':'同意','fixture_only':True})
                task=work.start(rid,'同意','workflow','all',settings);continue
            break
        room=store.room(rid);wf,_=store.load_workflow(rid)
        report.update(status=room['status'],workflow=wf,usage_summary=store.usage_summary(rid),usage=store.usage(rid),
            messages=[{k:m[k] for k in ['id','provider','phase','status','text']} for m in store.messages(rid)])
        evaluate_delivery(report)
    except BaseException as exc:
        from orchestrator import describe_error
        await work.stop(rid);report.update(passed=False,error=describe_error(exc),messages=store.messages(rid),usage=store.usage(rid))
    finally:
        report['finished_utc']=datetime.now(timezone.utc).isoformat()
        (output/'live-task.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        store.db.close()
    print(json.dumps({k:report.get(k) for k in ['passed','status','error','project','usage_summary']},ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--resume',action='store_true',help='Continue only the isolated fixture after an explicitly verified repair')
    parser.add_argument('--report-dir',default='test-reports/token-optimization-2026-10-22/acceptance')
    args=parser.parse_args();logging.disable(logging.CRITICAL)
    raise SystemExit(asyncio.run(run(args)))
