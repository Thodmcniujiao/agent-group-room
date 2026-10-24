"""Isolated UI acceptance fixture. No credentials, remote providers or model calls."""
import argparse
import json
import os
from pathlib import Path
import sys

parser=argparse.ArgumentParser();parser.add_argument('--state',type=Path,required=True);parser.add_argument('--port',type=int,default=49342)
args=parser.parse_args();state=args.state.resolve();state.mkdir(parents=True,exist_ok=True)
os.environ.update(TIERED_GUI_DATA_DIR=str(state/'gui'),TIERED_GUI_PORT=str(args.port))
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'gui'))
from engines import preferences
preferences.STATE=state;preferences.PREFS=state/'preferences.json';preferences.SECRET=state/'deepseek.dpapi';preferences.PROFILE=state/'profile.toml'
selected=preferences.load()
import server
from optimization_settings import optimization_settings
from context import ContextTurn
from member_summary import extract
from output_archive import present
from usage import record

server.MODELS.request_refresh=lambda *a,**k:[]
server.USAGE.request_refresh=lambda *a,**k:[]
async def connections(*a,**k):return {agent['id']:{'ready':True,'detail':'验收夹具，无真实模型调用'} for agent in selected['agents']}
server.roster.connections=connections
if server.STORE.rooms():raise RuntimeError('UI fixture needs an empty state directory')
project=state/'project';project.mkdir();(project/'demo.py').write_text('print("fixture")\n',encoding='utf-8')
rid=server.STORE.create('摘要与回取 · 验收夹具',str(project))['id'];server.ROOMS.seed(rid,selected,legacy=True)
server.ROOMS.update(rid,{'optimization':optimization_settings({'enabled':True})})
settings=server.ROOMS.load(rid)
server.STORE.logs.begin(rid,'fixture','supervisor')
server.STORE.message(rid,'you','这是隔离验收协作室。保留用户约束和未决异议；所有数据为测试夹具。')
text='评审记录：必须检查失败尝试，完整原文可以分段回取。'*600+'\n摘要：立场=支持｜要点=保留执行证据｜未决=尾部错误尚待核对\n表态：反对 P1@1：仍有未决问题'
mid=server.STORE.message(rid,'claude',text);server.STORE.set_summary(mid,extract(text,settings))
monitor=next(a for a in settings['agents'] if a['id']==settings['supervisor']['provider'])
turn=ContextTurn(server.STORE,rid,'supervisor',monitor,settings['supervisor']['model'],settings,'夹具摘要投递')
from collaboration_log import call_for
audit=call_for(turn,'chatgpt')
audit.emit('cli_stdin',{'fixture':True,'text':'隔离界面夹具：不是真实模型请求'})
turn.compose('了解较早记录；夹具不会调用模型');turn.complete('夹具投递结束')
record(turn,'claude',{'input_tokens':100,'cache_read_input_tokens':800,'cache_creation_input_tokens':100,'output_tokens':80},request_ref='fixture-usage')
audit.finish('complete','隔离夹具输出，无真实模型调用')
server.STORE.logs.end(rid,'complete')
turn.read('group_get_message',{'message_id':mid});turn.complete('夹具部分回取；未称已看完全文')
body='test output\n'+('PASS fixture\n'*2000)+'TAIL-ERROR: fixture exit 3\n'
preview,reference=present(server.STORE,rid,'host','fixture acceptance',3,body,settings,kind='acceptance')
server.STORE.event(rid,{'type':'test','command':'fixture acceptance','exit_code':3,'output':preview,'output_id':reference['id']})
turn.read('output_read',{'id':reference['id'],'limit':100});turn.record('delivered')
server.STORE.status(rid,'idle')
other=server.STORE.create('优化关闭 · 对照夹具',str(project))['id'];server.ROOMS.seed(other,selected,legacy=True)
(state/'fixture.json').write_text(json.dumps({'room':rid,'other_room':other,'output_id':reference['id'],'message_id':mid,'live_model_calls':False}),encoding='utf-8')
if __name__=='__main__':
    import uvicorn
    uvicorn.run(server.app,host='127.0.0.1',port=args.port,log_level='warning',access_log=False)
