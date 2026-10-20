param(
    [string]$Python = 'py',
    [switch]$NoShortcut
)
$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtime)) {
    $pythonArgs = if ($Python -eq 'py') { @('-3.12') } else { @() }
    $versionText = & $Python @pythonArgs -c 'import sys; print(str(sys.version_info.major)+"."+str(sys.version_info.minor))'
    if ($LASTEXITCODE -ne 0 -or "$versionText".Trim() -ne '3.12') {
        throw 'Python 3.12 is required. Install it or provide -Python C:\path\to\python.exe.'
    }
    & $Python @pythonArgs -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& $runtime -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath('DesktopDirectory')
    $wsh = New-Object -ComObject WScript.Shell
    $shortcut = $wsh.CreateShortcut((Join-Path $desktop 'Agent Group Room.lnk'))
    $shortcut.TargetPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    $shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'gui\launch.py') + '"'
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = (Join-Path $PSScriptRoot 'gui\icon.ico') + ',0'
    $shortcut.Description = 'Configurable agent team with an independent supervisor'
    $shortcut.Save()
}
Write-Host 'Installed. Run .\Start-GUI.ps1 or open the Agent Group Room shortcut.'
