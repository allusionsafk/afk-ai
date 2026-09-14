#requires -Version 7.0
<#
  tests/Test-FirstRunTransitions.ps1 - the first-run state machine, end to end.

  The preflight suite proves the classifier and the recovery suite proves the
  action registry. Neither proves the thing a user actually experiences: that a
  given machine state produces one honest diagnosis, one bounded recovery, the
  right elevation and restart expectations, and a checkpoint that resumes at the
  correct phase.

  This suite drives that whole chain from machine evidence, as a matrix. Every
  row is a state the installer is expected to meet on a real first run.

  Fixture-only: it starts nothing, changes no Windows state, and touches no
  network. Recovery actions are resolved and inspected, never executed.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'contract-common.ps1')
. (Join-Path $Root 'installer/installer-common.ps1')
. (Join-Path $Root 'installer/preflight.ps1')
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

# ------------------------------------------------------------------ fixtures

function New-Machine {
  # A fully ready machine. Each row overrides only the layer it is about, so a
  # fixture states its own subject and nothing else.
  param([hashtable]$Override = @{})
  $base = @{
    Platform = [pscustomobject]@{ IsWindows = $true; Build = 26100; ProductType = 1; Queried = $true; Error = $null }
    Firmware = [pscustomobject]@{ Values = @($true); Queried = $true; Error = $null }
    WindowsVirtualization = [pscustomobject]@{
      HypervisorPresent = $true
      Features = @{ 'VirtualMachinePlatform' = 'Enabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' }
      PendingReboot = $false; Queried = $true; Error = $null
    }
    Wsl = [pscustomobject]@{ CommandFound = $true; VersionExit = 0; VersionText = 'WSL version: 2.6.1.0'; TimedOut = $false; Error = $null }
    Docker = [pscustomobject]@{
      CliFound = $true; DesktopProcessRunning = $true; DockerHostEnv = $null; DockerContextEnv = $null
      ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
      InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '28.1.1'
      InfoText = ''; TimedOut = $false; Error = $null
    }
  }
  foreach ($key in $Override.Keys) { $base[$key] = $Override[$key] }
  return [pscustomobject]$base
}

function New-Docker {
  param($Override = @{})
  $docker = @{
    CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null; DockerContextEnv = $null
    ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
    InfoExit = 1; InfoOsType = $null; InfoServerVersion = $null
    InfoText = ''; TimedOut = $false; Error = $null
  }
  foreach ($key in $Override.Keys) { $docker[$key] = $Override[$key] }
  return [pscustomobject]$docker
}

$WslOutdated   = [pscustomobject]@{ CommandFound = $true; VersionExit = 0; VersionText = 'WSL version: 1.2.5.0'; TimedOut = $false; Error = $null }
$WslMissing    = [pscustomobject]@{ CommandFound = $false; VersionExit = $null; VersionText = ''; TimedOut = $false; Error = $null }
$WslUnhealthy  = [pscustomobject]@{ CommandFound = $true; VersionExit = $null; VersionText = ''; TimedOut = $true; Error = $null }
$FirmwareOff   = [pscustomobject]@{ Values = @($false); Queried = $true; Error = $null }
$FirmwareUnknown = [pscustomobject]@{ Values = @(); Queried = $false; Error = 'rpc unavailable' }

function New-WinVirt {
  param($Hypervisor = $true, [hashtable]$Features, $PendingReboot = $false, [bool]$Queried = $true)
  if ($null -eq $Features) { $Features = @{ 'VirtualMachinePlatform' = 'Enabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' } }
  return [pscustomobject]@{ HypervisorPresent = $Hypervisor; Features = $Features; PendingReboot = $PendingReboot; Queried = $Queried; Error = $null }
}

# ------------------------------------------------------------ the matrix
# Columns: machine state -> overall, reason, code, recovery action, elevation,
# restart. One row per state the product claims to distinguish.

Write-Host '-- first-run transition matrix' -ForegroundColor Cyan

$matrix = @(
  @{ Name = 'READY'
     Evidence = (New-Machine)
     Overall = 'READY'; Reason = 'READY'; Code = 'PREFLIGHT-READY'
     Action = 'retry'; Elevation = $false; Reboot = $false }

  @{ Name = 'firmware virtualization disabled'
     Evidence = (New-Machine @{ Firmware = $FirmwareOff; WindowsVirtualization = (New-WinVirt -Hypervisor $false); Docker = (New-Docker) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'FIRMWARE_VIRTUALIZATION_DISABLED'; Code = 'PREFLIGHT-FIRMWARE-VIRT-DISABLED'
     Action = 'firmware-guidance'; Elevation = $false; Reboot = $false }

  @{ Name = 'Windows virtualization feature missing'
     Evidence = (New-Machine @{ WindowsVirtualization = (New-WinVirt -Features @{ 'VirtualMachinePlatform' = 'Disabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' }); Docker = (New-Docker) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'WINDOWS_VIRTUALIZATION_FEATURE_MISSING'; Code = 'PREFLIGHT-WINDOWS-FEATURE-MISSING'
     Action = 'install-wsl'; Elevation = $true; Reboot = $false }

  @{ Name = 'Windows restart required'
     Evidence = (New-Machine @{ WindowsVirtualization = (New-WinVirt -Hypervisor $false -PendingReboot $true); Docker = (New-Docker) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED'; Code = 'PREFLIGHT-WINDOWS-REBOOT-REQUIRED'
     Action = 'restart-windows'; Elevation = $true; Reboot = $true }

  @{ Name = 'hypervisor configured but not running'
     Evidence = (New-Machine @{ WindowsVirtualization = (New-WinVirt -Hypervisor $false); Docker = (New-Docker) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'WINDOWS_HYPERVISOR_NOT_RUNNING'; Code = 'PREFLIGHT-HYPERVISOR-NOT-RUNNING'
     Action = 'restart-windows'; Elevation = $true; Reboot = $false }

  @{ Name = 'WSL not installed'
     Evidence = (New-Machine @{ Wsl = $WslMissing; Docker = (New-Docker) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'WSL_NOT_INSTALLED'; Code = 'PREFLIGHT-WSL-NOT-INSTALLED'
     Action = 'install-wsl'; Elevation = $true; Reboot = $false }

  @{ Name = 'WSL outdated'
     Evidence = (New-Machine @{ Wsl = $WslOutdated; Docker = (New-Docker) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'WSL_UPDATE_REQUIRED'; Code = 'PREFLIGHT-WSL-UPDATE-REQUIRED'
     Action = 'update-wsl'; Elevation = $true; Reboot = $false }

  @{ Name = 'WSL unhealthy'
     Evidence = (New-Machine @{ Wsl = $WslUnhealthy; Docker = (New-Docker) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'WSL_UNHEALTHY'; Code = 'PREFLIGHT-WSL-UNHEALTHY'
     Action = 'update-wsl'; Elevation = $true; Reboot = $false }

  @{ Name = 'Docker not installed'
     Evidence = (New-Machine @{ Docker = (New-Docker @{ CliFound = $false; ContextEndpoint = $null; InfoExit = $null }) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'DOCKER_NOT_INSTALLED'; Code = 'PREFLIGHT-DOCKER-NOT-INSTALLED'
     Action = 'install-docker'; Elevation = $false; Reboot = $false }

  @{ Name = 'Docker installed but not running'
     Evidence = (New-Machine @{ Docker = (New-Docker @{ DesktopProcessRunning = $false; InfoText = 'error during connect: open //./pipe/dockerDesktopLinuxEngine' }) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'DOCKER_INSTALLED_NOT_RUNNING'; Code = 'PREFLIGHT-DOCKER-NOT-RUNNING'
     Action = 'start-docker'; Elevation = $false; Reboot = $false }

  @{ Name = 'Docker still starting'
     Evidence = (New-Machine @{ Docker = (New-Docker @{ DesktopProcessRunning = $true; InfoText = 'error during connect: open //./pipe/dockerDesktopLinuxEngine' }) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'DOCKER_STARTING'; Code = 'PREFLIGHT-DOCKER-STARTING'
     Action = 'retry'; Elevation = $false; Reboot = $false }

  @{ Name = 'Docker on a remote context'
     Evidence = (New-Machine @{ Docker = (New-Docker @{ ContextEndpoint = 'tcp://10.0.0.5:2376'; InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '28.1.1' }) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'DOCKER_CONTEXT_REMOTE'; Code = 'PREFLIGHT-DOCKER-REMOTE-CONTEXT'
     Action = 'docker-context-guidance'; Elevation = $false; Reboot = $false }

  @{ Name = 'Docker on the wrong engine'
     Evidence = (New-Machine @{ Docker = (New-Docker @{ DesktopProcessRunning = $true; InfoExit = 0; InfoOsType = 'windows'; InfoServerVersion = '28.1.1' }) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'DOCKER_LINUX_ENGINE_REQUIRED'; Code = 'PREFLIGHT-DOCKER-LINUX-ENGINE-REQUIRED'
     Action = 'docker-linux-guidance'; Elevation = $false; Reboot = $false }

  @{ Name = 'insufficient evidence beneath the stack'
     Evidence = (New-Machine @{ Firmware = $FirmwareUnknown; WindowsVirtualization = (New-WinVirt -Hypervisor $null); Docker = (New-Docker) })
     Overall = 'UNKNOWN_BLOCKER'; Reason = 'FIRMWARE_UNKNOWN'; Code = 'PREFLIGHT-UNKNOWN'
     Action = 'support-guidance'; Elevation = $false; Reboot = $false }

  @{ Name = 'contradictory virtualization evidence'
     Evidence = (New-Machine @{ Docker = (New-Docker @{ DesktopProcessRunning = $true; InfoText = 'virtualization support is disabled in the BIOS' }) })
     Overall = 'UNKNOWN_BLOCKER'; Reason = 'CONFLICTING_VIRTUALIZATION_EVIDENCE'; Code = 'PREFLIGHT-UNKNOWN'
     Action = 'support-guidance'; Elevation = $false; Reboot = $false }

  # The combination that actually shipped broken. A conclusive blocker beneath
  # an UNKNOWN layer: the recovery for the lower layer is established and safe,
  # and must not be hidden behind the layer above it. Reproduced from a real
  # installer run whose checkpoint recorded exactly this.
  @{ Name = 'WSL outdated beneath an unobservable Docker'
     Evidence = (New-Machine @{ Wsl = $WslOutdated; Docker = (New-Docker @{ ContextEndpoint = $null; InfoExit = $null }) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'WSL_UPDATE_REQUIRED'; Code = 'PREFLIGHT-WSL-UPDATE-REQUIRED'
     Action = 'update-wsl'; Elevation = $true; Reboot = $false }

  @{ Name = 'restart owed beneath an unobservable Docker'
     Evidence = (New-Machine @{ WindowsVirtualization = (New-WinVirt -Hypervisor $false -PendingReboot $true); Docker = (New-Docker @{ ContextEndpoint = $null; InfoExit = $null }) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED'; Code = 'PREFLIGHT-WINDOWS-REBOOT-REQUIRED'
     Action = 'restart-windows'; Elevation = $true; Reboot = $true }

  @{ Name = 'WSL missing beneath an unobservable Docker'
     Evidence = (New-Machine @{ Wsl = $WslMissing; Docker = (New-Docker @{ ContextEndpoint = $null; InfoExit = $null }) })
     Overall = 'RECOVERABLE_BLOCKER'; Reason = 'WSL_NOT_INSTALLED'; Code = 'PREFLIGHT-WSL-NOT-INSTALLED'
     Action = 'install-wsl'; Elevation = $true; Reboot = $false }

  @{ Name = 'Docker state genuinely unobservable'
     Evidence = (New-Machine @{ Docker = (New-Docker @{ ContextEndpoint = $null; InfoExit = $null }) })
     Overall = 'UNKNOWN_BLOCKER'; Reason = 'DOCKER_UNKNOWN'; Code = 'PREFLIGHT-UNKNOWN'
     Action = 'support-guidance'; Elevation = $false; Reboot = $false }
)

foreach ($row in $matrix) {
  $result = Invoke-EnvironmentPreflight -Evidence $row.Evidence
  Assert-Equal -Case "$($row.Name): overall"  -Expected $row.Overall -Actual $result.Overall
  Assert-Equal -Case "$($row.Name): reason"   -Expected $row.Reason  -Actual $result.Reason
  Assert-Equal -Case "$($row.Name): code"     -Expected $row.Code    -Actual $result.Code
  Assert-Equal -Case "$($row.Name): restart"  -Expected $row.Reboot  -Actual $result.RebootRequired

  $recovery = Get-PreflightRecovery -Code $result.Code
  Assert-Equal -Case "$($row.Name): recovery action"    -Expected $row.Action    -Actual $recovery.ActionId
  Assert-Equal -Case "$($row.Name): elevation required" -Expected $row.Elevation -Actual $recovery.RequiresElevation
  Assert-True  -Case "$($row.Name): recovery explains itself" -Condition (-not [string]::IsNullOrWhiteSpace($recovery.Explanation))

  # Every row must answer "what will AFK AI do?" - either a bounded machine
  # action from the allowlist, or an explicit guidance-only answer.
  $command = $null
  $guidanceOnly = $false
  try { $command = Get-RecoveryCommand -ActionId $recovery.ActionId }
  catch { $guidanceOnly = $true }
  # A row that claims elevation must actually carry a machine command; a
  # guidance-only row must never carry one.
  if ($row.Elevation) {
    Assert-True -Case "$($row.Name): an elevated row carries a real command" `
      -Condition ((-not $guidanceOnly) -and $null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.FilePath)) `
      "action=$($recovery.ActionId)"
  }
  if ($recovery.ActionId -like '*-guidance') {
    Assert-True -Case "$($row.Name): guidance-only row has no machine command" -Condition $guidanceOnly
  }

  # A checkpoint must be persistable for every row, and must round-trip the
  # reason code that produced it.
  $checkpoint = Get-PreflightCheckpoint -Result $result
  Assert-Equal -Case "$($row.Name): checkpoint records the code" -Expected $row.Code -Actual $checkpoint.code
}

# ----------------------------------------------- guidance-only stays guidance
Write-Host '-- guidance-only actions never mutate the machine' -ForegroundColor Cyan
foreach ($actionId in @('firmware-guidance', 'docker-context-guidance', 'docker-linux-guidance', 'support-guidance')) {
  $threw = $false
  try { [void](Get-RecoveryCommand -ActionId $actionId) } catch { $threw = $true }
  Assert-True -Case "$actionId has no machine command" -Condition $threw
}

# 'retry' is allowlisted but deliberately does nothing to the machine.
Assert-Equal -Case 'retry performs no machine command' -Expected '' -Actual "$(Get-RecoveryCommand -ActionId 'retry')"

# Anything outside the allowlist fails closed.
$rejected = $false
try { [void](Get-RecoveryCommand -ActionId 'prune-docker') } catch { $rejected = $true }
Assert-True -Case 'an action outside the allowlist is rejected' -Condition $rejected

# ------------------------------------------------- shell / installer parity
# The recovery table exists twice: once in PowerShell for the installer and once
# in C# for the shell. Drift would let the window offer one action while the
# installer performs another, so they are compared code by code.
Write-Host '-- shell and installer agree on every recovery' -ForegroundColor Cyan

$modelsPath = Join-Path $Root 'src/AFKLocalAI.App/ProvisioningModels.cs'
if (Test-Path -LiteralPath $modelsPath) {
  $models = Get-ContractText -Path $modelsPath
  $shellRows = [regex]::Matches(
    $models,
    '"(?<code>PREFLIGHT-[A-Z0-9-]+)"\s*=>\s*new\(\s*"(?<action>[a-z-]+)"\s*,\s*"[^"]*"\s*,\s*(?<elevation>true|false)\s*,\s*(?<automatic>true|false)')

  # Derived from the classifier rather than hard-coded, so a new reason code
  # cannot be added on one side only.
  $emittableCodes = @('PREFLIGHT-READY', 'PREFLIGHT-UNSUPPORTED-PLATFORM', 'PREFLIGHT-UNKNOWN') +
    @($script:PreflightBlockerCodes.Values)
  $shellCodes = @($shellRows | ForEach-Object { $_.Groups['code'].Value })
  foreach ($code in $emittableCodes) {
    Assert-True -Case "the shell defines a recovery for $code" -Condition ($shellCodes -contains $code)
  }
  Assert-True -Case 'the shell defines no recovery the classifier cannot emit' `
    -Condition (@($shellCodes | Where-Object { $_ -notin $emittableCodes }).Count -eq 0) `
    "extra: $(@($shellCodes | Where-Object { $_ -notin $emittableCodes }) -join ', ')"

  foreach ($row in $shellRows) {
    $code = $row.Groups['code'].Value
    $installer = Get-PreflightRecovery -Code $code
    Assert-Equal -Case "$code : shell and installer choose the same action" `
      -Expected $installer.ActionId -Actual $row.Groups['action'].Value
    Assert-Equal -Case "$code : shell and installer agree on elevation" `
      -Expected ([bool]$installer.RequiresElevation) -Actual ([bool]::Parse($row.Groups['elevation'].Value))
    Assert-Equal -Case "$code : shell and installer agree on automation" `
      -Expected ([bool]$installer.Automatic) -Actual ([bool]::Parse($row.Groups['automatic'].Value))
  }
}

# ------------------------------------------------------- resume / idempotence
Write-Host '-- resume and idempotence' -ForegroundColor Cyan

$scratch = Join-Path ([IO.Path]::GetTempPath()) ("afk-firstrun-" + [guid]::NewGuid().ToString('n'))
[void](New-Item -ItemType Directory -Path $scratch -Force)
try {
  $statePath = Get-InstallerStatePath -Root $scratch
  $state = Import-InstallerState -Path $statePath

  # Phases complete in order and are remembered.
  foreach ($phase in @('vet', 'intent', 'python')) {
    Set-PhaseDone -State $state -Phase $phase -Path $statePath
  }
  $reloaded = Import-InstallerState -Path $statePath
  Assert-True -Case 'completed phases survive a relaunch' `
    -Condition ((Test-PhaseDone -State $reloaded -Phase 'vet') -and (Test-PhaseDone -State $reloaded -Phase 'python'))
  Assert-True -Case 'an unreached phase is not claimed as done' `
    -Condition (-not (Test-PhaseDone -State $reloaded -Phase 'pulls'))

  # Recording the same phase twice must not duplicate it.
  Set-PhaseDone -State $reloaded -Phase 'vet' -Path $statePath
  $again = Import-InstallerState -Path $statePath
  Assert-Equal -Case 'repeating a completed phase does not duplicate it' `
    -Expected 1 -Actual (@(@($again.phases_done) | Where-Object { $_ -eq 'vet' }).Count)

  # Live-machine phases are never trusted from persisted state: after a restart
  # the environment must be observed again, not assumed.
  Set-PhaseDone -State $again -Phase 'environment-preflight' -Path $statePath
  $afterRestart = Import-InstallerState -Path $statePath
  Assert-True -Case 'a stale environment result is never treated as done' `
    -Condition (-not (Test-PhaseDone -State $afterRestart -Phase 'environment-preflight'))
  Assert-True -Case 'and expensive completed work is still remembered across it' `
    -Condition (Test-PhaseDone -State $afterRestart -Phase 'python')

  # A pending reboot round-trips, so a relaunch knows a restart is owed.
  $afterRestart.pending_reboot = [pscustomobject]@{ required = $true; reason = 'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED' }
  Save-InstallerState -State $afterRestart -Path $statePath
  $rebooted = Import-InstallerState -Path $statePath
  Assert-True -Case 'a pending restart survives a relaunch' -Condition $rebooted.pending_reboot.required
  Assert-Equal -Case 'and records why the restart is owed' `
    -Expected 'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED' -Actual $rebooted.pending_reboot.reason
} finally {
  Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------- summary
Write-Host ''
if ($script:Fail -gt 0) {
  Write-Host "FAILURES ($script:Fail):" -ForegroundColor Red
  foreach ($failure in $script:Failures) { Write-Host "  - $failure" -ForegroundColor Red }
  Write-Host ''
  Write-Host "FIRST-RUN TRANSITION TESTS FAILED: $script:Pass passed, $script:Fail failed." -ForegroundColor Red
  exit 1
}
Write-Host "FIRST-RUN TRANSITION TESTS PASSED: $script:Pass passed, 0 failed." -ForegroundColor Green
exit 0
