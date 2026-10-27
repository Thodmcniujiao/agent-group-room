# Starts the bundled backend from the portable folder with throwaway data and checks /api/health.
param([string]$Root = 'dist\AgentGroupRoom', [int]$Port = 49399)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path $Root).Path
$data = Join-Path ([IO.Path]::GetTempPath()) ('agr-smoke-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $data | Out-Null
$env:TIERED_DATA_HOME = $data
$env:TIERED_GUI_PORT = "$Port"
$env:PYTHONNOUSERSITE = '1'
$python = Join-Path $Root 'runtime\python.exe'
$server = Start-Process -FilePath $python -ArgumentList ('"' + (Join-Path $Root 'app\gui\server.py') + '"') -WorkingDirectory (Join-Path $Root 'app') -PassThru -NoNewWindow -RedirectStandardError (Join-Path $data 'stderr.txt')
try {
    $ok = $false
    for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
        Start-Sleep -Milliseconds 500
        try { $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health"; $ok = $health.service -eq 'tiered-dev-gui' } catch {}
    }
    if (-not $ok) { Get-Content (Join-Path $data 'stderr.txt') -ErrorAction SilentlyContinue; throw 'Portable backend did not become healthy.' }
    $boot = Invoke-RestMethod "http://127.0.0.1:$Port/api/bootstrap" -TimeoutSec 90
    Write-Host "Portable backend healthy: version $($health.version), $($boot.rooms.Count) rooms, $($boot.groups.Count) groups"
    if (-not (Test-Path (Join-Path $data 'gui\rooms.sqlite3'))) { throw 'Data was not written inside TIERED_DATA_HOME.' }
    Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing | Out-Null
} finally {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
}
