"""Isolated UI fixture. Does not read keys, generate replies or use real rooms."""
import argparse
import asyncio
import copy
import os
from pathlib import Path
import sys

parser=argparse.ArgumentParser();parser.add_argument('--state',required=True);parser.add_argument('--port',type=int,default=49341)
parser.add_argument('--catalog-file',help='Sanitized recorded metadata for reproducible selector checks')
parser.add_argument('--controls-demo',action='store_true',help='Simulate a paused request without model calls')
parser.add_argument('--optimization-demo',action='store_true',help='Deterministic host fault and workflow controls, no model calls')
parser.add_argument('--execution-fault-demo',action='store_true',help='Stopped coding job, host-only runtime recheck and output controls')
parser.add_argument('--approval-demo',action='store_true',help='Host approval document plus orphaned project lock, no model calls')
args=parser.parse_args();state=Path(args.state).resolve();state.mkdir(parents=True,exist_ok=True)
os.environ['TIERED_GUI_DATA_DIR']=str(state/'gui');os.environ['TIERED_GUI_PORT']=str(args.port)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gui'))
from engines import preferences
preferences.STATE=state;preferences.PREFS=state/'preferences.json';preferences.SECRET=state/'deepseek.dpapi';preferences.PROFILE=state/'profile.toml'
selected=preferences.load()
gemini=dict(id='gemini',name='Gemini',backend='openai-compatible',model='models/gemini-3.8-flash',effort='high',
    group='Google / Gemini',base_url='https://generativelanguage.googleapis.com/v1beta/openai',no_auth=False)
selected=preferences.save({'agents':selected['agents']+[gemini],
    'team':selected['team']+[dict(agent_id='gemini',enabled=True,model=gemini['model'],effort='high')]})
import server
from context import ContextTurn
server.MODELS.request_refresh=lambda *a,**k:[]
if args.catalog_file:
    import json
    from model_catalog import normalize,now
    recorded=json.loads(Path(args.catalog_file).read_text(encoding='utf-8'))['providers']
    for agent in selected['agents']:
        fingerprint,_=server.MODELS.context(agent)
        entry=recorded.get(agent['backend'] if agent['backend']!='openai-compatible' else agent['id'],{})
        server.MODELS.cache[fingerprint]={'models':normalize(entry.get('models',[])),'updated':now()}
        server.MODELS.attempts[fingerprint]={'state':'ready','checked':now()}
server.USAGE.request_refresh=lambda *a,**k:[]
async def connections(*a,**k):return {m['id']:{'ready':True,'detail':'界面测试夹具，无真实调用'} for m in selected['agents']}
server.roster.connections=connections
for room in server.STORE.rooms():raise RuntimeError('UI fixture state must be empty')
import tempfile
project=Path(tempfile.mkdtemp(prefix='agent-room-ui-project-')) if args.optimization_demo or args.approval_demo or args.execution_fault_demo else state
rid=server.STORE.create('上下文分流 · 界面测试',str(project))['id'];server.ROOMS.seed(rid,selected,legacy=True)
settings=server.ROOMS.load(rid)
server.STORE.message(rid,'you','界面测试：讨论成员直接共享原文；执行者只接收监工契约。')
server.STORE.message(rid,'chatgpt','原始提案：接口字段固定为 task_id、allowed_paths。')
source=next(a for a in settings['members'] if a['id']=='claude')
turn=ContextTurn(server.STORE,rid,'claude',source,source['model'],settings,'界面夹具 · 原文投递')
turn.compose('核对原提案');turn.complete('夹具答复')
source=next(a for a in settings['members'] if a['id']=='deepseek')
turn=ContextTurn(server.STORE,rid,'deepseek',source,source['model'],settings,'界面夹具 · 执行契约')
turn.compose('契约 T-01，第 1 版：只修改 demo.txt；保留 task_id、allowed_paths；不运行命令。')
turn.complete('夹具答复，没有真实修改文件')
if args.controls_demo:
    from avatars import Avatars
    server.AVATARS=Avatars(state/'avatars.json')
    async def demo_run(rid,text,mode,target,settings,root):
        try:
            source=next(a for a in settings['agents'] if a['id']=='deepseek')
            turn=ContextTurn(server.STORE,rid,'deepseek',source,source['model'],settings,'预算等待测试夹具')
            turn.compose('测试契约保持不变；无真实执行。')
            await turn.check_budget(140000,'模拟请求，不调用模型')
            server.STORE.message(rid,'deepseek','夹具验证：原请求已继续，没有重新派发任务，也没有真实调用模型或修改文件。',model=source['model'])
            server.STORE.status(rid,'complete')
        except asyncio.CancelledError:
            server.STORE.status(rid,'stopped');raise
        finally:
            server.WORK.running.pop(rid,None);server.WORK.project_busy.pop(str(root).casefold(),None)
    server.WORK.run=demo_run
    server.STORE.message(rid,'claude','成员回复位于左侧，用户回复位于右侧。此内容仅用于布局测试。')
    server.STORE.message(rid,'you','这是右侧用户气泡，用来检查头像与成员气泡是否对称。')
if args.optimization_demo:
    from workflow_state import new_state
    from preflight import check,fingerprint
    import coordination
    failed=[check('executor_start',False,'测试夹具：max_output_tokens must be between 1 and 16384','mcp')]
    async def deterministic_preflight(*a,**k):return failed
    coordination.preflight=deterministic_preflight
    wf=new_state('界面验证，不执行任务',server.STORE.messages(rid)[0]['id'],server.STORE.policy(rid,settings['cooperation_policy']))
    wf['blocker']={'checks':failed,'fingerprint':fingerprint(failed)}
    server.STORE.save_workflow(rid,wf,0)
    server.STORE.message(rid,'host',failed[0]['detail'],status='host_error',phase='宿主环境错误',actions=[{'id':'recheck','label':'复查并继续'}])
    server.STORE.message(rid,'supervisor','这是公开安排，控制 JSON 单独显示。',phase='安排与检查')
    mid=server.STORE.messages(rid)[-1]['id']
    server.STORE.event(rid,{'type':'control','message_id':mid,'control':{'action':{'type':'wait'},'checks_patch':[]}})
    server.STORE.status(rid,'blocked')
if args.execution_fault_demo:
    import hashlib,subprocess
    import coordination
    from workflow_state import new_state,current_refs
    from execution_faults import runtime_fault
    from preflight import check,code_hash
    subprocess.run(['git','init','--quiet',str(project)],check=True,capture_output=True)
    (project/'out.txt').write_text('local fixture delivery',encoding='utf-8')
    error='workspace mutation requires review; DO NOT RETRY; Agent loop exceeded max_turns (16)'
    recovery=check('recovery',False,'夹具：待核实写入保持原样','mcp')
    recovery['records']=[{'path':'out.txt','transaction_id':'fixture-transaction','sha256':'prior-version','status':'uncertain'}]
    async def deterministic_runtime_preflight(*a,**k):return [check('executor_start',True,'output=32768; tool_rounds=16','mcp'),recovery]
    async def forbid_model(*a,**k):raise AssertionError('宿主复查不能调用模型')
    coordination.preflight=deterministic_runtime_preflight;server.WORK.reply=forbid_model
    wf=new_state('宿主执行故障界面验收',server.STORE.messages(rid)[0]['id'],server.STORE.policy(rid,settings['cooperation_policy']))
    wf['code_hash']=asyncio.run(code_hash(project));server.STORE.save_workflow(rid,wf,0)
    c=coordination.Coordinator(server.WORK,rid,project,settings)
    execution=c.evidence({'kind':'execute','agent':'deepseek','status':'failed','host_error':True,'runtime_fault':runtime_fault(error),
        'error':error,'allowed_paths':['out.txt'],'acceptance_commands':['type out.txt'],'changed_files':['out.txt'],
        'file_hashes':{'out.txt':hashlib.sha256((project/'out.txt').read_bytes()).hexdigest()}})
    asyncio.run(c.execution_fault(execution))
if args.approval_demo:
    from workflow_state import new_state,adopt
    body='''# 本地文件读写最小测试方案

## 目标与范围
由指定执行者在当前项目内创建、追加并读取 `deepseek_io_test.txt`。若文件已存在，立即停止并报告，不得覆盖。

## 执行步骤
1. 创建文件并写入 `DEEPSEEK_IO_TEST_V1`，不加换行。
2. 追加 `|APPEND_OK`，不加换行。
3. 从磁盘重新读取，核对完整内容。

```text
DEEPSEEK_IO_TEST_V1|APPEND_OK
```

## 宿主验收
宿主独立检查文件内容与退出码。执行者不得仅输出文字代替真实文件写入；权限不足时报告原始错误。

```powershell
Get-Content -LiteralPath deepseek_io_test.txt -Raw
```

## 通过条件
三步操作均成功，且宿主验收输出 `PASS`、退出码为 `0`。本方案尚未执行，不能据此认定读写能力已跑通。
'''
    wf=new_state('审批文档 UI 夹具，不执行模型和用户项目',server.STORE.messages(rid)[0]['id'],server.STORE.policy(rid,selected['cooperation_policy']))
    mid=server.STORE.message(rid,'chatgpt',body)
    plan=adopt(wf,server.STORE,rid,{'plan_id':'P1','message_id':mid},{'chatgpt'})
    wf.update(awaiting_approval=plan['ref'],status='awaiting_approval')
    server.STORE.save_workflow(rid,wf,0)
    server.STORE.message(rid,'system','请确认方案 '+plan['ref']+'：\n'+body,phase='确认方案',actions=[{'id':'approve','label':'批准 '+plan['ref'],'plan_ref':plan['ref']}])
    server.STORE.status(rid,'awaiting_approval');server.WORK.project_busy[str(project).casefold()]=rid
    async def fixture_approved(*args,**kwargs):
        server.STORE.message(rid,'system','夹具验证：批准已记录，残留项目锁已清理，协作成功继续。没有调用模型或修改文件。',phase='审批验证')
        server.STORE.status(rid,'complete')
    server.WORK.supervised=fixture_approved
import uvicorn
uvicorn.run(server.app,host='127.0.0.1',port=args.port,access_log=False,log_level='warning')
