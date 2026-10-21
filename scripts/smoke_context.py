"""Opt-in live provider smoke test, isolated from real rooms and project files."""
import argparse
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import uuid
import subprocess

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gui'))
from engines import preferences
from store import Store
import roster
from context import ContextTurn
from engines import deepseek_session,read_response
from workflows import contract
from workflows import Workflows


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true')
    parser.add_argument('--mode',choices=['chat','coding','write'],default='chat');parser.add_argument('--report')
    parser.add_argument('--agent',choices=['chatgpt','claude','gemini','deepseek'])
    args=parser.parse_args()
    if not args.live:parser.error('Pass --live to authorize small, real model requests.')
    selected=copy.deepcopy(preferences.load());selected['executor']='deepseek'
    for a in selected['members']:a['effort']='low'
    agents=[a for a in selected['members'] if a['id'] in ['chatgpt','claude','gemini','deepseek']]
    if args.mode=='coding':agents=[a for a in agents if a['id'] in ['deepseek','gemini']]
    if args.agent:agents=[a for a in agents if a['id']==args.agent]
    results=[];limit=asyncio.Semaphore(2)
    with tempfile.TemporaryDirectory(prefix='agent-room-context-live-') as folder:
        root=Path(folder);store=Store(root/'data')
        async def check(agent):
            async with limit:
                identity=agent['id'];project=root/identity;project.mkdir()
                rid=store.create('Isolated smoke '+identity,str(project))['id'];nonce='ROOM'+uuid.uuid4().hex[:10]
                store.message(rid,'peer','公开原始消息中的共享编号：'+nonce)
                first=('监工执行咨询：本次执行编号为 '+nonce+'，只回复此编号，不使用工具、不读写文件。' if identity=='deepseek' else
                    '直接从群内原始消息找到共享编号，只回复编号；不使用工具、不读写文件。')
                started=time.monotonic()
                try:
                    if args.mode=='write':
                        private=copy.deepcopy(selected);private['executor']=identity
                        subprocess.run(['git','init'],cwd=project,check=True,capture_output=True)
                        (project/'verify.js').write_text("const fs=require('fs');if(fs.readFileSync('result.txt','utf8').trim()!=="+json.dumps(nonce)+")process.exit(1)",encoding='utf-8')
                        task={'id':'T-01','goal':'只创建 result.txt，内容精确为 '+nonce,'allowed_paths':['result.txt'],
                            'acceptance_commands':['node verify.js'],'context':'仅修改 result.txt。不运行命令、不安装依赖、不提交或推送。完成时返回 STATUS: DONE。验收由宿主执行。'}
                        work=Workflows(store)
                        result=await work.execute_supervised(rid,identity,task,project,private)
                        assert result['status']=='complete',str(result.get('error') or result.get('reply') or result)
                        assert result['changed_files']==['result.txt'],str(result['changed_files'])
                        assert result['tests'][0]['exit_code']==0
                        result={'agent':identity,'mode':'write','model':agent['model'],'ok':True,
                            'seconds':round(time.monotonic()-started,2),'turns':1,'changed_files':result['changed_files'],
                            'tests':[{'command':t['command'],'exit_code':t['exit_code']} for t in result['tests']]}
                        results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True);return
                    elif args.mode=='coding':
                        private=copy.deepcopy(selected);private['executor']=identity
                        (project/'proof.txt').write_text(nonce,encoding='utf-8')
                        async def run_task(number,goal):
                            task={'id':'T-'+str(number),'goal':goal,'allowed_paths':['proof.txt'],'acceptance_commands':[],
                                'context':'只读验证；禁止写文件、运行命令和联网。只返回编号。'}
                            turn=ContextTurn(store,rid,identity,agent,agent['model'],private,'只读执行验证',coding=True)
                            data=turn.compose(contract(task))
                            async with deepseek_session(project,agent['model'],store,rid,member=agent,context_turn=turn) as session:
                                value=json.loads(read_response(await session.call_tool('start_deepseek_readonly',{'task':contract(task),'context':data})))
                                if not value.get('ok'):raise RuntimeError(str(value))
                                job=value['job_id'];deadline=time.monotonic()+180
                                while True:
                                    result=json.loads(read_response(await session.call_tool('get_deepseek_result',{'job_id':job})))
                                    if result.get('ready'):break
                                    if time.monotonic()>deadline:raise TimeoutError('Coding smoke timeout')
                                    await asyncio.sleep(.3)
                                if result.get('status')!='completed':raise RuntimeError(str(result.get('error') or result.get('status')))
                                answer=result['result']['final_message'];turn.complete(answer)
                                file=Path(turn.state['native']['execution_file'])
                                checkpoint=json.loads(file.read_text(encoding='utf-8'))
                                assert checkpoint['complete'],'Private execution checkpoint was not committed'
                                assert 'MUST-NOT' not in file.read_text(encoding='utf-8')
                                return answer
                        a=await run_task(1,'使用 Read 工具读取 proof.txt，返回其中的编号。')
                        b=await run_task(2,'不要调用工具，复述你上一轮从 proof.txt 读到的编号。')
                        assert (project/'proof.txt').read_text(encoding='utf-8')==nonce
                    else:
                        a=await roster.reply(store,rid,identity,first,project,selected,'上下文短验证')
                        b=await roster.reply(store,rid,identity,'你上一轮回复的编号是什么？只回复编号，不使用工具。',project,selected,'续聊短验证')
                    assert nonce in a and nonce in b,'The session did not preserve the original identifier'
                    delivered=[d for d in store.deliveries(rid) if d['status']=='delivered']
                    assert len(delivered)==2
                    if identity=='deepseek' or args.mode=='coding':assert all(not d['messages'] and d['role']=='executor' for d in delivered)
                    else:assert any(d['messages'] for d in delivered)
                    result={'agent':identity,'mode':args.mode,'model':agent['model'],'ok':True,'seconds':round(time.monotonic()-started,2),'turns':2,
                        'roles':[d['role'] for d in delivered]}
                except Exception as e:result={'agent':identity,'mode':args.mode,'ok':False,'error':str(e)[:600],'seconds':round(time.monotonic()-started,2)}
                results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
        try:await asyncio.gather(*(check(a) for a in agents))
        finally:store.db.close()
    if args.report:Path(args.report).write_text(json.dumps({'results':results,'passed':all(r['ok'] for r in results)},ensure_ascii=False,indent=2),encoding='utf-8')
    if not all(r['ok'] for r in results):raise SystemExit(1)


if __name__=='__main__':asyncio.run(main())
