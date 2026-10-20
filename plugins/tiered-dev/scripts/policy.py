"""Shared contract policy; this is validation, not an OS sandbox."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

STATE = Path.home() / '.local' / 'share' / 'tiered-dev'
SECTIONS = {
    '目标 / Goal': r'目标|Goal|Objective',
    '允许修改 / Allowed paths': r'允许修改|Allowed (?:paths|changes|files)',
    '验收命令 / Acceptance': r'验收命令|Acceptance(?: commands)?',
    '回传格式 / Return format': r'回传格式|Return format|Report format',
}


def validate_contract(task):
    if not isinstance(task, str) or len(task) > 100_000:
        raise ValueError('Contract must be text, at most 100000 characters.')
    headings = re.findall(r'^#{1,6}\s+(.+)$', task, re.M)
    missing = [name for name, pattern in SECTIONS.items()
               if not any(re.search(pattern, h, re.I) for h in headings)]
    if missing:
        raise ValueError('Missing contract sections: ' + ', '.join(missing))
    sections = re.split(r'^#{1,6}\s+.+$', task, flags=re.M)[1:]
    for heading, content in zip(headings, sections):
        if any(re.search(pattern, heading, re.I) for pattern in SECTIONS.values()):
            if not content.strip():
                raise ValueError('Empty contract section: ' + heading)


def audit(event, **fields):
    # Never record task text, source, environment values, or provider responses.
    directory = STATE / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    record = {'time': datetime.now(timezone.utc).isoformat(), 'event': event, **fields}
    with (directory / 'delegations.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')


def load_user_key():
    from preferences import get_key
    configured = get_key()
    if configured:
        os.environ['DEEPSEEK_API_KEY'] = configured
        return
    if os.name == 'nt' and not os.environ.get('DEEPSEEK_API_KEY'):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                value, _ = winreg.QueryValueEx(key, 'DEEPSEEK_API_KEY')
            if isinstance(value, str) and value.strip():
                os.environ['DEEPSEEK_API_KEY'] = value.strip()
        except FileNotFoundError:
            pass
