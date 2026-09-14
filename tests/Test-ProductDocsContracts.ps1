#requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'contract-common.ps1')
$script:Pass = 0
$script:Fail = 0
$script:Failures = [System.Collections.Generic.List[string]]::new()
function Assert-True {
  param([string]$Case, $Condition)
  if ([bool]$Condition) { $script:Pass++; return }
  $script:Fail++; $script:Failures.Add($Case)
}

$readme = Get-ContractText -Path (Join-Path $Root 'README.md')
$support = Get-ContractText -Path (Join-Path $Root 'SUPPORT.md')
$security = Get-ContractText -Path (Join-Path $Root 'SECURITY.md')
$installer = Get-ContractText -Path (Join-Path $Root 'installer/README.md')
$docs = Get-ContractText -Path (Join-Path $Root 'docs/README.md')
$releasePath = Join-Path $Root 'docs/releases/0.2.0-rc1.md'
$lifecyclePath = Join-Path $Root 'docs/install-upgrade-uninstall.md'

Assert-True 'README uses canonical product name' ($readme -match '^# AFK LocalAI for Windows')
Assert-True 'README names one-click installer' ($readme -match 'AFKLocalAISetup-0\.2\.0-rc1-x64\.exe')
Assert-True 'README does not direct normal users to CMD bootstrap' ($readme -notmatch 'double-click.*\.cmd')
Assert-True 'README explains guided prerequisite recovery' ($readme -match '(?is)virtualization.*WSL.*Docker.*resume')
Assert-True 'README links current candidate notes' ($readme -match 'docs/releases/0\.2\.0-rc1\.md')
Assert-True 'support documents in-app diagnostics' ($support -match '(?is)Start Menu.*Diagnostics')
Assert-True 'support names current candidate' ($support -match '0\.2\.0-rc1')
Assert-True 'security names canonical product' ($security -match '^# AFK LocalAI security policy')
Assert-True 'installer guide documents native shell' ($installer -match '(?is)AFKLocalAI\.exe.*Inno Setup')
Assert-True 'installer guide documents deterministic payload' ($installer -match 'payload-manifest\.json')
Assert-True 'docs index uses canonical product name' ($docs -match '^# AFK LocalAI documentation')
Assert-True 'current release notes exist' (Test-Path -LiteralPath $releasePath -PathType Leaf)
Assert-True 'lifecycle guide exists' (Test-Path -LiteralPath $lifecyclePath -PathType Leaf)
if (Test-Path -LiteralPath $releasePath) {
  $release = Get-ContractText -Path $releasePath
  Assert-True 'release is explicitly prerelease' ($release -match '(?i)prerelease')
  Assert-True 'release blocks stable publication before exact lifecycle' ($release -match '(?is)exact installer bytes.*lifecycle')
}
if (Test-Path -LiteralPath $lifecyclePath) {
  $lifecycle = Get-ContractText -Path $lifecyclePath
  Assert-True 'lifecycle guide names program path' ($lifecycle -match '%LOCALAPPDATA%\\Programs\\AFK LocalAI')
  Assert-True 'lifecycle guide names preserved state path' ($lifecycle -match '%LOCALAPPDATA%\\AFK LocalAI')
}

Write-Host ''
if ($script:Fail) {
  Write-Host "FAILURES ($script:Fail):" -ForegroundColor Red
  foreach ($failure in $script:Failures) { Write-Host "  - $failure" -ForegroundColor Red }
  Write-Host "PRODUCT DOCS CONTRACTS FAILED: $script:Pass passed, $script:Fail failed." -ForegroundColor Red
  exit 1
}
Write-Host "PRODUCT DOCS CONTRACTS PASSED: $script:Pass passed, 0 failed." -ForegroundColor Green
exit 0
