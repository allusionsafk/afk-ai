#requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'contract-common.ps1')
. (Join-Path $Root 'installer/installer-common.ps1')
. (Join-Path $Root 'installer/recovery.ps1')

$script:Pass = 0
$script:Fail = 0
$script:Failures = [System.Collections.Generic.List[string]]::new()

function Assert-Equal {
  param([string]$Case, $Expected, $Actual)
  if ("$Expected" -ceq "$Actual") { $script:Pass++; return }
  $script:Fail++
  $script:Failures.Add("$Case`n        expected: '$Expected'`n        actual:   '$Actual'")
}

function Assert-True {
  param([string]$Case, $Condition, [string]$Detail = '')
  Assert-Equal -Case $Case -Expected True -Actual ([bool]$Condition)
  if (-not $Condition -and $Detail) { $script:Failures[-1] += "`n        detail:   $Detail" }
}

Write-Host '-- recovery mapping' -ForegroundColor Cyan
$expected = [ordered]@{
  'PREFLIGHT-READY'                       = @('retry',                    $false, $true)
  'PREFLIGHT-UNSUPPORTED-PLATFORM'        = @('support-guidance',         $false, $false)
  'PREFLIGHT-UNKNOWN'                     = @('support-guidance',         $false, $false)
  'PREFLIGHT-FIRMWARE-VIRT-DISABLED'      = @('firmware-guidance',        $false, $false)
  'PREFLIGHT-WINDOWS-FEATURE-MISSING'     = @('install-wsl',              $true,  $true)
  'PREFLIGHT-WINDOWS-REBOOT-REQUIRED'     = @('restart-windows',          $true,  $false)
  'PREFLIGHT-HYPERVISOR-NOT-RUNNING'      = @('restart-windows',          $true,  $false)
  'PREFLIGHT-WSL-NOT-INSTALLED'           = @('install-wsl',              $true,  $true)
  'PREFLIGHT-WSL-UPDATE-REQUIRED'         = @('update-wsl',               $true,  $true)
  'PREFLIGHT-WSL-UNHEALTHY'               = @('update-wsl',               $true,  $true)
  'PREFLIGHT-DOCKER-REMOTE-CONTEXT'       = @('docker-context-guidance',  $false, $false)
  'PREFLIGHT-DOCKER-LINUX-ENGINE-REQUIRED'= @('docker-linux-guidance',    $false, $false)
  'PREFLIGHT-DOCKER-VERSION-UNSUPPORTED'  = @('update-docker',            $false, $true)
  'PREFLIGHT-DOCKER-NOT-INSTALLED'        = @('install-docker',           $false, $true)
  'PREFLIGHT-DOCKER-STARTING'             = @('retry',                    $false, $true)
  'PREFLIGHT-DOCKER-NOT-RUNNING'          = @('start-docker',             $false, $true)
}

foreach ($code in $expected.Keys) {
  $actual = Get-PreflightRecovery -Code $code
  Assert-Equal "$code action" $expected[$code][0] $actual.ActionId
  Assert-Equal "$code elevation" $expected[$code][1] $actual.RequiresElevation
  Assert-Equal "$code automatic flag" $expected[$code][2] $actual.Automatic
  Assert-True "$code has a user label" (-not [string]::IsNullOrWhiteSpace($actual.Label))
  Assert-True "$code explains the action" (-not [string]::IsNullOrWhiteSpace($actual.Explanation))
}

$unknownThrew = $false
try { [void](Get-PreflightRecovery -Code 'PREFLIGHT-NOT-A-REAL-CODE') } catch { $unknownThrew = $true }
Assert-True 'unknown reason codes fail closed' $unknownThrew

Write-Host '-- exact command allowlist' -ForegroundColor Cyan
$programFiles = 'C:\Program Files'
$commands = [ordered]@{
  'install-wsl'     = @('wsl.exe', '--install --no-distribution')
  'update-wsl'      = @('wsl.exe', '--update')
  'install-docker'  = @('winget.exe', 'install --id Docker.DockerDesktop --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity')
  'update-docker'   = @('winget.exe', 'upgrade --id Docker.DockerDesktop --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity')
  'start-docker'    = @((Join-Path $programFiles 'Docker\Docker\Docker Desktop.exe'), '')
  'restart-windows' = @('shutdown.exe', '/r /t 0 /d p:2:4 /c AFK LocalAI prerequisite setup')
}
foreach ($action in $commands.Keys) {
  $command = Get-RecoveryCommand -ActionId $action -ProgramFilesRoot $programFiles
  Assert-Equal "$action executable" $commands[$action][0] $command.FilePath
  Assert-Equal "$action arguments" $commands[$action][1] ($command.Arguments -join ' ')
}
Assert-Equal 'retry never starts a process' $null (Get-RecoveryCommand -ActionId retry -ProgramFilesRoot $programFiles)

$invalidActionThrew = $false
try { [void](Get-RecoveryCommand -ActionId 'docker-context-use' -ProgramFilesRoot $programFiles) } catch { $invalidActionThrew = $true }
Assert-True 'non-allowlisted recovery actions fail closed' $invalidActionThrew

$modulePath = Join-Path $Root 'installer/recovery.ps1'
$module = Get-ContractText -Path $modulePath
foreach ($forbidden in @('bcdedit', 'docker\s+context\s+use', 'SwitchDaemon', 'Enable-WindowsOptionalFeature',
    'Disable-WindowsOptionalFeature', 'Set-VMProcessor', 'Set-MpPreference')) {
  Assert-True "recovery never contains forbidden mutation $forbidden" ($module -notmatch $forbidden)
}

Write-Host '-- recovery state and events' -ForegroundColor Cyan
$dir = Join-Path ([System.IO.Path]::GetTempPath()) ('afkai-recovery-test-' + [guid]::NewGuid().ToString('n'))
try {
  [void](New-Item -ItemType Directory -Path $dir -Force)
  $path = Get-InstallerStatePath -Root $dir
  $state = New-InstallerState
  $state.preflight = [pscustomobject]@{ overall = 'READY'; code = 'PREFLIGHT-READY' }
  Save-InstallerState -State $state -Path $path

  $events = @(Invoke-RecoveryAction -ActionId retry -DataRoot $dir -EventStream)
  $after = Import-InstallerState -Path $path
  Assert-Equal 'recovery invalidates the prior live-preflight checkpoint' $null $after.preflight
  Assert-Equal 'recovery records the action id' retry $after.recovery.action_id
  Assert-Equal 'recovery records a completed attempt' success $after.recovery.status
  Assert-Equal 'recovery increments an attempt counter' 1 $after.recovery.attempt

  $eventLines = @($events | Where-Object { "$_" -like 'AFK-EVENT:*' })
  Assert-True 'recovery emits structured events when requested' ($eventLines.Count -ge 2) ($events -join ' | ')
  foreach ($line in $eventLines) {
    $parsedEvent = "$line".Substring('AFK-EVENT:'.Length) | ConvertFrom-Json
    Assert-Equal 'event schema is versioned' 1 $parsedEvent.schema_version
    Assert-Equal 'event identifies the recovery phase' recovery $parsedEvent.phase
    Assert-True 'event contains a UTC timestamp' ("$line" -match '"timestamp_utc":"[^"]+Z"')
  }
} finally {
  Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
}

$entryPath = Join-Path $Root 'installer/Invoke-Recovery.ps1'
Assert-True 'shell-facing recovery entry point exists' (Test-Path -LiteralPath $entryPath -PathType Leaf)
if (Test-Path -LiteralPath $entryPath -PathType Leaf) {
  $entry = Get-ContractText -Path $entryPath
  foreach ($exitCode in @(0, 10, 20, 1)) {
    Assert-True "recovery entry point defines exit $exitCode" ($entry -match "exit\s+$exitCode\b")
  }
  Assert-True 'elevated recovery hides the Windows PowerShell window' ($entry -match 'WindowStyle.+Hidden')
}

$orchestrator = Get-ContractText -Path (Join-Path $Root 'installer/Install-LocalAI.ps1')
Assert-True 'orchestrator accepts EventStream' ($orchestrator -match '\[switch\]\$EventStream')
Assert-True 'orchestrator accepts LegacyInstallRoot' ($orchestrator -match '\[string\]\$LegacyInstallRoot')
foreach ($kind in @('phase-start', 'phase-success', 'phase-failure', 'checkpoint')) {
  Assert-True "orchestrator emits $kind events" ($orchestrator -match "Write-InstallerEvent[\s\S]{0,180}$kind")
}

Write-Host ''
if ($script:Fail -gt 0) {
  Write-Host "FAILURES ($script:Fail):" -ForegroundColor Red
  foreach ($failure in $script:Failures) { Write-Host "  - $failure" -ForegroundColor Red }
  Write-Host "RECOVERY TESTS FAILED: $script:Pass passed, $script:Fail failed." -ForegroundColor Red
  exit 1
}
Write-Host "RECOVERY TESTS PASSED: $script:Pass passed, 0 failed." -ForegroundColor Green
exit 0
