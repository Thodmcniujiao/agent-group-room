"""Opt-in paired live supervisor test. Measures this path only, not total task savings."""
import argparse
import asyncio
import copy
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'gui'),str(ROOT/'plugins/tiered-dev/scripts')]
from engines import preferences
from store import Store
from context import ContextTurn,SYSTEM,TOOL_SCHEMAS
from provider_sessions import reply
from supervision import supervisor_request,supervisor_payload,validate_decision,validate_control
from workflow_state import new_state,current_refs
from workflows import json_reply

GOAL='部署一个开机自启动的双击桌面空白处切换桌面 show desktop icon的小脚本 开机时默认状态是取消显示'
POLICY='这是一项监工载荷计量实验，不执行任务。每次只返回 wait，公开说明还需用户确认部署范围；不要调用任何工具、安排成员、发起批准或执行。连续五次都是同一个只读计量任务，状态的变化是实验输入数据。'


async def variant(output,label,settings):
    directory=output/label;directory.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='agent-room-payload-'+label+'-'))
    store=Store(directory/'state');rid=store.create('SYNTHETIC LIVE BENCHMARK',str(root))['id']
    mid=store.message(rid,'you',GOAL);store.message(rid,'claude','测试提案：仅生成 C# 源码、清单、构建脚本和说明。实际自启动安装须经用户确认。')
    policy=store.policy(rid,POLICY);wf=new_state(GOAL,mid,policy);wf['policy_text']=POLICY;records=[];rules=[]
    source=next(a for a in settings['agents'] if a['id']==settings['supervisor']['provider'])
    context='项目路径：DesktopIconToggle/DesktopIconToggle.cs、app.manifest、build.cmd、README.md。只读计量，绝不部署。'
    report={'variant':label,'role':'supervisor','adapter':source['backend'],'model':settings['supervisor']['model'],'effort':settings['supervisor'].get('effort'),'turns':[]}
    try:
        for index in range(5):
            turn=ContextTurn(store,rid,'supervisor',source,settings['supervisor']['model'],settings,'paired live benchmark')
            if label=='baseline':
                turn.instructions=lambda:SYSTEM;turn.schemas=lambda:TOOL_SCHEMAS
                prompt=supervisor_request(GOAL,policy,rules,records,context,{},members=settings['members'],executor='deepseek')
            else:prompt=supervisor_payload(turn,wf,{'type':'fixture_update','index':index},context,{},None)
            answer=await reply(turn,prompt,root,settings['supervisor'].get('effort'),source,publish=label=='baseline')
            value=json_reply(answer)
            if label=='baseline':
                decision=validate_decision(value,rules,records,{m['id'] for m in settings['members']});rules=decision['rules']
            else:
                decision=validate_control(value,wf,{m['id'] for m in settings['members']});wf['rules']=decision['rules']
                store.message(rid,'supervisor',decision['public_message'])
            if decision['action']['type']!='wait':raise ValueError('Benchmark unexpectedly requested side effects; rejected')
            wf['step']+=1
            record={'id':f'E{index+1:02d}','kind':'check','agent':'host','status':'complete','bound_to':current_refs(wf),'note':'synthetic fixture update '+str(index)}
            records.append(record);wf['evidence']=records[:]
            report['turns'].append({'index':index,'action':'wait','host_input_characters':len(prompt),'delivery':turn.delivery_id})
            print(json.dumps({'variant':label,'turn':index+1,'status':'complete'}),flush=True)
        report.update(passed=True,usage=store.usage(rid),summary=store.usage_summary(rid))
    except Exception as exc:report.update(passed=False,error=type(exc).__name__+': '+str(exc),usage=store.usage(rid))
    finally:
        (directory/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');store.db.close()
    return report


async def main(args):
    output=(ROOT/args.report_dir).resolve();output.mkdir(parents=True,exist_ok=True)
    settings=copy.deepcopy(preferences.load());settings['executor']='deepseek';settings['supervisor_context']='state'
    baseline=await variant(output,'baseline',settings);optimized=await variant(output,'optimized',settings)
    def total(data,key):
        values=[r[key] for r in data['usage']]
        return sum(values) if values and all(v is not None for v in values) else None
    report={'scope':'Five read-only supervisor decisions on the same task, adapter, model and effort; not full-task savings',
        'baseline_passed':baseline['passed'],'optimized_passed':optimized['passed'],
        'baseline_input':total(baseline,'input'),'optimized_input':total(optimized,'input'),
        'baseline_output':total(baseline,'output'),'optimized_output':total(optimized,'output')}
    (output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--report-dir',default='test-reports/token-optimization-2026-10-22/paired-supervisor')
    args=parser.parse_args();logging.disable(logging.CRITICAL);asyncio.run(main(args))
