"""Upstream workspace tool child with host-managed Git trust and scratch folders.

The upstream Bash tool runs commands with HOME/USERPROFILE set to the workspace
and drops TEMP/APPDATA. Git then reads the workspace as the user's home, never
sees any trust the user configured, and Windows tools write AppData/temporary
folders into the project. This wrapper keeps the upstream isolation but points
Git at the host's per-project trust file and moves scratch output outside.
"""
import os
from deepseek_mcp import bash_tool, tool_child

_original=bash_tool._host_environment


def host_environment(workspace):
    env=_original(workspace)
    config=os.environ.get('TIERED_GIT_CONFIG')
    if config:
        from pathlib import Path
        trusted="'"+Path(workspace).resolve().as_posix().replace("'","'\\''")+"'"
        env['GIT_CONFIG_GLOBAL']=config
        env['GIT_CONFIG_PARAMETERS']="'safe.directory'="+trusted
        env['GIT_TERMINAL_PROMPT']='0'
    scratch=os.environ.get('TIERED_TOOL_SCRATCH')
    if scratch:
        folders={'TEMP':os.path.join(scratch,'Temp'),'TMP':os.path.join(scratch,'Temp')}
        if os.name=='nt':
            folders.update(APPDATA=os.path.join(scratch,'AppData','Roaming'),
                           LOCALAPPDATA=os.path.join(scratch,'AppData','Local'))
        for value in folders.values():os.makedirs(value,exist_ok=True)
        env.update(folders)
    return env


bash_tool._host_environment=host_environment


def install_output_archive():
    if not os.environ.get('ROOM_OUTPUT_PREVIEW') or not os.environ.get('ROOM_DATABASE'):return
    import threading
    from output_archive import present_environment
    from deepseek_mcp import tools
    local=threading.local();original_run=bash_tool._run_on_trusted_host;original_format=bash_tool._format_result
    original_execute=bash_tool.execute_bash
    # Keep the upstream process/cancellation boundary; capture before its formatter truncates.
    bash_tool.MAX_STREAM_BYTES=8_000_000
    def run(command,config,timeout):
        result=original_run(command,config,timeout)
        text=f'[exit {result.returncode}]\n--- stdout ---\n'+result.stdout.decode('utf-8',errors='replace')
        if result.stderr:text+='\n--- stderr ---\n'+result.stderr.decode('utf-8',errors='replace')
        complete=result.stdout_total==len(result.stdout) and result.stderr_total==len(result.stderr)
        local.preview=present_environment(command,result.returncode,text,'bash',complete)
        return result
    def formatted(result):return getattr(local,'preview',None) or original_format(result)
    def execute(*args,**kwargs):
        local.preview=None
        result=original_execute(*args,**kwargs)
        if local.preview and result.startswith('ERROR: command timed out'):return result+'\n'+local.preview
        return result
    bash_tool._run_on_trusted_host=run;bash_tool._format_result=formatted
    bash_tool.execute_bash=tools.execute_bash=execute


install_output_archive()
if __name__=='__main__':tool_child.main()
