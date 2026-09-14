#requires -Version 7.0
[CmdletBinding()]
param([switch]$SkipPython)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

function Invoke-Checked {
  param([string]$Label, [string]$FilePath, [string[]]$Arguments)
  Write-Host "== $Label" -ForegroundColor Cyan
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE." }
}

Push-Location $Root
try {
  if (-not $SkipPython) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    Invoke-Checked 'Python tests' $python @('-m', 'pytest', 'tests', '-q')
    Invoke-Checked 'Ruff' $python @('-m', 'ruff', 'check', '.')
    Invoke-Checked 'Mypy' $python @('-m', 'mypy', 'src')
  }
  Invoke-Checked 'PowerShell operational gate' (Get-Process -Id $PID).Path @(
    '-NoProfile', '-File', (Join-Path $Root 'tests/Invoke-Checks.ps1'))
  Invoke-Checked 'Installer contracts' (Get-Process -Id $PID).Path @(
    '-NoProfile', '-File', (Join-Path $Root 'tests/Test-InstallerContracts.ps1'))
  Invoke-Checked 'Native application tests' 'dotnet.exe' @(
    'run', '--project', (Join-Path $Root 'tests/AFKLocalAI.App.Tests'), '-c', 'Release')
  & git diff --check
  if ($LASTEXITCODE -ne 0) { throw 'git diff --check failed.' }
} finally {
  Pop-Location
}
Write-Host 'DISTRIBUTION CONTRACTS PASSED' -ForegroundColor Green
