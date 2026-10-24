$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VoiceDir = Split-Path -Parent $ScriptDir
$TargetArchive = Join-Path $VoiceDir "GPT-SoVITS-v2pro-nvidia50.7z"
$EngineDir = Join-Path $VoiceDir "GPT-SoVITS-Engine"

$DownloadUrl = "https://www.modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/resolve/master/GPT-SoVITS-v2pro-20250604-nvidia50.7z"
$7zPath = "C:\Program Files\VapourSynth\vsrepo\7z.exe"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  RTX 5080 GPT-SoVITS Training Engine Deployment" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Engine Path: $EngineDir" -ForegroundColor White
Write-Host "GPU Model: NVIDIA GeForce RTX 5080 (Blackwell sm_120)" -ForegroundColor Green

# 1. Check 7-Zip
if (-not (Test-Path $7zPath)) {
    Write-Host "[*] Checking PATH for 7z..." -ForegroundColor Yellow
    $7zCmd = Get-Command 7z -ErrorAction SilentlyContinue
    if ($7zCmd) {
        $7zPath = $7zCmd.Source
    } else {
        Write-Host "[ERROR] 7-Zip not found. Please install 7-Zip." -ForegroundColor Red
        exit 1
    }
}
Write-Host "[OK] 7-Zip Ready: $7zPath" -ForegroundColor Green

# 2. Download archive with resume support (-C -)
if (-not (Test-Path $EngineDir)) {
    Write-Host "`n[1/3] Downloading RTX 5080 Training Package from ModelScope mirror..." -ForegroundColor Yellow
    Write-Host "      Archive Size: ~8.8 GB (Includes PyTorch cu128, pretrained bases, UVR5, FlashAttention)" -ForegroundColor Gray
    Write-Host "      Resume supported: if interrupted, re-running will resume seamlessly." -ForegroundColor Gray

    $curlArgs = @(
        "-L",
        "-C", "-",
        "--retry", "5",
        "--retry-delay", "3",
        "-o", $TargetArchive,
        $DownloadUrl
    )

    $proc = Start-Process -FilePath "curl.exe" -ArgumentList $curlArgs -NoNewWindow -PassThru -Wait
    if ($proc.ExitCode -ne 0) {
        Write-Host "[ERROR] Download interrupted (ExitCode: $($proc.ExitCode)). Please retry." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Archive Download Completed!" -ForegroundColor Green

    # 3. Extract
    Write-Host "`n[2/3] Extracting archive to $EngineDir ..." -ForegroundColor Yellow
    Write-Host "      This may take 1-3 minutes, please wait..." -ForegroundColor Gray

    $extractArgs = @(
        "x",
        "-y",
        "-o$EngineDir",
        $TargetArchive
    )
    $proc7z = Start-Process -FilePath $7zPath -ArgumentList $extractArgs -NoNewWindow -PassThru -Wait
    if ($proc7z.ExitCode -ne 0) {
        Write-Host "[ERROR] 7z extraction failed (ExitCode: $($proc7z.ExitCode))" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Extraction Completed!" -ForegroundColor Green

    Write-Host "Cleaning up temporary 7z file to free disk space..." -ForegroundColor Gray
    Remove-Item $TargetArchive -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "[OK] GPT-SoVITS-Engine directory already exists, skipping download." -ForegroundColor Green
}

# 4. Create Desktop shortcut
Write-Host "`n[3/3] Setting up Desktop shortcut for Training WebUI..." -ForegroundColor Yellow

$goWebuiBat = Join-Path $EngineDir "go-webui.bat"
if (-not (Test-Path $goWebuiBat)) {
    $found = Get-ChildItem -Path $EngineDir -Filter "go-webui.bat" -Recurse | Select-Object -First 1
    if ($found) {
        $goWebuiBat = $found.FullName
        $EngineDir = $found.DirectoryName
    }
}

if (Test-Path $goWebuiBat) {
    $WshShell = New-Object -ComObject WScript.Shell
    $desktop = [Environment]::GetFolderPath('DesktopDirectory')
    $shortcut = $WshShell.CreateShortcut((Join-Path $desktop "GPT-SoVITS-Training.lnk"))
    $shortcut.TargetPath = $goWebuiBat
    $shortcut.WorkingDirectory = $EngineDir
    $iconPath = Join-Path (Split-Path -Parent $VoiceDir) 'gui\icon.ico'
    if (Test-Path $iconPath) {
        $shortcut.IconLocation = "$iconPath, 0"
    }
    $shortcut.Description = "GPT-SoVITS Training and Fine-Tuning Console (RTX 5080)"
    $shortcut.Save()
    Write-Host "[OK] Desktop shortcut created: 'GPT-SoVITS-Training'" -ForegroundColor Green
}

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "  RTX 5080 Training Engine Deployment Finished!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
