$ErrorActionPreference = 'Stop'
$secureKey = Read-Host 'Paste your DeepSeek API key (input is hidden)' -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $keyValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer).Trim()
    if (-not $keyValue.StartsWith('sk-')) { throw 'Expected a DeepSeek API key starting with sk-.' }
    [Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', $keyValue, 'User')
    $env:DEEPSEEK_API_KEY = $keyValue
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    $keyValue = $null
    $secureKey.Dispose()
}
Write-Host 'Saved to the Windows user environment. No key was written to this folder.'
& (Join-Path $PSScriptRoot 'Doctor.ps1') -DeepSeekLive
