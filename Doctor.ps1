param([switch]$DeepSeekLive, [switch]$ReviewLive)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$doctorArgs = @((Join-Path $PSScriptRoot 'scripts\doctor.py'))
if ($DeepSeekLive) { $doctorArgs += '--deepseek-live' }
if ($ReviewLive) { $doctorArgs += '--review-live' }
& $python @doctorArgs
if ($LASTEXITCODE -ne 0) { throw "Doctor failed with exit code $LASTEXITCODE" }
