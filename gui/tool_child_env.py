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
        env['GIT_CONFIG_GLOBAL']=config
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
if __name__=='__main__':tool_child.main()
