"""Version-controlled extension of the pinned, bounded coding executor.

Keeps upstream mutation/recovery/cancellation logic. Only adds private history
checkpoints and an isolated native Gemini transport child.
"""
import json
import os
from pathlib import Path
import sys


def install():
    from deepseek_mcp import agent_loop as loop,provider_process as process,child_runtime
    path=Path(os.environ['TIERED_CONTEXT_FILE'])
    soft=int(os.environ.get('TIERED_CONTEXT_SOFT','128000'))
    reserve=int(os.environ.get('TIERED_CONTEXT_OUTPUT_RESERVE','16384'))
    original_create=loop._create_agent_state
    def checkpoint(state,complete=False):
        data={'messages':state.messages,'complete':complete,'model':state.config.model}
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');os.replace(temp,path)
    def create(*args,**kwargs):
        state=original_create(*args,**kwargs)
        try:
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved.get('complete') and saved.get('model')==state.config.model:
                from context import estimate
                combined=saved['messages']+state.messages[1:]
                if estimate(combined)<soft-reserve:state.messages=combined
                else:
                    # Start a new independent history, never cut signed/tool blocks.
                    archive=path.with_name(path.stem+'-previous.json')
                    archive.write_text(json.dumps(saved,ensure_ascii=False),encoding='utf-8')
                    finals=[m['content'] for m in saved['messages'] if m.get('role')=='assistant' and not m.get('tool_calls') and m.get('content')]
                    if finals and estimate(finals[-1])+estimate(state.messages)<soft-reserve:
                        state.messages[-1]['content']='此前自己的执行结果（请核对实际文件）：\n'+finals[-1]+'\n\n'+state.messages[-1]['content']
        except (OSError,ValueError,KeyError,TypeError):pass
        checkpoint(state);return state
    loop._create_agent_state=create
    for name in ['_record_response','_execute_and_record_tool','_steer_before_tools','_finalize_or_steer']:
        original=getattr(loop,name)
        def wrapper(state,*args,_original=original,_name=name,**kwargs):
            result=_original(state,*args,**kwargs)
            checkpoint(state,_name=='_finalize_or_steer' and result is not None)
            return result
        setattr(loop,name,wrapper)
    original_budget=loop._ensure_request_budget
    def check_budget(state,request_bytes):
        from context import estimate
        if estimate({'messages':state.messages,'tools':state.tools})+reserve>soft:
            raise loop.AgentBudgetExceeded('执行上下文达到本地预算；记录与文件保留，需要新执行分支继续。')
        original_budget(state,request_bytes)
    loop._ensure_request_budget=check_budget
    if os.environ.get('ROOM_DATABASE'):
        from context import TOOL_SCHEMAS,read_originals
        from room_tools import Archive
        archive=Archive();rid=os.environ['ROOM_ID'];session=os.environ['ROOM_SESSION']
        schemas=loop.build_tool_schemas;execute=loop._execute_one_tool
        loop.build_tool_schemas=lambda allowed:schemas(allowed)+[{'type':'function','function':t} for t in TOOL_SCHEMAS]
        def room_tool(config,call,turn,**kwargs):
            if call.function.name not in {t['name'] for t in TOOL_SCHEMAS}:return execute(config,call,turn,**kwargs)
            try:
                row=archive.db.execute('SELECT role,data FROM agent_sessions WHERE key=? AND room=?',(session,rid)).fetchone()
                if row is None or row['role']=='executor':raise ValueError('No room history permission')
                result=read_originals(archive,rid,json.loads(row['data'])['active_boundary'],call.function.name,json.loads(call.function.arguments))
                return json.dumps(result,ensure_ascii=False)
            except (ValueError,TypeError,KeyError) as e:return 'ERROR: '+str(e)
        loop._execute_one_tool=room_tool
    original_argv=process.isolated_child_argv
    def argv(module,*arguments):
        if module!='deepseek_mcp.provider_child':return original_argv(module,*arguments)
        roots=[str(p) for p in child_runtime.child_import_roots()]+[str(Path(__file__).parent)]
        script=str(Path(__file__).with_name('native_provider_child.py'))
        bootstrap=('import runpy,sys;count=int(sys.argv[1]);sys.path[:0]=sys.argv[2:2+count];'
            'script=sys.argv[2+count];sys.argv=[script,*sys.argv[3+count:]];runpy.run_path(script,run_name="__main__")')
        return [sys.executable,'-I','-S','-c',bootstrap,str(len(roots)),*roots,script,*arguments]
    process.isolated_child_argv=argv
