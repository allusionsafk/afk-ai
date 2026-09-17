#requires -Version 5.1
<#
  ai-firewall.ps1 - Audit and repair the intended localai firewall posture.

  Intended state:
    - Open WebUI is published by Docker on 127.0.0.1 only.
    - Device access goes through Tailscale Serve, not raw LAN/Public firewall
      exposure.
    - Open WebUI, Ollama, SearXNG, Kokoro, and ComfyUI ports are not opened to
      the LAN by Windows Firewall rules.
    - A localai-owned block rule protects those ports on physical WiFi/Ethernet
      adapters even if a third-party app has broad allow rules.

  By default this script audits only. Use -Apply to remove only localai-owned
  legacy allow rules and recreate the physical-adapter block rule. It does not
  disable third-party rules such as Zoom or Windows app rules; those are
  reported for manual review.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [switch]$Apply,
  [switch]$NoSelfElevate
)

$ErrorActionPreference = 'Stop'

$LegacyOpenRuleName = 'Open WebUI LAN (localai)'
$LegacyOpenRuleId = 'LocalAI-OpenWebUI-LAN'
$LegacyTailscaleRuleName = 'Open WebUI Tailscale (localai)'
$LegacyTailscaleRuleId = 'LocalAI-OpenWebUI-Tailscale'
$PhysicalBlockRuleName = 'Block LocalAI ports on physical networks (localai)'
$PhysicalBlockRuleId = 'LocalAI-Block-Physical-Ports'
$OpenPort = 3000
$LocalAIPorts = @(3000, 8888, 11434, 8080, 8880, 8188)
$LocalAIRuleDisplayNames = @($LegacyOpenRuleName, $LegacyTailscaleRuleName)
$LocalAIRuleIds = @($LegacyOpenRuleId, $LegacyTailscaleRuleId, $PhysicalBlockRuleId)

$Ok = 0
$Warn = 0
$Fail = 0

function Line([string]$Status, [string]$Name, [string]$Detail) {
  $color = switch ($Status) {
    'OK' { 'Green' }
    'WARN' { 'Yellow' }
    'FAIL' { 'Red' }
    default { 'Gray' }
  }
  if ($Status -eq 'OK') { $script:Ok++ }
  elseif ($Status -eq 'WARN') { $script:Warn++ }
  elseif ($Status -eq 'FAIL') { $script:Fail++ }
  Write-Host ("[{0}] {1,-24} {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($id)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-SelfElevatedApplyArguments([string]$ScriptPath, [string]$LogPath) {
  # Both paths go inside single-quoted literals of the elevated -Command. A
  # profile or install path with an apostrophe (O'Brien, or the typographic
  # U+2019 that PowerShell also treats as a quote) would otherwise end the
  # literal early, the firewall rule would never be applied, and setup - which
  # requires a verified rule - could never finish.
  $escape = [System.Management.Automation.Language.CodeGeneration]
  $script = $escape::EscapeSingleQuotedStringContent($ScriptPath)
  $log = $escape::EscapeSingleQuotedStringContent($LogPath)
  $command = "& '$script' -Apply -NoSelfElevate *>&1 | Tee-Object -FilePath '$log'; exit `$LASTEXITCODE"
  return @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-Command', $command
  )
}

function Invoke-SelfElevatedApply {
  $hostExe = (Get-Command 'pwsh.exe' -ErrorAction SilentlyContinue).Source
  if (-not $hostExe) { $hostExe = (Get-Command 'powershell.exe' -ErrorAction SilentlyContinue).Source }
  if (-not $hostExe) { throw 'PowerShell was not found for elevation.' }

  $logDir = Join-Path $PSScriptRoot 'logs'
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $logPath = Join-Path $logDir 'firewall-apply.log'
  $cmdArgs = Get-SelfElevatedApplyArguments -ScriptPath $PSCommandPath -LogPath $logPath
  $p = Start-Process -FilePath $hostExe -Verb RunAs -ArgumentList $cmdArgs -Wait -PassThru
  if (Test-Path -LiteralPath $logPath) {
    Get-Content -LiteralPath $logPath | ForEach-Object { Write-Host $_ }
  }
  if ($p -and $null -ne $p.ExitCode) { return $p.ExitCode }
  return 0
}

. (Join-Path $PSScriptRoot 'ai-common.ps1')   # shared Invoke-AiProcess (was inlined below)
function Invoke-ProcessCaptured([string]$FilePath, [string[]]$ArgumentList = @(), [int]$TimeoutSec = 20) {
  return Invoke-AiProcess $FilePath $ArgumentList $TimeoutSec
}

function Add-PortMatch([System.Collections.Generic.List[object]]$Rows, [hashtable]$Rule, [int]$Port) {
  $Rows.Add([pscustomobject]@{
    Port = $Port
    RuleName = [string]$Rule.RuleName
    Profiles = [string]$Rule.Profiles
    RemoteIP = [string]$Rule.RemoteIP
    LocalPort = [string]$Rule.LocalPort
    Program = [string]$Rule.Program
  })
}

function Add-MatchingPorts([System.Collections.Generic.List[object]]$Rows, [hashtable]$Rule, [int[]]$Ports) {
  foreach ($part in (([string]$Rule.LocalPort) -split ',')) {
    $part = $part.Trim()
    if ($part -match '^(\d+)-(\d+)$') {
      $first = [int]$Matches[1]
      $last = [int]$Matches[2]
      foreach ($port in $Ports) {
        if ($port -ge $first -and $port -le $last) { Add-PortMatch $Rows $Rule $port }
      }
    } elseif ($part -match '^\d+$') {
      $port = [int]$part
      if ($port -in $Ports) { Add-PortMatch $Rows $Rule $port }
    }
  }
}

function Get-InboundAllowRows([int[]]$Ports) {
  $netsh = Invoke-ProcessCaptured 'netsh' @('advfirewall', 'firewall', 'show', 'rule', 'name=all', 'dir=in') 20
  if ($netsh.Code -ne 0) { throw $netsh.Text }

  $rows = [System.Collections.Generic.List[object]]::new()
  $current = @{}
  foreach ($line in ($netsh.Text -split "`r?`n")) {
    if ($line -match '^Rule Name:\s*(.+)$') {
      if ($current.Count -gt 0 -and $current.Enabled -eq 'Yes' -and $current.Direction -eq 'In' -and $current.Action -eq 'Allow' -and $current.Protocol -eq 'TCP' -and $current.LocalPort) {
        Add-MatchingPorts $rows $current $Ports
      }
      $current = @{ RuleName = $Matches[1].Trim() }
      continue
    }
    if ($current.Count -gt 0 -and $line -match '^([A-Za-z ]+):\s*(.*)$') {
      $current[$Matches[1].Trim()] = $Matches[2].Trim()
    }
  }
  if ($current.Count -gt 0 -and $current.Enabled -eq 'Yes' -and $current.Direction -eq 'In' -and $current.Action -eq 'Allow' -and $current.Protocol -eq 'TCP' -and $current.LocalPort) {
    Add-MatchingPorts $rows $current $Ports
  }
  return @($rows)
}

function Get-PhysicalAdapterAliases {
  $adapters = @(Get-NetAdapter -Physical -ErrorAction Stop |
    Where-Object { $_.Status -ne 'Disabled' -and $_.Name -notmatch 'Tailscale|Loopback|vEthernet|Docker|WSL' } |
    Select-Object -ExpandProperty Name)
  return @($adapters | Where-Object { $_ } | Sort-Object -Unique)
}

function Test-PortSpecContains([object]$LocalPortSpec, [int]$Port) {
  foreach ($raw in @($LocalPortSpec)) {
    foreach ($part in (([string]$raw) -split ',')) {
      $part = $part.Trim()
      if (-not $part) { continue }
      if ($part -eq 'Any') { return $true }
      if ($part -match '^(\d+)-(\d+)$') {
        if ($Port -ge [int]$Matches[1] -and $Port -le [int]$Matches[2]) { return $true }
      } elseif ($part -match '^\d+$' -and $Port -eq [int]$part) {
        return $true
      }
    }
  }
  return $false
}

function Get-MissingBlockedPorts([object]$Rule, [int[]]$Ports) {
  $filters = @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $Rule -ErrorAction SilentlyContinue)
  if ($filters.Count -eq 0) { return $Ports }

  $missing = @()
  foreach ($port in $Ports) {
    $covered = @($filters | Where-Object {
      $_.Protocol -in @('TCP', 'Any') -and (Test-PortSpecContains $_.LocalPort $port)
    }).Count -gt 0
    if (-not $covered) { $missing += $port }
  }
  return $missing
}

function Get-NetshFirewallRule([string]$RuleName) {
  $result = Invoke-ProcessCaptured 'netsh' @('advfirewall', 'firewall', 'show', 'rule', "name=$RuleName", 'verbose') 20
  if ($result.Code -ne 0 -or $result.Text -match 'No rules match') { return $null }

  $rule = @{}
  foreach ($line in ($result.Text -split "`r?`n")) {
    if ($line -match '^([^:]+):\s*(.*)$') {
      $rule[$Matches[1].Trim()] = $Matches[2].Trim()
    }
  }
  if ($rule.Count -eq 0) { return $null }
  return [pscustomobject]$rule
}

function Repair-LocalAIFirewall {
  if ($PSCmdlet.ShouldProcess('localai firewall posture', 'remove legacy allow rules and block LocalAI ports on physical adapters')) {
    if (-not (Test-Admin)) {
      throw 'Administrator rights are required for -Apply. Re-run this in an elevated PowerShell window.'
    }
    $aliases = Get-PhysicalAdapterAliases
    if ($aliases.Count -eq 0) {
      throw 'No physical network adapters found for the LocalAI block rule.'
    }

    foreach ($ruleId in $LocalAIRuleIds) {
      Get-NetFirewallRule -Name $ruleId -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
    foreach ($displayName in @($LocalAIRuleDisplayNames + $PhysicalBlockRuleName)) {
      Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }

    New-NetFirewallRule `
      -Name $PhysicalBlockRuleId `
      -DisplayName $PhysicalBlockRuleName `
      -Direction Inbound `
      -Action Block `
      -Enabled True `
      -Profile Any `
      -Protocol TCP `
      -LocalPort $LocalAIPorts `
      -InterfaceAlias $aliases |
      Out-Null

    return $aliases
  }
  return @()
}

if ($Apply -and -not (Test-Admin) -and -not $NoSelfElevate) {
  Write-Host 'Requesting administrator approval to repair the LocalAI firewall rule...'
  try {
    exit (Invoke-SelfElevatedApply)
  } catch {
    Line 'FAIL' 'Apply' $_.Exception.Message
    exit 2
  }
}

if ($Apply) {
  try {
    $aliases = @(Repair-LocalAIFirewall)
    if ($aliases.Count -gt 0) {
      Line 'OK' 'Apply' ('removed legacy rules; blocked physical adapters: ' + ($aliases -join ', '))
    } else {
      Line 'WARN' 'Apply' 'no firewall changes were applied'
    }
  } catch {
    Line 'FAIL' 'Apply' $_.Exception.Message
  }
}

Write-Host "==== localai firewall audit ====  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

$blockRule = Get-NetFirewallRule -Name $PhysicalBlockRuleId -ErrorAction SilentlyContinue
$blockRuleSource = ''
$missingBlockedPorts = $LocalAIPorts
if ($blockRule -and $blockRule.Enabled -eq 'True' -and $blockRule.Action -eq 'Block') {
  $blockRuleSource = 'NetSecurity'
  $missingBlockedPorts = @(Get-MissingBlockedPorts $blockRule $LocalAIPorts)
} else {
  $netshBlock = Get-NetshFirewallRule $PhysicalBlockRuleName
  if ($netshBlock -and $netshBlock.Enabled -eq 'Yes' -and $netshBlock.Action -eq 'Block' -and $netshBlock.Protocol -eq 'TCP') {
    $blockRuleSource = 'netsh'
    $missingBlockedPorts = @($LocalAIPorts | Where-Object { -not (Test-PortSpecContains $netshBlock.LocalPort $_) })
  }
}
$blockProtectsPhysical = $false
if ($blockRuleSource) {
  if ($missingBlockedPorts.Count -eq 0) {
    $blockProtectsPhysical = $true
    Line 'OK' 'Physical block' "$PhysicalBlockRuleName is enabled for ports $($LocalAIPorts -join '/')"
  } else {
    Line 'WARN' 'Physical block' "enabled but missing port(s) $($missingBlockedPorts -join ', '); run -Apply"
  }
} else {
  Line 'WARN' 'Physical block' "missing; run -Apply to block LocalAI ports on WiFi/Ethernet"
}

$rows = Get-InboundAllowRows $LocalAIPorts
if ($rows.Count -eq 0) {
  Line 'OK' 'Inbound ports' 'no inbound allow rules for 3000/8888/11434/8080/8880/8188'
} elseif ($blockProtectsPhysical) {
  $ports = @(($rows | Select-Object -ExpandProperty Port -Unique | Sort-Object) | ForEach-Object { [string]$_ })
  Line 'OK' 'Inbound ports' ('third-party allow rules are present but shadowed on WiFi/Ethernet by the LocalAI block: ' + ($ports -join '/'))
} else {
  $groups = $rows | Group-Object Port | Sort-Object Name
  foreach ($g in $groups) {
    $rules = @($g.Group | ForEach-Object { "$($_.RuleName) [$($_.Profiles), remote=$($_.RemoteIP), localport=$($_.LocalPort)]" } | Select-Object -Unique)
    $owned = @($g.Group | Where-Object { $_.RuleName -in $LocalAIRuleDisplayNames })
    if ($owned.Count -gt 0) {
      Line 'WARN' "Port $($g.Name)" (($rules -join '; ') + ' - run -Apply to remove localai-owned rule(s)')
    } else {
      Line 'WARN' "Port $($g.Name)" ($rules -join '; ')
    }
  }
  Line 'WARN' 'Secure access' 'Tailscale Serve does not require inbound allow rules; run -Apply and review third-party rules above'
}

Write-Host ("`nSummary: {0} OK, {1} WARN, {2} FAIL" -f $Ok, $Warn, $Fail)
if ($Fail -gt 0) { exit 2 }
if ($Warn -gt 0) { exit 1 }
exit 0
