"""Claude hooks scoped to tiered workspaces, plus contract validation."""
import json
from pathlib import Path
import re
import sys
from policy import audit, validate_contract


def main():
    mode = sys.argv[1]
    payload = json.load(sys.stdin)
    tool = payload.get('tool_name', '')
    args = payload.get('tool_input', {})
    if mode == 'contract':
        validate_contract(args.get('task', ''))
    elif mode == 'guard':
        cwd = Path(payload.get('cwd', '.')).resolve()
        if not any((p / '.tiered').is_dir() for p in (cwd, *cwd.parents)):
            return
        command = args.get('command', '')
        blocked = [r'\bgit\s+(push|commit|reset\s+--hard|clean\s+-[a-z]*f)',
                   r'\b(rm\s+-[a-z]*r[a-z]*f|format(?:\.com)?|diskpart|mkfs)\b',
                   r'\b(Remove-Item|rmdir|rd)\b.*(?:-Recurse|/s)',
                   r'\b(npm\s+publish|twine\s+upload|gh\s+(release|pr\s+merge))\b']
        if any(re.search(p, command, re.I) for p in blocked):
            raise ValueError('Tiered workspace: destructive commands and publishing require a separate user action outside this workflow.')
    elif mode == 'log':
        audit('host_tool_return', tool=tool)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
