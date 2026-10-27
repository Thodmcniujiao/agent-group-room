# Builds a portable Windows folder and zip: AgentGroupRoom.exe + bundled Python 3.12 + app files.
# Usage: .\packaging\build-portable.ps1 [-Python C:\path\to\python.exe] [-Output dist]
param(
    [string]$Python = '',
    [string]$Output = 'dist'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
if (-not $Python) { $Python = (& py -3.12 -c 'import sys; print(sys.executable)').Trim() }
$version = (& $Python -c "import sys; sys.path.insert(0, 'gui'); from version import VERSION; print(VERSION)").Trim()
$pyVersion = (& $Python -c 'import sys; print("%d.%d" % sys.version_info[:2])').Trim()
if ($pyVersion -ne '3.12') { throw "Python 3.12 is required, found $pyVersion" }
$pyHome = (& $Python -c 'import sys; print(sys.base_prefix)').Trim()

$outRoot = Join-Path $repo $Output
$dest = Join-Path $outRoot 'AgentGroupRoom'
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force -Path $dest | Out-Null

Write-Host "== Runtime: copying Python $pyVersion from $pyHome"
$runtime = Join-Path $dest 'runtime'
robocopy $pyHome $runtime /E /NFL /NDL /NJH /NJS /NP /XD 'Scripts' 'Doc' 'include' 'libs' 'tcl' '__pycache__' 'test' 'idlelib' 'tkinter' 'turtledemo' 'site-packages' | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with $LASTEXITCODE" }
$sitePackages = Join-Path $runtime 'Lib\site-packages'
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
& $Python -m pip install --disable-pip-version-check --no-warn-script-location --target $sitePackages -r (Join-Path $repo 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Get-ChildItem $sitePackages -Directory -Filter 'bin' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
& (Join-Path $runtime 'python.exe') -c "import starlette, uvicorn, webview, mcp, openai, win32api; print('runtime imports ok')"
if ($LASTEXITCODE -ne 0) { throw 'Bundled runtime cannot import its dependencies.' }

Write-Host '== App: copying tracked files'
$app = Join-Path $dest 'app'
$include = @('gui', 'plugins', 'scripts', 'browser', 'voice', 'docs', 'third_party', '.claude-plugin',
    'README.md', 'CHANGELOG.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE_STATUS.md', 'requirements.txt', 'requirements.lock.txt', 'sources.lock.json')
$files = & git -c core.quotepath=off ls-files -- $include
foreach ($file in $files) {
    if ($file -match '^gui/test_.*\.py$') { continue }
    $target = Join-Path $app ($file -replace '/', '\')
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo $file) -Destination $target
}

Write-Host '== Launcher'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $csc /nologo /codepage:65001 /optimize+ /target:winexe "/win32icon:$(Join-Path $repo 'gui\icon.ico')" "/out:$(Join-Path $dest 'AgentGroupRoom.exe')" /r:System.Windows.Forms.dll (Join-Path $PSScriptRoot 'Launcher.cs')
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed.' }
Copy-Item (Join-Path $PSScriptRoot 'PORTABLE.md') (Join-Path $dest 'README-PORTABLE.md')
New-Item -ItemType Directory -Force -Path (Join-Path $dest 'data') | Out-Null

Write-Host '== Archive'
$zip = Join-Path $outRoot "AgentGroupRoom-$version-win64-portable.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
& 7z a -tzip -mx=7 $zip $dest | Out-Null
if ($LASTEXITCODE -ne 0) { Compress-Archive -Path $dest -DestinationPath $zip }
$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "Built $zip ($size MB)"
