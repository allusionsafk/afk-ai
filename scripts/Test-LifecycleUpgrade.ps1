#requires -Version 7.0
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$PayloadRoot,
  [Parameter(Mandatory)][string]$InnoCompiler,
  [Parameter(Mandatory)][string]$ReportPath,
  [string]$DisposableRoot = ''
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'lifecycle-common.ps1')
$payload = (Resolve-Path -LiteralPath $PayloadRoot).Path
$compiler = (Resolve-Path -LiteralPath $InnoCompiler).Path
$buildRoot = Join-Path $Root 'build/lifecycle-upgrade'
if (-not $DisposableRoot) { $DisposableRoot = Join-Path $buildRoot ([guid]::NewGuid().ToString('n')) }
$disposable = Assert-SafeDisposablePath -Path $DisposableRoot -AllowedParent $buildRoot
[void](New-Item -ItemType Directory -Path $disposable -Force)
[void](New-Item -ItemType Directory -Path (Split-Path -Parent ([IO.Path]::GetFullPath($ReportPath))) -Force)

$TestAppId = '{{E6E1AC1C-09DA-4C65-A4C8-EEC84B57A7E1}'
$registryAppId = '{E6E1AC1C-09DA-4C65-A4C8-EEC84B57A7E1}_is1'
$groupName = 'AFK LocalAI Lifecycle Test'
$installRoot = Join-Path $disposable 'program'
$stateRoot = Join-Path $disposable 'state'
$sentinel = Join-Path $stateRoot 'settings-sentinel.txt'
$outputRoot = Join-Path $disposable 'installers'
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$registryAppId"
$startMenu = Join-Path $env:APPDATA "Microsoft/Windows/Start Menu/Programs/$groupName"
[void](New-Item -ItemType Directory -Path $outputRoot -Force)

if ((Test-Path -LiteralPath $uninstallKey) -or (Test-Path -LiteralPath $startMenu)) {
  throw 'Isolated lifecycle-test registration already exists; refusing to overwrite it.'
}

function New-TestInstaller {
  param([string]$DisplayVersion, [string]$FileVersion, [string]$OutputDirectory)
  [void](New-Item -ItemType Directory -Path $OutputDirectory -Force)
  $arguments = @(
    ('/DSourceRoot=' + $payload),
    ('/DOutputDir=' + $OutputDirectory),
    ('/DAppVersion=' + $DisplayVersion),
    ('/DFileVersion=' + $FileVersion),
    '/DChannel=lifecycle-test',
    '/DSourceCommit=lifecycle-test',
    ('/DTestAppId=' + $TestAppId),
    ('/DTestDefaultDir=' + $installRoot),
    ('/DTestGroupName=' + $groupName),
    (Join-Path $Root 'installer/AFKLocalAI.iss')
  )
  $compile = Invoke-BoundedProcess -FilePath $compiler -Arguments $arguments -TimeoutSeconds 900 -CaptureOutput
  if ($compile.ExitCode -ne 0) { throw "Lifecycle installer compile failed: $($compile.StandardError)" }
  $path = Join-Path $OutputDirectory "AFKLocalAISetup-$DisplayVersion-x64.exe"
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Lifecycle installer missing: '$path'." }
  return $path
}

$predecessorVersion = '0.1.99-test'
$candidateVersion = '0.2.0-rc1'
$predecessor = New-TestInstaller -DisplayVersion $predecessorVersion -FileVersion '0.1.99.0' -OutputDirectory (Join-Path $outputRoot 'predecessor')
$candidate = New-TestInstaller -DisplayVersion $candidateVersion -FileVersion '0.2.0.0' -OutputDirectory (Join-Path $outputRoot 'candidate')
$candidateHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToUpperInvariant()

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
$uninstaller = Join-Path $installRoot 'unins000.exe'
try {
  $predecessorInstall = Invoke-BoundedProcess -FilePath $predecessor -Arguments @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCANCEL', '/TASKS='
  ) -TimeoutSeconds 900
  if ($predecessorInstall.ExitCode -ne 0) { throw "Predecessor installer exited $($predecessorInstall.ExitCode)." }
  $before = Get-ItemProperty -LiteralPath $uninstallKey -ErrorAction Stop
  if ("$($before.DisplayVersion)" -ne $predecessorVersion) { throw 'Predecessor registration has the wrong version.' }

  [void](New-Item -ItemType Directory -Path $stateRoot -Force)
  [IO.File]::WriteAllText($sentinel, 'preserve-me-across-upgrade')

  $candidateInstall = Invoke-BoundedProcess -FilePath $candidate -Arguments @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCANCEL', '/TASKS='
  ) -TimeoutSeconds 900
  $evidence.installer_exit_zero = $candidateInstall.ExitCode -eq 0
  $registration = Get-ItemProperty -LiteralPath $uninstallKey -ErrorAction Stop
  $evidence.version_matches = "$($registration.DisplayVersion)" -eq $candidateVersion
  $evidence.uninstall_entry_present = -not [string]::IsNullOrWhiteSpace($registration.UninstallString)
  $installedExe = Join-Path $installRoot 'AFKLocalAI.exe'
  $evidence.executable_present = Test-Path -LiteralPath $installedExe -PathType Leaf
  $evidence.start_menu_present = Test-Path -LiteralPath (Join-Path $startMenu 'AFK LocalAI.lnk') -PathType Leaf
  $selfTest = Invoke-BoundedProcess -FilePath $installedExe -Arguments @('--self-test', '--data-root', $stateRoot) -TimeoutSeconds 120 -CaptureOutput
  $summary = $selfTest.StandardOutput | ConvertFrom-Json -ErrorAction Stop
  $evidence.self_test_passed = $selfTest.ExitCode -eq 0 -and [bool]$summary.success
  $evidence.state_preserved = (Test-Path -LiteralPath $sentinel -PathType Leaf) -and
    ([IO.File]::ReadAllText($sentinel) -eq 'preserve-me-across-upgrade')

  $uninstall = Invoke-BoundedProcess -FilePath $uninstaller -Arguments @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'
  ) -TimeoutSeconds 600
  $evidence.uninstaller_exit_zero = $uninstall.ExitCode -eq 0
  $evidence.program_files_removed = Wait-PathAbsent -Path $installRoot -TimeoutSeconds 30
  $evidence.state_preserved = $evidence.state_preserved -and (Test-Path -LiteralPath $sentinel -PathType Leaf)
} catch {
  $failure = $_.Exception.Message
} finally {
  if ((Test-Path -LiteralPath $uninstaller -PathType Leaf) -and (Test-Path -LiteralPath $installRoot)) {
    try { [void](Invoke-BoundedProcess -FilePath $uninstaller -Arguments @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -TimeoutSeconds 600) } catch {}
  }
}

$certified = $false
try { $certified = Assert-LifecycleEvidence -Evidence $evidence } catch {
  if (-not $failure) { $failure = $_.Exception.Message }
}
$result = [ordered]@{
  schema_version = 1
  test_kind = 'isolated-upgrade'
  predecessor_version = $predecessorVersion
  candidate_version = $candidateVersion
  candidate_sha256 = $candidateHash
  certified = [bool]$certified
  evidence = $evidence
  failure = $failure
}
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding utf8
if (-not $certified) { throw "Upgrade lifecycle qualification failed: $failure" }
$result
