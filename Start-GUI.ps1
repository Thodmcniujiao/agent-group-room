$ErrorActionPreference = 'Stop'
$guiPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $guiPython)) { throw 'Run .\Install.ps1 first.' }
Start-Process -FilePath $guiPython -ArgumentList ('"' + (Join-Path $PSScriptRoot 'gui\launch.py') + '"') -WindowStyle Hidden
