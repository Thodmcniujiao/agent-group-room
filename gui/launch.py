"""Start the desktop window; keep the loopback backend reusable across launches."""
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
STATE=Path.home()/'.local/share/tiered-dev/gui'


def healthy(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:
            return json.load(response).get('service')=='tiered-dev-gui'
    except Exception:
        return False


def ensure_service():
    """Return a healthy local backend URL without opening any user interface."""
    STATE.mkdir(parents=True,exist_ok=True)
    try:
        old=json.loads((STATE/'service.json').read_text(encoding='utf-8'))
        if healthy(old['port']):
            return f"http://127.0.0.1:{old['port']}"
    except (OSError,ValueError,KeyError): pass
    port=None
    for candidate in range(49315,49326):
        if healthy(candidate):
            return f'http://127.0.0.1:{candidate}'
        with socket.socket() as sock:
            try: sock.bind(('127.0.0.1',candidate));port=candidate;break
            except OSError: pass
    if port is None: raise RuntimeError('无法找到可用的本机端口。')
    python=Path(os.environ['TIERED_GUI_PYTHON']) if os.environ.get('TIERED_GUI_PYTHON') else ROOT/'.venv/Scripts/pythonw.exe'
    if not python.is_file(): raise RuntimeError('请先在仓库目录运行 Install.ps1，建立本机运行环境。')
    env={**os.environ,'TIERED_GUI_PORT':str(port),'PYTHONIOENCODING':'utf-8'}
    with (STATE/'service.log').open('ab') as log:
        subprocess.Popen([str(python),str(Path(__file__).with_name('server.py'))],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=env,creationflags=0x08000000,close_fds=True)
    for _ in range(80):
        if healthy(port):
            return f'http://127.0.0.1:{port}'
        time.sleep(0.25)
    raise RuntimeError('本机服务启动未完成。日志位于 '+str(STATE/'service.log'))


def main():
    STATE.mkdir(parents=True,exist_ok=True)
    # pythonw has no console streams. Keep native-host diagnostics available.
    if sys.stdout is None:sys.stdout=(STATE/'desktop.log').open('a',encoding='utf-8',buffering=1)
    if sys.stderr is None:sys.stderr=sys.stdout
    if '--browser' in sys.argv[1:]:
        webbrowser.open(ensure_service())
    else:
        from desktop import run
        run(ensure_service,STATE)


if __name__=='__main__':
    try: main()
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(0,str(exc),'代理协作室',0x10)
