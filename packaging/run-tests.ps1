# Runs each gui/test_*.py module on the bundled runtime with its own time limit.
# Results are reported as GitHub annotations so they are readable without the raw log.
# Always exits 0: the release is not blocked by tests, but the outcome is written to $env:GITHUB_OUTPUT.
param([string]$Root = 'dist\AgentGroupRoom', [int]$ModuleTimeoutSeconds = 300)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path (Resolve-Path $Root).Path 'runtime\python.exe'
$gui = Join-Path $repo 'gui'
$logs = Join-Path $repo 'dist\test-logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONIOENCODING = 'utf-8'

$summary = @(); $failed = @(); $totalRan = 0
foreach ($test in Get-ChildItem $gui -Filter 'test_*.py' | Sort-Object Name) {
    $module = $test.BaseName
    $out = Join-Path $logs "$module.out.txt"; $err = Join-Path $logs "$module.err.txt"
    $started = Get-Date
    $process = Start-Process -FilePath $python -ArgumentList @('-m', 'unittest', $module) -WorkingDirectory $gui `
        -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    $finished = $process.WaitForExit($ModuleTimeoutSeconds * 1000)
    $seconds = [int]((Get-Date) - $started).TotalSeconds
    $text = (Get-Content $err -Raw -ErrorAction SilentlyContinue) + (Get-Content $out -Raw -ErrorAction SilentlyContinue)
    if (-not $finished) {
        & taskkill /PID $process.Id /T /F | Out-Null
        # Name the test that was running when the limit hit, if verbose output shows it.
        $status = "TIMEOUT after ${seconds}s"
        $failed += $module
        $tail = ($text -split "`n" | Select-Object -Last 15) -join "`n"
        Write-Host "::error title=$module timed out::$(($tail -replace '%','%25' -replace "`r",'' -replace "`n",'%0A'))"
    } else {
        $ran = if ($text -match 'Ran (\d+) tests?') { [int]$Matches[1] } else { 0 }
        $totalRan += $ran
        $result = if ($text -match '(?m)^(OK.*|FAILED .*)$') { $Matches[1].Trim() } else { "exit $($process.ExitCode)" }
        $status = "$ran tests, $result (${seconds}s)"
        if ($process.ExitCode -ne 0) {
            $failed += $module
            $problems = [regex]::Matches($text, '(?m)^(FAIL|ERROR): .*$') | ForEach-Object { $_.Value } | Select-Object -First 8
            $tail = ($text -split "`n" | Where-Object { $_ -match 'Error|assert|Exception|File "' } | Select-Object -Last 12) -join "`n"
            $detail = (($problems -join "`n") + "`n---`n" + $tail)
            Write-Host "::error title=$module failed::$(($detail -replace '%','%25' -replace "`r",'' -replace "`n",'%0A'))"
        }
    }
    $summary += "$module : $status"
    Write-Host "$module : $status"
}
$headline = if ($failed.Count) { "Unit tests: $($failed.Count) module(s) failed or timed out, $totalRan tests ran" } else { "Unit tests: all modules passed, $totalRan tests ran" }
Write-Host "::notice title=$headline::$((($summary -join "`n") -replace '%','%25' -replace "`n",'%0A'))"
if ($env:GITHUB_OUTPUT) {
    "passed=$(if ($failed.Count) { 'false' } else { 'true' })" | Out-File -Append -Encoding utf8 $env:GITHUB_OUTPUT
    "summary=$headline" | Out-File -Append -Encoding utf8 $env:GITHUB_OUTPUT
}
exit 0
