#requires -Version 5.1
<#
  Pure recovery mapping plus the deliberately small action executor used by the
  AFK LocalAI shell. Unknown actions fail closed.
#>

function New-RecoveryDefinition {
  param([string]$ActionId, [string]$Label, [bool]$RequiresElevation, [bool]$Automatic, [string]$Explanation)
  [pscustomobject]@{
    ActionId = $ActionId
    Label = $Label
    RequiresElevation = $RequiresElevation
    Automatic = $Automatic
    Explanation = $Explanation
  }
}

function Get-PreflightRecovery {
  param([Parameter(Mandatory)][string]$Code)
  switch ($Code) {
    'PREFLIGHT-READY' { return (New-RecoveryDefinition 'retry' 'Continue' $false $true 'Re-check this PC and continue setup.') }
    'PREFLIGHT-UNSUPPORTED-PLATFORM' { return (New-RecoveryDefinition 'support-guidance' 'View supported systems' $false $false 'AFK LocalAI requires 64-bit Windows 11. Diagnostics can help support confirm the detected version.') }
    'PREFLIGHT-UNKNOWN' { return (New-RecoveryDefinition 'support-guidance' 'Open diagnostics' $false $false 'No automatic change is safe while prerequisite evidence is incomplete or contradictory.') }
    'PREFLIGHT-FIRMWARE-VIRT-DISABLED' { return (New-RecoveryDefinition 'firmware-guidance' 'Show virtualization instructions' $false $false 'Virtualization must be enabled in the PC firmware. AFK LocalAI explains the step but does not change firmware settings.') }
    'PREFLIGHT-WINDOWS-FEATURE-MISSING' { return (New-RecoveryDefinition 'install-wsl' 'Set up Windows virtualization' $true $true 'Windows can enable the required WSL and virtualization components with one approved system action.') }
    'PREFLIGHT-WINDOWS-REBOOT-REQUIRED' { return (New-RecoveryDefinition 'restart-windows' 'Restart Windows' $true $false 'A restart is required before Windows virtualization changes can take effect.') }
    'PREFLIGHT-HYPERVISOR-NOT-RUNNING' { return (New-RecoveryDefinition 'restart-windows' 'Restart Windows' $true $false 'Restarting Windows is the safest first recovery when configured virtualization is not active.') }
    'PREFLIGHT-WSL-NOT-INSTALLED' { return (New-RecoveryDefinition 'install-wsl' 'Install WSL components' $true $true 'Windows will install the WSL platform without adding an unnecessary Linux distribution.') }
    'PREFLIGHT-WSL-UPDATE-REQUIRED' { return (New-RecoveryDefinition 'update-wsl' 'Update WSL' $true $true 'Windows will update its WSL runtime, then AFK LocalAI will check again.') }
    'PREFLIGHT-WSL-UNHEALTHY' { return (New-RecoveryDefinition 'update-wsl' 'Repair WSL runtime' $true $true 'Updating WSL is the bounded recovery for a present but unhealthy runtime.') }
    'PREFLIGHT-DOCKER-REMOTE-CONTEXT' { return (New-RecoveryDefinition 'docker-context-guidance' 'Show Docker context guidance' $false $false 'AFK LocalAI will not alter an advanced remote Docker configuration automatically.') }
    'PREFLIGHT-DOCKER-LINUX-ENGINE-REQUIRED' { return (New-RecoveryDefinition 'docker-linux-guidance' 'Show Linux engine guidance' $false $false 'AFK LocalAI requires a local Linux container engine and will not switch container mode automatically.') }
    'PREFLIGHT-DOCKER-VERSION-UNSUPPORTED' { return (New-RecoveryDefinition 'update-docker' 'Update Docker Desktop' $false $true 'Windows Package Manager will update Docker Desktop from its official package.') }
    'PREFLIGHT-DOCKER-NOT-INSTALLED' { return (New-RecoveryDefinition 'install-docker' 'Install Docker Desktop' $false $true 'Windows Package Manager will install Docker Desktop from its official package.') }
    'PREFLIGHT-DOCKER-STARTING' { return (New-RecoveryDefinition 'retry' 'Check again' $false $true 'Docker Desktop is still starting; wait briefly and check its local engine again.') }
    'PREFLIGHT-DOCKER-NOT-RUNNING' { return (New-RecoveryDefinition 'start-docker' 'Start Docker Desktop' $false $true 'Open Docker Desktop, let it finish starting, then check its local engine again.') }
    default { throw "Unknown AFK LocalAI preflight reason code '$Code'. No recovery action was selected." }
  }
}

function Get-RecoveryCommand {
  param([Parameter(Mandatory)][string]$ActionId, [string]$ProgramFilesRoot = $env:ProgramFiles)
  switch ($ActionId) {
    'retry' { return $null }
    'install-wsl' { return [pscustomobject]@{ FilePath = 'wsl.exe'; Arguments = @('--install', '--no-distribution'); Gui = $false } }
    'update-wsl' { return [pscustomobject]@{ FilePath = 'wsl.exe'; Arguments = @('--update'); Gui = $false } }
    'install-docker' { return [pscustomobject]@{ FilePath = 'winget.exe'; Arguments = @('install', '--id', 'Docker.DockerDesktop', '--exact', '--source', 'winget', '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity'); Gui = $false } }
    'update-docker' { return [pscustomobject]@{ FilePath = 'winget.exe'; Arguments = @('upgrade', '--id', 'Docker.DockerDesktop', '--exact', '--source', 'winget', '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity'); Gui = $false } }
    'start-docker' { return [pscustomobject]@{ FilePath = (Join-Path $ProgramFilesRoot 'Docker\Docker\Docker Desktop.exe'); Arguments = @(); Gui = $true } }
    'restart-windows' { return [pscustomobject]@{ FilePath = 'shutdown.exe'; Arguments = @('/r', '/t', '0', '/d', 'p:2:4', '/c', 'AFK LocalAI prerequisite setup'); Gui = $false } }
    default { throw "Recovery action '$ActionId' is not in the AFK LocalAI allowlist." }
  }
}

function ConvertTo-RecoveryArgumentString {
  param([string[]]$Arguments)
  return (($Arguments | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_.Replace('"', '\"')) + '"' } else { $_ }
  }) -join ' ')
}

function Invoke-RecoveryProcess {
  param([Parameter(Mandatory)]$Command)
  if ($Command.Gui) {
    if (-not (Test-Path -LiteralPath $Command.FilePath -PathType Leaf)) {
      throw "Docker Desktop was detected but its application could not be found at '$($Command.FilePath)'."
    }
    [void](Start-Process -FilePath $Command.FilePath -ErrorAction Stop)
    return 0
  }

  $info = New-Object System.Diagnostics.ProcessStartInfo
  $info.FileName = $Command.FilePath
  $info.Arguments = ConvertTo-RecoveryArgumentString -Arguments $Command.Arguments
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  $info.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
  $info.RedirectStandardOutput = $true
  $info.RedirectStandardError = $true
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $info
  if (-not $process.Start()) { throw "Could not start recovery command '$($Command.FilePath)'." }
  $stdout = $process.StandardOutput.ReadToEnd()
  $stderr = $process.StandardError.ReadToEnd()
  $process.WaitForExit()
  if ($process.ExitCode -notin @(0, 3010)) {
    $detail = @($stderr, $stdout) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1
    throw "Recovery command '$($Command.FilePath)' exited $($process.ExitCode). $detail"
  }
  return $process.ExitCode
}

function Invoke-RecoveryAction {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory)][ValidateSet('install-wsl', 'update-wsl', 'install-docker', 'update-docker', 'start-docker', 'restart-windows', 'retry')][string]$ActionId,
    [Parameter(Mandatory)][string]$DataRoot,
    [switch]$EventStream,
    [string]$ProgramFilesRoot = $env:ProgramFiles,
    [scriptblock]$ProcessInvoker
  )

  $DataRoot = [System.IO.Path]::GetFullPath($DataRoot)
  $statePath = Get-InstallerStatePath -Root $DataRoot
  $state = Import-InstallerState -Path $statePath
  $attempt = 1
  if ($state.recovery -and $state.recovery.action_id -eq $ActionId) {
    $attempt = [int]$state.recovery.attempt + 1
  }

  $state.preflight = $null
  $state.recovery = [pscustomobject]@{
    action_id = $ActionId
    status = 'running'
    attempt = $attempt
    started_at = [DateTime]::UtcNow.ToString('o')
    completed_at = $null
    error = $null
  }
  Save-InstallerState -State $state -Path $statePath
  Write-InstallerEvent -Enabled:$EventStream -EventType 'phase-start' -Phase 'recovery' -Status 'running' -Code $ActionId -Message 'Recovery action started.'

  try {
    $command = Get-RecoveryCommand -ActionId $ActionId -ProgramFilesRoot $ProgramFilesRoot
    if ($null -ne $command) {
      if ($ProcessInvoker) { [void](& $ProcessInvoker $command) }
      else { [void](Invoke-RecoveryProcess -Command $command) }
    }
    $state.recovery.status = 'success'
    $state.recovery.completed_at = [DateTime]::UtcNow.ToString('o')
    Save-InstallerState -State $state -Path $statePath
    Write-InstallerEvent -Enabled:$EventStream -EventType 'phase-success' -Phase 'recovery' -Status 'success' -Code $ActionId -Message 'Recovery action completed; prerequisites must be checked again.'
  } catch {
    $state.recovery.status = 'failure'
    $state.recovery.completed_at = [DateTime]::UtcNow.ToString('o')
    $state.recovery.error = $_.Exception.Message
    Save-InstallerState -State $state -Path $statePath
    Write-InstallerEvent -Enabled:$EventStream -EventType 'phase-failure' -Phase 'recovery' -Status 'failure' -Code $ActionId -Message $_.Exception.Message
    throw
  }
}
