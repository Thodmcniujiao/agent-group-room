"""Local configuration and MCP health probes. Never request model generation."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'gui'), str(ROOT / 'plugins/tiered-dev/scripts')]

import preferences
from deepseek_mcp import config, windows_file_io


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['before', 'after'], required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'phase': args.phase, 'model_generation_requests': 0, 'checks': []}

    def check(name, operation):
        try:
            value = operation()
            report['checks'].append({'name': name, 'passed': True, 'result': value})
        except Exception as exc:
            report['checks'].append({'name': name, 'passed': False,
                                     'error': str(exc), 'cause': str(exc.__cause__)})

    for path, directory in [(config.CONFIG_PATH.parent, True), (config.CONFIG_PATH, False)]:
        check('secure_path_' + ('directory' if directory else 'file'),
              lambda p=path, d=directory: windows_file_io.validate_private_path(p, directory=d))

    def secure_read():
        text = config._read_config_text()
        json.loads(text)
        return {'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'valid_json': True}

    check('secure_config_read', secure_read)
    if args.phase == 'after':
        settings = preferences.load()
        agent = next(a for a in settings['agents'] if a['backend'] == 'deepseek')
        key = preferences.get_member_key(agent)
        if not key:
            raise RuntimeError('Saved DeepSeek credential is unavailable')
        os.environ.update(DEEPSEEK_WORKSPACE=str(args.workspace), DEEPSEEK_API_KEY=key,
                          TIERED_MEMBER_API_KEY=key, TIERED_MEMBER_BASE_URL=agent['base_url'],
                          TIERED_DEEPSEEK_MODEL=agent['model'], DEEPSEEK_MODE='auto')

        def selected_config():
            import deepseek_server
            cfg = deepseek_server._selected_config()
            return {'workspace': str(cfg.workspace), 'model': cfg.model,
                    'credential_available': bool(cfg.api_key)}

        check('production_executor_config', selected_config)

        async def health():
            from engines import deepseek_session, read_response
            async with deepseek_session(args.workspace, agent['model'], member=agent) as session:
                tools = await session.list_tools()
                assert 'ping' in {t.name for t in tools.tools}
                text = read_response(await session.call_tool('ping', {}))
                if 'NOT_CONFIGURED' in text or 'mode=auto' not in text:
                    raise RuntimeError(text)
                return {'initialized': True, 'tool_count': len(tools.tools), 'ping': text,
                        'tool_calls': ['ping']}

        check('production_mcp_startup_and_ping', lambda: asyncio.run(health()))

    report['passed'] = all(c['passed'] for c in report['checks'])
    target = Path(__file__).with_name(args.phase + '.json')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
