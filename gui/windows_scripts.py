"""Write PowerShell UTF-8 BOM inside the upstream atomic mutation transaction."""
from pathlib import Path


def compatible_text(path,content):
    if Path(path).suffix.lower() in ['.ps1','.psm1','.psd1']:
        return '\ufeff'+content.lstrip('\ufeff')
    return content


def install():
    from deepseek_mcp import tools
    original=tools._atomic_write_workspace_text
    if getattr(original,'_room_powershell_encoding',False):return
    def write(workspace,label,content,**kwargs):
        # Upstream hashes and journals the resulting bytes before committing them.
        return original(workspace,label,compatible_text(label,content),**kwargs)
    write._room_powershell_encoding=True
    tools._atomic_write_workspace_text=write
