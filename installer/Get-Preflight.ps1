<#
  Shell-facing, read-only AFK LocalAI environment check.

  Runs on the Windows PowerShell 5.1 included with Windows 11. The only write is
  the privacy-bounded resume checkpoint under DataRoot; machine probing remains
  read-only and performs no network access.
#>
[CmdletBinding()]
param(
  [switch]$Json,
  [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'AFK LocalAI\State')
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
. (Join-Path $RepoRoot 'ai-common.ps1')
. (Join-Path $PSScriptRoot 'installer-common.ps1')
. (Join-Path $PSScriptRoot 'preflight.ps1')

$DataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$statePath = Get-InstallerStatePath -Root $DataRoot

# Keep the JSON channel pristine even when a corrupt checkpoint is quarantined.
$oldInformationPreference = $InformationPreference
if ($Json) { $InformationPreference = 'SilentlyContinue' }
try {
  $state = Import-InstallerState -Path $statePath
  $result = Invoke-EnvironmentPreflight -Evidence (Get-PreflightEvidence)
  $checkpoint = Get-PreflightCheckpoint -Result $result
  $state.preflight = $checkpoint
  $state.pending_reboot = [pscustomobject]@{
    required = [bool]$result.RebootRequired
    reason = $(if ($result.RebootRequired) { $result.Reason } else { $null })
  }
  Save-InstallerState -State $state -Path $statePath
} finally {
  $InformationPreference = $oldInformationPreference
}

$payload = [pscustomobject]@{
  schema_version = 1
  classifier_version = $result.ClassifierVersion
  overall = $result.Overall
  reason = $result.Reason
  code = $result.Code
  action = $result.Action
  reboot_required = [bool]$result.RebootRequired
  components = $checkpoint.components
  user_message = @(Get-PreflightUserMessage -Result $result)
  diagnostics = @(Get-PreflightDiagnosticSummary -Result $result)
  state_path = $statePath
}

if ($Json) {
  $payload | ConvertTo-Json -Depth 8 -Compress
} else {
  Get-PreflightUserMessage -Result $result | ForEach-Object { Write-Host $_ }
  Write-Host ''
  Get-PreflightDiagnosticSummary -Result $result | ForEach-Object { Write-Host "  $_" }
}

if ($result.Overall -eq 'READY') { exit 0 }
if ($result.Overall -eq 'RECOVERABLE_BLOCKER') { exit 10 }
exit 20
