param([switch]$Browser)
$ErrorActionPreference = 'Stop'
$guiPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $guiPython)) { throw 'Run .\Install.ps1 first.' }
$launchArgs = '"' + (Join-Path $PSScriptRoot 'gui\launch.py') + '"'
if ($Browser) { $launchArgs += ' --browser' }
Start-Process -FilePath $guiPython -ArgumentList $launchArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
