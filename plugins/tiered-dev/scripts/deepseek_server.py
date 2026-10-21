"""Pinned upstream MCP server with contract checks on all delegation APIs."""
import time
import os
import json
import logging
import re
import sys
from dataclasses import replace
from typing import Literal

from policy import audit, load_user_key, validate_contract

load_user_key()
if os.environ.get('TIERED_MEMBER_API_KEY'):
    os.environ['DEEPSEEK_API_KEY']=os.environ['TIERED_MEMBER_API_KEY']
from deepseek_mcp import server as upstream
from preferences import load, model_id

_original_load = upstream._load_config
def _selected_config(*args, **kwargs):
    cfg = _original_load(*args, **kwargs)
    chosen = os.environ.get('TIERED_DEEPSEEK_MODEL')
    if not chosen and (not args or len(args) < 2 or args[1] == 'flash'):
        chosen = load()['models']['deepseek']
    changes={}
    if chosen:changes['model']=model_id(chosen)
    if os.environ.get('TIERED_MEMBER_BASE_URL'):
        changes.update(base_url=os.environ['TIERED_MEMBER_BASE_URL'],api_key=os.environ['TIERED_MEMBER_API_KEY'])
        if 'api.deepseek.com' not in changes['base_url']:
            changes['reasoning_effort']='provider-default'
    if os.environ.get('TIERED_CONTEXT_FILE'):changes['max_output_tokens']=int(os.environ.get('TIERED_CONTEXT_OUTPUT','16384'))
    if os.environ.get('TIERED_CONTEXT_TOOL_ROUNDS'):changes['max_turns']=int(os.environ['TIERED_CONTEXT_TOOL_ROUNDS'])
    if 'TIERED_MEMBER_EFFORT' in os.environ:
        from preferences import effort_id
        changes['reasoning_effort']=effort_id(os.environ['TIERED_MEMBER_EFFORT']) or 'provider-default'
    return replace(cfg,**changes) if changes else cfg
upstream._load_config = _selected_config

from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'gui'))
if os.environ.get('TIERED_CONTEXT_FILE'):
    from execution_context import install
    install()
if os.environ.get('TIERED_GIT_CONFIG'):
    from execution_context import install_git_bridge
    install_git_bridge()


class ProgressHandler(logging.Handler):
    def emit(self, record):
        match = re.search(r'Turn (\d+) tool_call: (\w+)', record.getMessage())
        if match:
            event = {'kind': 'tool', 'turn': int(match[1]), 'tool': match[2]}
            sys.stderr.write('TIERED_EVENT '+json.dumps(event)+'\n')
            sys.stderr.flush()

if os.environ.get('TIERED_PROGRESS') == '1':
    progress_logger = logging.getLogger('deepseek_mcp.agent_loop')
    progress_logger.addHandler(ProgressHandler())
    progress_logger.setLevel(logging.INFO)


def checked(task, model, tool):
    validate_contract(task)
    audit('start', tool=tool, model=model, task_chars=len(task))
    return time.monotonic()


def finished(result, started, model, tool):
    text = str(result)
    status = 'returned'
    if text.startswith('ERROR') or '"ok": false' in text:
        status = 'error'
    audit('return', tool=tool, model=model, seconds=round(time.monotonic()-started, 2), status=status)
    return result


async def delegate_to_deepseek(task: str, context: str = '', model: Literal['flash', 'pro'] = 'flash') -> str:
    """Execute a complete task contract; Flash by default, Pro for escalation."""
    stamp = checked(task, model, 'delegate_to_deepseek')
    result = await upstream.delegate_to_deepseek(task, context, model)
    return finished(result, stamp, model, 'delegate_to_deepseek')


async def delegate_to_deepseek_readonly(task: str, context: str = '', model: Literal['flash', 'pro'] = 'flash') -> str:
    """Analyze files only; requires a contract, no commands or writes."""
    stamp = checked(task, model, 'delegate_to_deepseek_readonly')
    result = await upstream.delegate_to_deepseek_readonly(task, context, model)
    return finished(result, stamp, model, 'delegate_to_deepseek_readonly')


def start_deepseek(task: str, context: str = '', model: Literal['flash', 'pro'] = 'flash') -> str:
    """Start a steerable coding job from a complete task contract."""
    stamp = checked(task, model, 'start_deepseek')
    return finished(upstream.start_deepseek(task, context, model), stamp, model, 'start_deepseek')


def start_deepseek_readonly(task: str, context: str = '', model: Literal['flash', 'pro'] = 'flash') -> str:
    """Start a steerable file-only analysis job from a complete task contract."""
    stamp = checked(task, model, 'start_deepseek_readonly')
    return finished(upstream.start_deepseek_readonly(task, context, model), stamp, model, 'start_deepseek_readonly')


for name in ('delegate_to_deepseek', 'delegate_to_deepseek_readonly', 'start_deepseek', 'start_deepseek_readonly'):
    upstream.mcp.remove_tool(name)
    annotation = upstream._READONLY_AGENT_EXECUTION if 'readonly' in name else upstream._AGENT_EXECUTION
    upstream.mcp.add_tool(globals()[name], name=name, annotations=annotation)

if __name__ == '__main__':
    upstream.main()
