#requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'contract-common.ps1')
. (Join-Path $Root 'scripts/lifecycle-common.ps1')

$script:Pass = 0
$script:Fail = 0
$script:Failures = [System.Collections.Generic.List[string]]::new()
function Assert-True {
  param([string]$Case, $Condition, [string]$Detail = '')
  if ($Condition) { $script:Pass++; return }
  $script:Fail++; $script:Failures.Add($(if ($Detail) { "$Case :: $Detail" } else { $Case }))
}
function Assert-Throws {
  param([string]$Case, [scriptblock]$Action)
  try { & $Action; $script:Fail++; $script:Failures.Add("$Case :: expected failure") }
  catch { $script:Pass++ }
}

$dir = Join-Path ([IO.Path]::GetTempPath()) ('afkai-lifecycle-unit-' + [guid]::NewGuid().ToString('n'))
try {
  [void](New-Item -ItemType Directory -Path $dir -Force)
  $artifact = Join-Path $dir 'candidate.exe'
  [IO.File]::WriteAllText($artifact, 'exact candidate bytes')
  $hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash
  Assert-True 'matching digest is accepted' ((Assert-ExpectedDigest -Path $artifact -ExpectedSha256 $hash) -eq $hash)
  Assert-Throws 'digest mismatch fails before installation' { Assert-ExpectedDigest -Path $artifact -ExpectedSha256 ('0' * 64) }
  Assert-Throws 'malformed expected digest fails closed' { Assert-ExpectedDigest -Path $artifact -ExpectedSha256 'abc' }

  $good = [ordered]@{
    digest_verified = $true
    installer_exit_zero = $true
    executable_present = $true
    version_matches = $true
    self_test_passed = $true
    uninstall_entry_present = $true
    start_menu_present = $true
    uninstaller_exit_zero = $true
    program_files_removed = $true
    state_preserved = $true
  }
  Assert-True 'complete lifecycle evidence is accepted' (Assert-LifecycleEvidence -Evidence $good)
  foreach ($key in @($good.Keys)) {
    $broken = [ordered]@{}
    foreach ($pair in $good.GetEnumerator()) { $broken[$pair.Key] = $pair.Value }
    $broken[$key] = $false
    Assert-Throws "$key failure is rejected" { Assert-LifecycleEvidence -Evidence $broken }
  }

  Assert-Throws 'workspace root is not a disposable cleanup target' {
    Assert-SafeDisposablePath -Path $Root -AllowedParent $Root
  }
  $safeChild = Join-Path $Root 'build/lifecycle-unit-safe'
  Assert-True 'nested disposable path is allowed' ((Assert-SafeDisposablePath -Path $safeChild -AllowedParent (Join-Path $Root 'build')) -eq [IO.Path]::GetFullPath($safeChild))

  $alreadyAbsent = Join-Path $dir 'already-absent'
  Assert-True 'absent path completes the uninstall wait immediately' (Wait-PathAbsent -Path $alreadyAbsent -TimeoutSeconds 1 -PollMilliseconds 10)
  $stillPresent = Join-Path $dir 'still-present'
  [IO.File]::WriteAllText($stillPresent, 'fixture')
  Assert-Throws 'uninstall wait fails closed when a path remains' {
    Wait-PathAbsent -Path $stillPresent -TimeoutSeconds 0 -PollMilliseconds 10
  }
} finally {
  Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
}

$lifecyclePath = Join-Path $Root 'scripts/Test-InstallerLifecycle.ps1'
$upgradePath = Join-Path $Root 'scripts/Test-LifecycleUpgrade.ps1'
Assert-True 'exact-artifact lifecycle script exists' (Test-Path -LiteralPath $lifecyclePath -PathType Leaf)
Assert-True 'isolated upgrade lifecycle script exists' (Test-Path -LiteralPath $upgradePath -PathType Leaf)
if (Test-Path -LiteralPath $lifecyclePath) {
  $text = Get-ContractText -Path $lifecyclePath
  Assert-True 'digest is checked before installer starts' ($text.IndexOf('Assert-ExpectedDigest') -lt $text.IndexOf('Invoke-BoundedProcess') -and $text.IndexOf('Assert-ExpectedDigest') -ge 0)
  foreach ($needle in @('ExpectedSha256', '/VERYSILENT', '--self-test', 'UninstallString', 'Start Menu', 'Assert-LifecycleEvidence', 'ConvertTo-Json')) {
    Assert-True "lifecycle harness contains $needle" ($text -match [regex]::Escape($needle))
  }
}
if (Test-Path -LiteralPath $upgradePath) {
  $text = Get-ContractText -Path $upgradePath
  foreach ($needle in @('0.1.99-test', '0.2.0-rc1', 'sentinel', 'TestAppId', 'DisplayVersion', 'state_preserved')) {
    Assert-True "upgrade harness contains $needle" ($text -match [regex]::Escape($needle))
  }
  Assert-True 'upgrade harness waits for asynchronous uninstall cleanup' ($text -match 'Wait-PathAbsent')
}

Write-Host ''
if ($script:Fail) {
  Write-Host "FAILURES ($script:Fail):" -ForegroundColor Red
  foreach ($failure in $script:Failures) { Write-Host "  - $failure" -ForegroundColor Red }
  Write-Host "LIFECYCLE HARNESS TESTS FAILED: $script:Pass passed, $script:Fail failed." -ForegroundColor Red
  exit 1
}
Write-Host "LIFECYCLE HARNESS TESTS PASSED: $script:Pass passed, 0 failed." -ForegroundColor Green
exit 0
