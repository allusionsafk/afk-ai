#requires -Version 7.0
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$InstallerPath,
  [Parameter(Mandatory)][string]$ExpectedSha256,
  [Parameter(Mandatory)][string]$ReportPath,
  [string]$DisposableRoot = ''
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'lifecycle-common.ps1')

$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$hash = Assert-ExpectedDigest -Path $installer -ExpectedSha256 $ExpectedSha256
$version = Get-Content -LiteralPath (Join-Path $Root 'installer/version.json') -Raw | ConvertFrom-Json
$buildRoot = Join-Path $Root 'build/lifecycle'
if (-not $DisposableRoot) { $DisposableRoot = Join-Path $buildRoot ([guid]::NewGuid().ToString('n')) }
$disposable = Assert-SafeDisposablePath -Path $DisposableRoot -AllowedParent $buildRoot
$report = [IO.Path]::GetFullPath($ReportPath)
[void](New-Item -ItemType Directory -Path $disposable -Force)
[void](New-Item -ItemType Directory -Path (Split-Path -Parent $report) -Force)

$installRoot = Join-Path $env:LOCALAPPDATA 'Programs/AFK LocalAI'
$stateRoot = Join-Path $disposable 'state'
$startMenuRoot = Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs/AFK LocalAI'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8A8A2D4D-CE75-4A2D-A39B-56B4206F93D0}_is1'
$installLog = Join-Path $disposable 'install.log'
$uninstallLog = Join-Path $disposable 'uninstall.log'
$stdoutPath = Join-Path $disposable 'self-test.json'
$stderrPath = Join-Path $disposable 'self-test.err'

if ((Test-Path -LiteralPath $installRoot) -or (Test-Path -LiteralPath $uninstallKey)) {
  throw 'A production AFK LocalAI installation already exists. Lifecycle qualification refuses to overwrite user state.'
}

$evidence = [ordered]@{
  digest_verified = $true
  installer_exit_zero = $false
  executable_present = $false
  version_matches = $false
  self_test_passed = $false
  uninstall_entry_present = $false
  start_menu_present = $false
  uninstaller_exit_zero = $false
  program_files_removed = $false
  state_preserved = $false
}
$failure = $null
$uninstaller = $null
try {
  $installArguments = @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCANCEL', '/TASKS=',
    ('/LOG=' + $installLog)
  )
  $installResult = Invoke-BoundedProcess -FilePath $installer -Arguments $installArguments -TimeoutSeconds 900
  $evidence.installer_exit_zero = $installResult.ExitCode -eq 0
  if (-not $evidence.installer_exit_zero) { throw "Installer exited $($installResult.ExitCode)." }

  $installedExe = Join-Path $installRoot 'AFKLocalAI.exe'
  $evidence.executable_present = Test-Path -LiteralPath $installedExe -PathType Leaf
  if (-not $evidence.executable_present) { throw 'Installed AFKLocalAI.exe is missing.' }
  $installedVersion = ([Diagnostics.FileVersionInfo]::GetVersionInfo($installedExe)).FileVersion
  $evidence.version_matches = "$installedVersion" -eq "$($version.file_version)"

  $registration = Get-ItemProperty -LiteralPath $uninstallKey -ErrorAction SilentlyContinue
  $evidence.uninstall_entry_present = $null -ne $registration -and
    "$($registration.DisplayVersion)" -eq "$($version.display_version)" -and
    -not [string]::IsNullOrWhiteSpace($registration.UninstallString)
  if ($registration) { $uninstaller = Join-Path $installRoot 'unins000.exe' }

  $evidence.start_menu_present = Test-Path -LiteralPath (Join-Path $startMenuRoot 'AFK LocalAI.lnk') -PathType Leaf

  [void](New-Item -ItemType Directory -Path $stateRoot -Force)
  $selfTest = Invoke-BoundedProcess -FilePath $installedExe -Arguments @('--self-test', '--data-root', $stateRoot) -TimeoutSeconds 120 -CaptureOutput
  [IO.File]::WriteAllText($stdoutPath, $selfTest.StandardOutput)
  [IO.File]::WriteAllText($stderrPath, $selfTest.StandardError)
  $selfTestJson = $null
  try { $selfTestJson = $selfTest.StandardOutput | ConvertFrom-Json -ErrorAction Stop } catch {}
  $evidence.self_test_passed = $selfTest.ExitCode -eq 0 -and [bool]$selfTestJson.success
  $stateSentinel = Join-Path $stateRoot 'State/provisioning-state.json'
  if (-not (Test-Path -LiteralPath $stateSentinel -PathType Leaf)) { throw 'Installed self-test did not write its disposable state checkpoint.' }

  if (-not $uninstaller -or -not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) { throw 'Registered uninstaller is missing.' }
  $uninstallResult = Invoke-BoundedProcess -FilePath $uninstaller -Arguments @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/LOG=' + $uninstallLog)
  ) -TimeoutSeconds 600
  $evidence.uninstaller_exit_zero = $uninstallResult.ExitCode -eq 0
  $evidence.program_files_removed = Wait-PathAbsent -Path $installRoot -TimeoutSeconds 30
  $evidence.state_preserved = Test-Path -LiteralPath $stateSentinel -PathType Leaf
} catch {
  $failure = $_.Exception.Message
} finally {
  if ((Test-Path -LiteralPath $installRoot) -and $uninstaller -and (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
    try {
      [void](Invoke-BoundedProcess -FilePath $uninstaller -Arguments @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -TimeoutSeconds 600)
    } catch {}
  }
}

$certified = $false
try { $certified = Assert-LifecycleEvidence -Evidence $evidence } catch {
  if (-not $failure) { $failure = $_.Exception.Message }
}
$result = [ordered]@{
  schema_version = 1
  product_name = $version.product_name
  display_version = $version.display_version
  artifact_name = [IO.Path]::GetFileName($installer)
  certified_sha256 = $hash
  certified = [bool]$certified
  evidence = $evidence
  failure = $failure
  logs = @('install.log', 'uninstall.log', 'self-test.json', 'self-test.err')
}
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $report -Encoding utf8
if (-not $certified) { throw "Installer lifecycle qualification failed: $failure" }
$result
