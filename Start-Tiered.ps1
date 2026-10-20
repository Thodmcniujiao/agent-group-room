param(
    [Parameter(Mandatory=$true)][string]$Project,
    [ValidateSet('Claude','Codex')][string]$Console = 'Claude'
)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath $Project).Path
if (-not (Test-Path -LiteralPath $projectPath -PathType Container)) { throw 'Project must be a directory.' }
$preferencesPath = Join-Path $env:USERPROFILE '.local\share\tiered-dev\preferences.json'
$modelPreferences = if (Test-Path -LiteralPath $preferencesPath) { Get-Content -LiteralPath $preferencesPath -Raw | ConvertFrom-Json } else { '{"models":{"claude":"claude-opus-5-5","chatgpt":"gpt-6-astra"},"effort":{"claude":"high"}}' | ConvertFrom-Json }
$tieredPath = Join-Path $projectPath '.tiered'
New-Item -ItemType Directory -Force -Path (Join-Path $tieredPath 'tasks') | Out-Null
Push-Location -LiteralPath $projectPath
$previousPath = $env:PATH
$env:PATH = (Join-Path $PSScriptRoot '.venv\Scripts') + ';' + $env:PATH
try {
    if ($Console -eq 'Claude') {
        & claude --model $modelPreferences.models.claude --effort $modelPreferences.effort.claude
    } else {
        & codex --model $modelPreferences.models.chatgpt
    }
} finally {
    $env:PATH = $previousPath
    Pop-Location
}
