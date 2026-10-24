"""Version-controlled extension of the pinned, bounded coding executor.

Keeps upstream mutation/recovery/cancellation logic. Only adds private history
checkpoints and an isolated native Gemini transport child.
"""
import json
import os
from pathlib import Path
import sys


def install():
    from deepseek_mcp import agent_loop as loop,provider_process as process
    install_usage()
    path=Path(os.environ['TIERED_CONTEXT_FILE'])
    soft=int(os.environ.get('TIERED_CONTEXT_SOFT','128000'))
    reserve=int(os.environ.get('TIERED_CONTEXT_OUTPUT_RESERVE','16384'))
    original_create=loop._create_agent_state
    def checkpoint(state,complete=False):
        data={'messages':state.messages,'complete':complete,'model':state.config.model}
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');os.replace(temp,path)
    def create(*args,**kwargs):
        state=original_create(*args,**kwargs)
        instructions=os.environ.get('TIERED_EXECUTOR_INSTRUCTIONS')
        if instructions:state.messages[0]['content']+='\n'+instructions
        try:
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved.get('complete') and saved.get('model')==state.config.model:
                from context import estimate
                combined=saved['messages']+state.messages[1:]
                if os.environ.get('TIERED_CONTEXT_FORCE')=='1' or estimate(combined)<soft-reserve:state.messages=combined
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
        if os.environ.get('TIERED_CONTEXT_FORCE')!='1' and estimate({'messages':state.messages,'tools':state.tools})+reserve>soft:
            raise loop.AgentBudgetExceeded('执行上下文达到本地预算；记录与文件保留，需要新执行分支继续。')
        original_budget(state,request_bytes)
    loop._ensure_request_budget=check_budget
    if os.environ.get('ROOM_DATABASE'):
        from context import TOOL_SCHEMAS,EVIDENCE_TOOL,DIRECTORY_TOOL,read_originals
        from output_archive import OUTPUT_TOOL
        from readonly_project import PROJECT_SCHEMAS
        from room_tools import Archive
        archive=Archive();rid=os.environ['ROOM_ID'];session=os.environ['ROOM_SESSION']
        schemas=loop.build_tool_schemas;execute=loop._execute_one_tool
        session_row=archive.db.execute('SELECT role,data FROM agent_sessions WHERE key=? AND room=?',(session,rid)).fetchone()
        enabled=json.loads(session_row['data']).get('tool_names',[]) if session_row else []
        room_schemas=TOOL_SCHEMAS+([EVIDENCE_TOOL] if session_row and session_row['role']=='supervisor' else []) if session_row and session_row['role']!='executor' else []
        room_schemas+=[t for t in [DIRECTORY_TOOL,*PROJECT_SCHEMAS,OUTPUT_TOOL] if t['name'] in enabled]
        loop.build_tool_schemas=lambda allowed:schemas(allowed)+[{'type':'function','function':t} for t in room_schemas]
        def room_tool(config,call,turn,**kwargs):
            if call.function.name not in {t['name'] for t in room_schemas}:return execute(config,call,turn,**kwargs)
            try:
                result=archive.read(session,rid,call.function.name,json.loads(call.function.arguments))
                return json.dumps(result,ensure_ascii=False)
            except (ValueError,TypeError,KeyError) as e:return 'ERROR: '+str(e)
        loop._execute_one_tool=room_tool
    original_argv=process.isolated_child_argv
    def argv(module,*arguments):
        if module!='deepseek_mcp.provider_child':return original_argv(module,*arguments)
        return wrapped_child_argv('native_provider_child.py',arguments)
    process.isolated_child_argv=argv


def install_usage():
    """Audit each hardened provider attempt, including retries and killed requests."""
    target=os.environ.get('TIERED_USAGE_FILE')
    if not target:return
    from deepseek_mcp import provider_process,provider_retry
    from api_protocol import is_gemini
    from vertex_protocol import is_vertex
    from calibration import estimate_v2
    import threading,uuid
    local=threading.local();decode=provider_process._decode_response;request=provider_retry.request_in_subprocess
    environment=provider_process._provider_environment
    def provider_environment():
        values=environment()
        for name in ['TIERED_CALL_LOG_SPOOL','TIERED_CALL_LOG_CONTROL']:
            if os.environ.get(name):values[name]=os.environ[name]
        if getattr(local,'ref',None):values['TIERED_CALL_REQUEST_REF']=local.ref
        return values
    provider_process._provider_environment=provider_environment
    def decoded(raw):
        try:
            body=json.loads(raw).get('response',{})
            local.raw=body.get('gemini_usage',{}) if getattr(local,'adapter','')=='gemini' else body.get('usage',{})
        except (ValueError,TypeError,AttributeError):pass
        return decode(raw)
    def requested(config,messages,tools,*args,**kwargs):
        ref=uuid.uuid4().hex;local.ref=ref;local.raw={};local.adapter='gemini' if is_gemini(config.base_url) or is_vertex(config.base_url) else 'openai_compatible'
        estimate=estimate_v2({'messages':messages,'tools':tools})
        def emit(status):
            value={'request_ref':ref,'adapter':local.adapter,'raw':local.raw,'status':status,'full_estimate':estimate}
            with open(target,'a',encoding='utf-8') as stream:stream.write(json.dumps(value,ensure_ascii=False)+'\n')
        emit('interrupted');status='failed'
        try:
            result=request(config,messages,tools,*args,**kwargs)
            if result[0] is not None:status='complete'
            return result
        except provider_process.ProviderRequestCancelled:status='interrupted';raise
        finally:emit(status)
    provider_process._decode_response=decoded;provider_retry.request_in_subprocess=requested


def wrapped_child_argv(name,arguments):
    from deepseek_mcp import child_runtime
    roots=[str(p) for p in child_runtime.child_import_roots()]+[str(Path(__file__).parent)]
    script=str(Path(__file__).with_name(name))
    bootstrap=('import runpy,sys;count=int(sys.argv[1]);sys.path[:0]=sys.argv[2:2+count];'
        'script=sys.argv[2+count];sys.argv=[script,*sys.argv[3+count:]];runpy.run_path(script,run_name="__main__")')
    return [sys.executable,'-I','-S','-c',bootstrap,str(len(roots)),*roots,script,*arguments]


def install_git_bridge():
    """Give every executor (coding or read-only) a working path to Git.

    1. Bash: the isolated tool child gets the host trust file and scratch folders.
    2. A dedicated read-only `Git` tool executed by the host, so the executor
       can inspect status/diff/log even when Bash is not used.
    """
    config=os.environ.get('TIERED_GIT_CONFIG')
    if not config:return
    from deepseek_mcp import agent_loop as loop,tool_process
    import logging
    from git_access import TOOL_SCHEMA,run_readonly
    original_environment=tool_process._environment
    def environment():
        values=original_environment()
        for name in ['TIERED_GIT_CONFIG','TIERED_TOOL_SCRATCH','ROOM_DATABASE','ROOM_ID','ROOM_SESSION','ROOM_OUTPUT_PREVIEW']:
            if os.environ.get(name):values[name]=os.environ[name]
        return values
    tool_process._environment=environment
    original_argv=tool_process.isolated_child_argv
    def argv(module,*arguments):
        if module!='deepseek_mcp.tool_child':return original_argv(module,*arguments)
        return wrapped_child_argv('tool_child_env.py',arguments)
    tool_process.isolated_child_argv=argv
    schemas=loop.build_tool_schemas;execute=loop._execute_one_tool
    loop.build_tool_schemas=lambda allowed:schemas(allowed)+[{'type':'function','function':TOOL_SCHEMA}]
    log=logging.getLogger('deepseek_mcp.agent_loop')
    def git_tool(config_value,call,turn,**kwargs):
        if call.function.name!=TOOL_SCHEMA['name']:return execute(config_value,call,turn,**kwargs)
        log.info('Turn %d tool_call: %s arg_count=%d',turn,'Git',1)
        try:args=json.loads(call.function.arguments).get('args')
        except (ValueError,AttributeError):return 'ERROR: Git 工具参数必须是 {"args": [...]}。'
        return run_readonly(config_value.workspace,args,os.environ.get('TIERED_GIT_CONFIG') or config)
    loop._execute_one_tool=git_tool
