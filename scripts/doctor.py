"""Exercise actual MCP stdio clients; live probes are explicitly opt-in."""
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'plugins' / 'tiered-dev' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from policy import load_user_key


def text_response(result):
    return '\n'.join(block.text for block in result.content if block.type == 'text')


def contract(goal, paths, acceptance):
    return f'# Smoke test\n## 目标\n{goal}\n## 允许修改\n{paths}\n## 验收命令\n{acceptance}\n## 回传格式\nSTATUS, FILES, TESTS. At most 8 lines.'


async def main(options):
    load_user_key()
    print('DeepSeek credential: ' + ('present' if os.environ.get('DEEPSEEK_API_KEY') else 'MISSING'))
    with tempfile.TemporaryDirectory(prefix='tiered-doctor-') as directory:
        workspace = Path(directory) / 'project'
        workspace.mkdir()
        (workspace / 'input.txt').write_text('TIERED_INPUT_OK\n', encoding='utf-8')
        for service, script in [('deepseek', 'deepseek_server.py'), ('review', 'review_server.py')]:
            params = StdioServerParameters(command=sys.executable, args=[str(SCRIPTS / script)],
                cwd=str(workspace), env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'DEEPSEEK_WORKSPACE': str(workspace)})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=1000)) as session:
                    await session.initialize()
                    tools = [tool.name for tool in (await session.list_tools()).tools]
                    health = text_response(await session.call_tool('ping', {}))
                    print(service + ' MCP: PASS (' + str(len(tools)) + ' tools)')
                    print(health)
                    if service == 'deepseek':
                        blocked = await session.call_tool('delegate_to_deepseek', {'task': 'no contract'})
                        assert blocked.isError and 'Missing contract' in text_response(blocked), text_response(blocked)
                        print('Contract enforcement: PASS')
                        if options.deepseek_live:
                            if not os.environ.get('DEEPSEEK_API_KEY'):
                                raise RuntimeError('Set DEEPSEEK_API_KEY locally before live verification.')
                            result = await session.call_tool('delegate_to_deepseek_readonly', {'task': contract(
                                'Use Read to read input.txt and report its exact contents. Do not run commands.',
                                'None; read only.', 'Host verifies response contains TIERED_INPUT_OK.'), 'model': 'flash'})
                            summary = text_response(result)
                            assert not result.isError and 'TIERED_INPUT_OK' in summary, summary
                            print('DeepSeek Flash live read: PASS')
                            result = await session.call_tool('delegate_to_deepseek', {'task': contract(
                                'Use Write to create output.txt containing exactly TIERED_WRITE_OK followed by a newline. Do not run commands.',
                                'output.txt only.', 'Host independently reads output.txt and compares exact content.'), 'model': 'flash'})
                            assert not result.isError, text_response(result)
                            assert (workspace / 'output.txt').read_text(encoding='utf-8') == 'TIERED_WRITE_OK\n', text_response(result)
                            recovery = json.loads(text_response(await session.call_tool('get_deepseek_recovery', {})))
                            assert recovery['ok'] and recovery['pending'], recovery
                            ids = [item['transaction_id'] for item in recovery['pending']]
                            acknowledged = json.loads(text_response(await session.call_tool('acknowledge_deepseek_mutations', {'transaction_ids': ids})))
                            assert acknowledged['ok'] and not acknowledged['pending'], acknowledged
                            print('DeepSeek Flash live write + recovery acknowledgement: PASS')
                    elif options.review_live:
                        for tool in ['codex_review', 'claude_review']:
                            result = await session.call_tool(tool, {'task': 'Report the marker in the supplied input.txt snapshot and VERDICT: PASS if it is readable. Do nothing else.', 'project_dir': str(workspace), 'paths': ['input.txt']})
                            data = json.loads(text_response(result))
                            assert data.get('status') == 'DONE' and 'TIERED_INPUT_OK' in data.get('review', ''), data
                            assert (workspace / 'input.txt').read_text(encoding='utf-8') == 'TIERED_INPUT_OK\n'
                            print(tool + ' live: PASS [' + data['model'] + ']')
    print('Selected checks: PASS')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--deepseek-live', action='store_true')
    parser.add_argument('--review-live', action='store_true')
    try:
        asyncio.run(main(parser.parse_args()))
    except BaseException as exc:
        def leaves(error):
            if isinstance(error, BaseExceptionGroup):
                return [item for child in error.exceptions for item in leaves(child)]
            return [str(error)]
        print('FAILED: ' + ' | '.join(leaves(exc)), file=sys.stderr)
        raise SystemExit(1)
