$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[!] Virtualenv Python not found at: $VenvPython" -ForegroundColor Red
    Write-Host "[!] Please run .\Install.ps1 in project root first." -ForegroundColor Yellow
    exit 1
}

$Port = 5005
$Url = "http://127.0.0.1:$Port"

Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Starting Voice Studio (AI Girls TTS Workbench)..." -ForegroundColor Cyan
Write-Host "  Characters: ChatGPT / Claude / Gemini / DeepSeek" -ForegroundColor Green
Write-Host "  Web Studio: $Url" -ForegroundColor White
Write-Host "=====================================================" -ForegroundColor Cyan

# Open browser in background
Start-Job -ScriptBlock {
    param($targetUrl)
    Start-Sleep -Seconds 2
    Start-Process $targetUrl
} -ArgumentList $Url | Out-Null

# Run server
$ServerScript = Join-Path $ScriptDir "server.py"
& $VenvPython $ServerScript --port $Port
