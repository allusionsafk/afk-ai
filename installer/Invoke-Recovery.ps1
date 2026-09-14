#requires -Version 5.1
[CmdletBinding()]
param(
  [string]$Code = '',
  [string]$ActionId = '',
  [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'AFK LocalAI\State'),
  [switch]$EventStream,
  [switch]$ElevatedChild
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'installer-common.ps1')
. (Join-Path $PSScriptRoot 'recovery.ps1')

function Test-CurrentProcessElevated {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

try {
  $definition = $null
  if ($Code) {
    $definition = Get-PreflightRecovery -Code $Code
    if ($ActionId -and $ActionId -ne $definition.ActionId) {
      throw "Requested action '$ActionId' does not match the safe recovery '$($definition.ActionId)' for '$Code'."
    }
    $ActionId = $definition.ActionId
  }
  if (-not $ActionId) { throw 'Specify a preflight Code or an explicit allowlisted ActionId.' }

  if ($ActionId -notin @('install-wsl', 'update-wsl', 'install-docker', 'update-docker', 'start-docker', 'restart-windows', 'retry')) {
    Write-InstallerEvent -Enabled:$EventStream -EventType 'checkpoint' -Phase 'recovery' -Status 'guidance' -Code $ActionId -Message $definition.Explanation
    exit 20
  }

  $requiresElevation = $ActionId -in @('install-wsl', 'update-wsl', 'restart-windows')
  if ($requiresElevation -and -not $ElevatedChild -and -not (Test-CurrentProcessElevated)) {
    Write-InstallerEvent -Enabled:$EventStream -EventType 'checkpoint' -Phase 'recovery' -Status 'elevation-required' -Code $ActionId -Message 'Windows approval is required for this one prerequisite action.'
    $escapedScript = $PSCommandPath.Replace("'", "''")
    $escapedAction = $ActionId.Replace("'", "''")
    $escapedData = ([System.IO.Path]::GetFullPath($DataRoot)).Replace("'", "''")
    $child = "& '$escapedScript' -ActionId '$escapedAction' -DataRoot '$escapedData' -ElevatedChild" + $(if ($EventStream) { ' -EventStream' } else { '' }) + '; exit $LASTEXITCODE'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($child))
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -notin @(0, 10)) { exit 1 }
    exit $process.ExitCode
  }

  Invoke-RecoveryAction -ActionId $ActionId -DataRoot $DataRoot -EventStream:$EventStream
  if ($ActionId -eq 'retry') { exit 0 }
  exit 10
} catch {
  Write-InstallerEvent -Enabled:$EventStream -EventType 'phase-failure' -Phase 'recovery' -Status 'failure' -Code $ActionId -Message $_.Exception.Message
  if (-not $EventStream) { Write-Error $_.Exception.Message }
  exit 1
}
