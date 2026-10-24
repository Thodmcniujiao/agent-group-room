# Setup-GPTSoVITS.ps1
# Setup guide for local RTX 5080 GPT-SoVITS offline engine

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  RTX 5080 Offline GPT-SoVITS Setup Guide" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$VoiceDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$ModelsDir = Join-Path $VoiceDir "models"
if (-not (Test-Path $ModelsDir)) {
    New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null
}

Write-Host "`n[1] Checking local service status on 127.0.0.1:9880..." -ForegroundColor Yellow
$connected = $false
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:9880" -TimeoutSec 2 -ErrorAction SilentlyContinue
    $connected = $true
} catch {
    $connected = $false
}

if ($connected) {
    Write-Host "[OK] Detected GPT-SoVITS service running on 127.0.0.1:9880!" -ForegroundColor Green
    Write-Host "     Voice Studio will automatically use your RTX 5080." -ForegroundColor Green
} else {
    Write-Host "[!] Local GPT-SoVITS service on 9880 is currently OFFLINE." -ForegroundColor Gray
    Write-Host "    (Voice Studio is using the high-fidelity fast engine by default)" -ForegroundColor Yellow

    Write-Host "`n[2] How to enable RTX 5080 deep voice cloning?" -ForegroundColor Cyan
    Write-Host "    1. Download GPT-SoVITS v2 package for Windows:"
    Write-Host "       https://github.com/RVC-Boss/GPT-SoVITS/releases" -ForegroundColor White
    Write-Host "    2. Extract it (e.g. to D:\AI\GPT-SoVITS)"
    Write-Host "    3. Run api_v2 service in package:"
    Write-Host "       runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880" -ForegroundColor White
    Write-Host "    4. Put trained character weights into:"
    Write-Host "       - $ModelsDir\chatgpt_sovits.pth & chatgpt_gpt.ckpt"
    Write-Host "       - $ModelsDir\claude_sovits.pth & claude_gpt.ckpt"
    Write-Host "       - $ModelsDir\gemini_sovits.pth & gemini_gpt.ckpt"
    Write-Host "       - $ModelsDir\deepseek_sovits.pth & deepseek_gpt.ckpt"
}

Write-Host "`n[3] Models directory:" -ForegroundColor Cyan
Write-Host "    $ModelsDir" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Cyan
