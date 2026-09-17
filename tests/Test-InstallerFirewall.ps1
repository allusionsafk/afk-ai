#requires -Version 5.1
<#
  tests/Test-InstallerFirewall.ps1 - the firewall rule is part of setup's security
  invariant, and setup must prove it on Windows PowerShell 5.1.

  Independent review of PR #24 reproduced: setup binds Ollama to 0.0.0.0, runs on
  inbox Windows PowerShell 5.1, and skipped the firewall step whenever pwsh.exe
  was absent - then told the user the services were "loopback-only". These cases
  pin the corrected behaviour:

  - the firewall tool and setup's scripts parse on Windows PowerShell 5.1;
  - the rule is judged from what Windows reports, field by field;
  - the secure phase runs the tool on the current (5.1) host, reads the rule
    back, and fails when it cannot be verified - whatever the tool's exit code;
  - no unverified "loopback-only" / "no LAN exposure" claim remains.

  Fixture-only: nothing here reads or changes this PC's firewall.
  Invoke-Checks runs this file with powershell.exe, not pwsh.

  Exit code 0 = all cases passed; 1 = at least one failed.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$script:Pass = 0
$script:Fail = 0
$script:Failures = New-Object System.Collections.Generic.List[string]

function Assert-True {
  param([Parameter(Mandatory)][string]$Case, $Condition, [string]$Detail = '')
  if ([bool]$Condition) { $script:Pass++; return }
  $script:Fail++
  if ($Detail) { $script:Failures.Add("$Case`n        $Detail") } else { $script:Failures.Add($Case) }
}

function Read-Text([string]$Relative) {
  return [IO.File]::ReadAllText((Join-Path $Root $Relative))
}

Assert-True 'this suite runs on Windows PowerShell 5.1 (Desktop), not PowerShell 7' (
  $PSVersionTable.PSEdition -eq 'Desktop' -and $PSVersionTable.PSVersion.Major -eq 5) "edition $($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion)"

# --------------------------------------------------------------- 5.1 parse
Write-Host '-- Windows PowerShell 5.1 parse' -ForegroundColor Cyan
foreach ($relative in @('ai-firewall.ps1', 'ai-common.ps1', 'installer/installer-common.ps1', 'installer/Install-LocalAI.ps1')) {
  $tokens = $null
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Root $relative), [ref]$tokens, [ref]$errors)
  Assert-True "$relative parses on Windows PowerShell 5.1" (-not $errors) (($errors | ForEach-Object { $_.Message }) -join '; ')
}
$firewallText = Read-Text 'ai-firewall.ps1'
Assert-True 'ai-firewall.ps1 no longer requires PowerShell 7' ($firewallText -match '^#requires -Version 5\.1\r?\n')

. (Join-Path $Root 'ai-common.ps1')
. (Join-Path $Root 'installer/installer-common.ps1')

# ------------------------------------------------ rule values mirror the tool
Write-Host '-- rule identity mirrors ai-firewall.ps1' -ForegroundColor Cyan
$toolRuleId = [regex]::Match($firewallText, "(?m)^\`$PhysicalBlockRuleId = '([^']+)'").Groups[1].Value
$toolPorts = [regex]::Match($firewallText, '(?m)^\$LocalAIPorts = @\(([^)]+)\)').Groups[1].Value -split '\s*,\s*' | ForEach-Object { [int]$_ }
Assert-True 'rule id matches the tool' ($toolRuleId -and $toolRuleId -eq $script:AfkBlockRuleId) "tool '$toolRuleId' vs '$script:AfkBlockRuleId'"
Assert-True 'blocked ports match the tool' ((@($toolPorts) -join ',') -eq (@($script:AfkBlockedPorts) -join ',')) "tool '$(@($toolPorts) -join ',')'"
Assert-True 'blocked ports include Ollama 11434' (@($script:AfkBlockedPorts) -contains 11434)
Assert-True 'adapter filter matches the tool' ($firewallText.Contains("-notmatch '$script:AfkNonPhysicalAdapterPattern'"))

# ------------------------------------------------------- rule validation
Write-Host '-- rule validation' -ForegroundColor Cyan
$adapters = @('Ethernet', 'Wi-Fi')
function New-Rule([hashtable]$Override = @{}) {
  $rule = [ordered]@{
    Enabled = 'True'; Direction = 'Inbound'; Action = 'Block'; Protocol = 'TCP'
    LocalPort = @('3000', '8888', '11434', '8080', '8880', '8188')
    InterfaceAlias = @('Ethernet', 'Wi-Fi'); RemoteAddress = @('Any'); Program = 'Any'
  }
  foreach ($key in $Override.Keys) { $rule[$key] = $Override[$key] }
  return [pscustomobject]$rule
}
function Get-Problems($Rule, [string[]]$Physical = $adapters) {
  return @(Get-AfkFirewallRuleProblems -Rule $Rule -PhysicalAdapters $Physical)
}

$ok = Get-Problems (New-Rule)
Assert-True 'a conforming rule has no problems' ($ok.Count -eq 0) ($ok -join '; ')
Assert-True 'a single port range covering every port is accepted' ((Get-Problems (New-Rule @{ LocalPort = @('3000-12000') })).Count -eq 0)
Assert-True 'Protocol Any is accepted' ((Get-Problems (New-Rule @{ Protocol = 'Any' })).Count -eq 0)

$cases = [ordered]@{
  'a missing rule'                        = @{ Rule = $null; Expect = 'does not exist' }
  'a disabled rule'                       = @{ Rule = (New-Rule @{ Enabled = 'False' }); Expect = 'not enabled' }
  'an outbound rule'                      = @{ Rule = (New-Rule @{ Direction = 'Outbound' }); Expect = 'not inbound' }
  'an allow rule'                         = @{ Rule = (New-Rule @{ Action = 'Allow' }); Expect = 'does not block' }
  'a UDP rule'                            = @{ Rule = (New-Rule @{ Protocol = 'UDP' }); Expect = 'TCP' }
  'a rule without Ollama 11434'           = @{ Rule = (New-Rule @{ LocalPort = @('3000', '8888', '8080', '8880', '8188') }); Expect = '11434' }
  'a rule with no ports'                  = @{ Rule = (New-Rule @{ LocalPort = @() }); Expect = '11434' }
  'a rule limited to some remote hosts'   = @{ Rule = (New-Rule @{ RemoteAddress = @('10.0.0.0/255.0.0.0') }); Expect = 'remote addresses' }
  'a rule limited to one program'         = @{ Rule = (New-Rule @{ Program = 'C:\Tools\other.exe' }); Expect = 'one program' }
  'a rule on every interface'             = @{ Rule = (New-Rule @{ InterfaceAlias = @('Any') }); Expect = 'not scoped to physical' }
  'a rule with no interface scope'        = @{ Rule = (New-Rule @{ InterfaceAlias = @() }); Expect = 'not scoped to physical' }
  'a rule that also covers WSL'           = @{ Rule = (New-Rule @{ InterfaceAlias = @('Ethernet', 'Wi-Fi', 'vEthernet (WSL)') }); Expect = 'virtual adapter' }
  'a rule missing a physical adapter'     = @{ Rule = (New-Rule @{ InterfaceAlias = @('Ethernet') }); Expect = 'physical network adapter' }
}
foreach ($case in $cases.GetEnumerator()) {
  $problems = Get-Problems $case.Value.Rule
  Assert-True "$($case.Key) is not verified" ($problems.Count -gt 0 -and ($problems -join '; ') -match [regex]::Escape($case.Value.Expect)) ($problems -join '; ')
}
$noAdapters = Get-Problems (New-Rule) @()
Assert-True 'no physical adapters to check against is not verified' ($noAdapters.Count -gt 0)

# ------------------------------------------------------------ the secure phase
Write-Host '-- secure phase (extracted from Install-LocalAI.ps1)' -ForegroundColor Cyan
$installer = Join-Path $Root 'installer/Install-LocalAI.ps1'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$null, [ref]$null)
$secureAst = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-PhaseSecure' }, $true)
Assert-True 'Invoke-PhaseSecure exists' $secureAst
. ([scriptblock]::Create($secureAst.Extent.Text))

$RepoRoot = $Root
$script:Calls = New-Object System.Collections.Generic.List[object]
$script:ToolExit = 0
$script:RuleFixture = $null
function Write-Card { param($Title, $Lines) $script:Cards += @($Lines) }
function Write-Host { param([Parameter(ValueFromRemainingArguments)]$Rest) $script:Printed += @("$($Rest | Select-Object -First 1)") }
function Invoke-AiProcess {
  param([string]$FilePath, [string[]]$ArgumentList, [int]$TimeoutSec, [string]$WorkingDirectory)
  $script:Calls.Add([pscustomobject]@{ FilePath = $FilePath; ArgumentList = @($ArgumentList) })
  if ($FilePath -eq 'netsh') { return [pscustomobject]@{ Code = 0; Text = '' } }
  return [pscustomobject]@{ Code = $script:ToolExit; Text = '' }
}
function Get-AfkFirewallRuleEvidence { return $script:RuleFixture }
function Get-AfkPhysicalAdapterAliases { return @('Ethernet', 'Wi-Fi') }

function Invoke-Secure([int]$ToolExit, $Rule, [switch]$WhatIf) {
  $script:Calls.Clear()
  $script:Cards = @()
  $script:Printed = @()
  $script:ToolExit = $ToolExit
  $script:RuleFixture = $Rule
  try {
    if ($WhatIf) { Invoke-PhaseSecure -WhatIf } else { Invoke-PhaseSecure }
    return $null
  } catch {
    return $_.Exception.Message
  }
}

$missing = Invoke-Secure -ToolExit 0 -Rule $null
Assert-True 'a missing rule fails setup even when the tool exits 0' ($missing) 'no failure'
Assert-True 'the failure names the exposed model runtime port' ("$missing" -match '11434')
Assert-True 'the failure states the exposure plainly' ("$missing" -match 'may be able to reach the model runtime')
Assert-True 'a missing rule is never reported as verified' (-not (@($script:Printed) -match 'verified'))

$declined = Invoke-Secure -ToolExit 2 -Rule $null
Assert-True 'a declined or failed apply fails setup' ($declined)
$disabled = Invoke-Secure -ToolExit 0 -Rule (New-Rule @{ Enabled = 'False' })
Assert-True 'a rule that exists but is disabled fails setup' ("$disabled" -match 'not enabled')

$verified = Invoke-Secure -ToolExit 1 -Rule (New-Rule)
Assert-True 'a verified rule lets setup continue' ($null -eq $verified) "$verified"
Assert-True 'a verified rule is reported as verified' (@($script:Printed) -match 'rule verified')
$toolCall = @($script:Calls | Where-Object { @($_.ArgumentList) -contains '-Apply' }) | Select-Object -First 1
Assert-True 'the firewall tool is applied' ($toolCall -and (@($toolCall.ArgumentList) -match 'ai-firewall\.ps1$'))
Assert-True 'the firewall tool runs on the current PowerShell host, not pwsh' (
  $toolCall -and $toolCall.FilePath -eq (Get-Process -Id $PID).Path -and $toolCall.FilePath -notmatch 'pwsh') "$($toolCall.FilePath)"

$dryRun = Invoke-Secure -ToolExit 0 -Rule $null -WhatIf
Assert-True 'a dry run changes no firewall and claims nothing' ($null -eq $dryRun -and -not @($script:Calls | Where-Object { @($_.ArgumentList) -contains '-Apply' }).Count)

# ------------------------------------------------------------- false copy
Write-Host '-- no unverified security claims' -ForegroundColor Cyan
$setupText = [IO.File]::ReadAllText($installer)
foreach ($claim in @('loopback[- ]only', 'no LAN exposure', 'bind to loopback', 'firewall hardening skipped', 'pwsh\.exe')) {
  Assert-True "setup does not say '$claim'" ($setupText -notmatch "(?i)$claim")
}
Assert-True 'the secure phase fails on unverified evidence' (
  $secureAst.Extent.Text -match 'Get-AfkFirewallRuleProblems' -and $secureAst.Extent.Text -match '\bthrow\b')

Write-Host ''
if ($script:Fail -gt 0) {
  Write-Host "FAILURES ($script:Fail):" -ForegroundColor Red
  foreach ($failure in $script:Failures) { Microsoft.PowerShell.Utility\Write-Host "  - $failure" -ForegroundColor Red }
  Microsoft.PowerShell.Utility\Write-Host "FIREWALL TESTS FAILED: $script:Pass passed, $script:Fail failed."
  exit 1
}
Microsoft.PowerShell.Utility\Write-Host "FIREWALL TESTS PASSED: $script:Pass passed, 0 failed."
exit 0
