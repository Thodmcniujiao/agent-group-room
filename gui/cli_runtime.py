"""Locate installed CLIs even when Explorer has an older or shorter PATH."""
import os
from pathlib import Path
import shutil


def registered_path():
    """Read Windows installation paths without changing the user's environment."""
    if os.name!='nt':return ''
    import winreg
    values=[]
    for hive,key in [(winreg.HKEY_CURRENT_USER,'Environment'),
        (winreg.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')]:
        try:
            with winreg.OpenKey(hive,key) as handle:
                value,_=winreg.QueryValueEx(handle,'Path')
                if isinstance(value,str):values.append(os.path.expandvars(value))
        except OSError:pass
    return os.pathsep.join(values)


def find_cli(name):
    if name not in ['codex','claude']:raise ValueError('Unsupported CLI')
    binary=shutil.which(name)
    if binary:return binary
    binary=shutil.which(name,path=registered_path())
    if binary:return binary
    home=Path.home()
    local=Path(os.environ.get('LOCALAPPDATA',str(home/'AppData/Local')))
    roaming=Path(os.environ.get('APPDATA',str(home/'AppData/Roaming')))
    for folder in [home/'.local/bin',home/'.cargo/bin',local/'Microsoft/WinGet/Links',roaming/'npm']:
        binary=shutil.which(name,path=str(folder))
        if binary:return binary
    if name=='codex':
        # Codex desktop versions use a changing hash directory outside Explorer's PATH.
        try:
            candidates=list((local/'OpenAI/Codex/bin').glob('*/codex.exe'))
            candidates.sort(key=lambda p:p.stat().st_mtime,reverse=True)
            for path in candidates:
                if path.is_file():return str(path)
        except OSError:pass
    return None
