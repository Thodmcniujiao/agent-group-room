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
rid=server.STORE.create('上下文分流 · 界面测试',str(state))['id'];server.ROOMS.seed(rid,selected,legacy=True)
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
import uvicorn
uvicorn.run(server.app,host='127.0.0.1',port=args.port,access_log=False,log_level='warning')
