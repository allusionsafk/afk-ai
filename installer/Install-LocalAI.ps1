#requires -Version 5.1
<#
  Install-LocalAI.ps1 - AFK AI's guided setup orchestrator.

  Takes a Windows 11 PC to a working local AI matched to its hardware, qualified
  by the product's own readiness check. Chat is published on 127.0.0.1 only. The
  model runtime must listen on all interfaces for the chat container to reach it,
  so setup does not finish until a firewall rule blocking AFK AI's ports on
  physical networks has been applied AND read back (Invoke-PhaseSecure).

  Runs on the Windows PowerShell 5.1 that ships with Windows 11: the native app
  launches it with powershell.exe, so a clean PC needs no PowerShell 7 to set up.

  All product work (runtime configuration, model download, starting services,
  seeding Open WebUI, qualification) is done by AFK AI's OWN engine on AFK AI's
  OWN pinned Python runtime, invoked by path from this installation. Nothing is
  installed into, or resolved from, the PC's own Python.

  Resumable: phase completion is recorded in installer-state.json under the data
  root, so a restart mid-run resumes with -Resume. -Repair re-runs every product
  phase while keeping the hardware tier, intent and model choice; it never
  removes chats, accounts or AFK AI's Docker volumes. -DryRun prints every action
  and changes nothing.

  Usage (the native app does this for you):
    powershell -ExecutionPolicy Bypass -File installer/Install-LocalAI.ps1 -AcceptDefaults
    ... -Resume                                  # continue after a restart
    ... -Repair                                  # re-run product setup steps
    ... -DryRun                                  # print the plan, execute nothing
#>
[CmdletBinding(SupportsShouldProcess)]
param(
  [string]$Intent = '',
  [switch]$AcceptDefaults,
  [switch]$Resume,
  [switch]$Repair,
  [switch]$DryRun,
  [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'AFK LocalAI\State'),
  [switch]$EventStream,
  [string]$LegacyInstallRoot = (Join-Path $env:USERPROFILE 'localai')
)

$ErrorActionPreference = 'Stop'
if ($DryRun) { $WhatIfPreference = $true }

$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
. (Join-Path $RepoRoot 'ai-common.ps1')
. (Join-Path $PSScriptRoot 'installer-common.ps1')
. (Join-Path $PSScriptRoot 'preflight.ps1')
Set-InstallerEventStream -Enabled ([bool]$EventStream)

$DataRoot = [System.IO.Path]::GetFullPath($DataRoot)
# The engine's data root is the folder that holds State, Config, Logs and
# Diagnostics; this script's -DataRoot has always named its State folder.
$ProductDataRoot = Split-Path -Parent $DataRoot
$LegacyInstallRoot = [System.IO.Path]::GetFullPath($LegacyInstallRoot)
$StatePath = Get-InstallerStatePath -Root $DataRoot
$TiersPath = Join-Path $PSScriptRoot 'tiers.json'
$Tiers = Get-Content -LiteralPath $TiersPath -Raw | ConvertFrom-Json
$State = Import-InstallerState -Path $StatePath
$State.legacy_install = [pscustomobject]@{ detected = [bool](Test-Path -LiteralPath $LegacyInstallRoot -PathType Container) }
if (-not $Resume) { $State.pending_reboot = [pscustomobject]@{ required = $false; reason = $null } }

# Product phases a repair re-runs. The machine checks always re-run anyway, and
# the hardware tier and intent are the user's choices, so they are kept.
$RepairPhases = @('runtime', 'scout', 'ollama-docker', 'pulls', 'compose', 'seed', 'secure', 'self-test')
if ($Repair) {
  $State.phases_done = @(@($State.phases_done) | Where-Object { $_ -notin $RepairPhases -and $_ -notin @('python', 'pip') })
}

# Canonical intent ids (audit finding 14: one id, display labels map to it).
$IntentLabels = [ordered]@{
  chat   = 'chat'
  coding = 'coding'
  web    = 'web browsing'
  voice  = 'voice'
}

# ------------------------------------------------------------ AFK AI engine

function Get-OwnedEngine {
  <#
    AFK AI's own interpreter and entry point, by path, from THIS installation.
    There is deliberately no fallback to py.exe, python.exe or PATH: the entry
    point itself also refuses any interpreter that is not this one.
  #>
  $python = Join-Path $RepoRoot 'runtime\python\python.exe'
  $entry = Join-Path $PSScriptRoot 'afk-payload.py'
  foreach ($required in @($python, $entry)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
      throw "AFK AI's own runtime is missing ($required). Reinstall AFK AI to repair it; your chats and settings are kept."
    }
  }
  return [pscustomobject]@{ Python = $python; Entry = $entry }
}

function Get-EngineArguments {
  param([Parameter(Mandatory)][string[]]$Arguments)
  $engine = Get-OwnedEngine
  return , (@('-I', '-B', $engine.Entry, '--program-root', $RepoRoot, '--data-root', $ProductDataRoot) + $Arguments)
}

function Invoke-Engine {
  # Captured run with a bounded timeout. Returns @{ Code; Text }.
  param([Parameter(Mandatory)][string[]]$Arguments, [int]$TimeoutSec = 600)
  $engine = Get-OwnedEngine
  return (Invoke-AiProcess -FilePath $engine.Python -ArgumentList (Get-EngineArguments -Arguments $Arguments) `
      -TimeoutSec $TimeoutSec -WorkingDirectory $RepoRoot)
}

function Invoke-EngineStreaming {
  <#
    Run an engine command and relay every line while it runs, so AFK-EVENT
    progress (model download percentages, service start-up) reaches the native
    app live instead of after the work is over. Returns the exit code and the
    last AFK-STATUS line, if any.
  #>
  param([Parameter(Mandatory)][string[]]$Arguments)
  $engine = Get-OwnedEngine
  $engineArguments = Get-EngineArguments -Arguments $Arguments
  $status = $null
  # Windows PowerShell 5.1 turns a native command's stderr into terminating
  # errors under 'Stop'; the engine's stderr is diagnostic text, not failure.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $engine.Python @engineArguments 2>&1 | ForEach-Object {
      $line = "$_"
      if ($line.StartsWith('AFK-STATUS:')) { $status = $line.Substring(11) }
      [Console]::Out.WriteLine($line)
    }
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previous
  }
  return [pscustomobject]@{ Code = $code; Status = $status }
}

# ------------------------------------------------- environment preflight/gate

function Stop-ForUserAction {
  <#
    The planned "action required" pause.

    Exit 10 is a CONTRACT, not an implementation detail: copies of
    "Install Local AI.cmd" already downloaded from the website route errorlevel
    10 to their friendly pause and treat anything >=11 as "Something went wrong".
    Every planned pause therefore exits 10 and puts the specific instruction on
    screen above it.
  #>
  param(
    [Parameter(Mandatory)][string[]]$Lines,
    [Parameter(Mandatory)][string]$Title,
    $Checkpoint,
    [string]$RebootReason
  )
  Write-Card $Title $Lines
  Write-InstallerEvent -EventType 'checkpoint' -Phase 'environment-preflight' `
    -Status 'action-required' -Code 'checkpoint' -Message ($Lines -join ' ') | ForEach-Object { [Console]::Out.WriteLine($_) }
  if ($Checkpoint) { $State.preflight = $Checkpoint }
  if ($RebootReason) {
    $State.pending_reboot = [pscustomobject]@{ required = $true; reason = $RebootReason }
  }
  try {
    Save-InstallerState -State $State -Path $StatePath
  } catch {
    # A checkpoint we cannot persist must not turn a clear, actionable pause
    # into an unexplained crash - the next run re-probes the machine anyway.
    Write-Host "   (Could not save setup state: $($_.Exception.Message))" -ForegroundColor DarkGray
  }
  exit 10
}

function Invoke-EnvironmentProbe {
  # One live, read-only sweep. Never trusts the persisted checkpoint.
  return (Invoke-EnvironmentPreflight -Evidence (Get-PreflightEvidence))
}

function Assert-EnvironmentReady {
  <#
    The live gate. Called before the product setup work and again immediately
    before the model pull, so a Docker Desktop that stopped mid-install cannot
    be papered over by a phase that was marked done twenty minutes ago.
  #>
  param([string]$Title = 'Environment check')
  if ($DryRun) {
    Write-Host '   [dry-run] would re-check Windows virtualization / Docker readiness' -ForegroundColor DarkGray
    return
  }
  $result = Invoke-EnvironmentProbe
  if (Test-EnvironmentReady -Result $result) {
    Write-Host "   Environment still ready ($($result.Docker.EndpointKind) Docker engine)." -ForegroundColor DarkGray
    return
  }
  Stop-ForUserAction -Title $Title -Lines (Get-PreflightUserMessage -Result $result) `
    -Checkpoint (Get-PreflightCheckpoint -Result $result) `
    -RebootReason $(if ($result.RebootRequired) { 'windows-virtualization-recovery' } else { '' })
}

function Invoke-PhaseEnvironmentPreflight {
  <#
    Phase 0. Classify the LOCAL Windows virtualization / WSL / Docker environment
    before anything expensive happens.

    The one bounded mutation this phase may perform is the Docker Desktop install
    that already existed later in the flow - and only when Docker's absence is
    the sole blocker. Firmware, Windows features, boot configuration and WSL are
    never changed here; those stay instruction-only.
  #>
  [CmdletBinding(SupportsShouldProcess)]
  param()
  Write-Card 'Checking this PC' @(
    'Looking at Windows virtualization, WSL and Docker before downloading anything.')
  if ($DryRun) {
    Write-Host '   [dry-run] would probe Windows virtualization / WSL / Docker (read-only)' -ForegroundColor DarkGray
    return
  }

  $result = Invoke-EnvironmentProbe
  Write-Host "   $((Get-PreflightDiagnosticSummary -Result $result) -join '  ')" -ForegroundColor DarkGray

  if (Test-EnvironmentReady -Result $result) {
    $State.preflight = Get-PreflightCheckpoint -Result $result
    Write-Card 'This PC is ready' @('Windows virtualization and the local Docker engine look healthy.')
    return
  }

  # Bounded recovery: Docker Desktop simply not being installed is the one
  # blocker AFK AI can act on, and only when nothing underneath it is broken.
  $onlyDockerMissing = (
    $result.Code -eq 'PREFLIGHT-DOCKER-NOT-INSTALLED' -and
    $result.Firmware.Status -eq 'READY' -and
    $result.WindowsVirtualization.Status -eq 'READY'
  )
  if ($onlyDockerMissing -and $PSCmdlet.ShouldProcess('Docker Desktop', 'winget install')) {
    Write-Card 'Installing Docker Desktop' @(
      'Docker Desktop is missing but this PC can run it. Installing it now...',
      'FIRST LAUNCH needs you to accept its license and finish setup, and may reboot.')
    [void](Install-WithWinget -Id 'Docker.DockerDesktop' -TimeoutSec 1200)
    # Re-probe live rather than assuming the install worked.
    $result = Invoke-EnvironmentProbe
    if (Test-EnvironmentReady -Result $result) {
      $State.preflight = Get-PreflightCheckpoint -Result $result
      Write-Card 'This PC is ready' @('Docker Desktop is installed and its local engine is healthy.')
      return
    }
    Stop-ForUserAction -Title 'Almost there' -Lines @(
      'Docker Desktop is installed but has not finished its own first-run setup.',
      'Next: open Docker Desktop from the Start menu, accept its terms and let it finish.',
      'If it asks to restart Windows, restart. Then open AFK AI again.',
      'Your answers and setup progress are saved.'
    ) -Checkpoint (Get-PreflightCheckpoint -Result $result) -RebootReason 'docker-desktop-first-run'
  }

  Stop-ForUserAction -Title 'AFK AI needs one thing first' `
    -Lines (Get-PreflightUserMessage -Result $result) `
    -Checkpoint (Get-PreflightCheckpoint -Result $result) `
    -RebootReason $(if ($result.RebootRequired) { 'windows-virtualization-recovery' } else { '' })
}

function Invoke-PhaseEnvironmentReady {
  Write-Card 'Environment gate' @(
    'Confirming the local Docker engine is still healthy before any setup work.')
  Assert-EnvironmentReady -Title 'AFK AI needs one thing first'
}

# ---------------------------------------------------------------- phases

function Invoke-PhaseVet {
  $vram = Get-VetVramGb
  $gpu = Get-VetGpuName
  $ramGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
  $cores = (Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum
  $diskGb = [math]::Round((Get-PSDrive -Name ($RepoRoot.Substring(0, 1))).Free / 1GB, 1)
  $tier = Get-CapabilityTier -Tiers $Tiers -VramGb $vram

  $State.hardware = [pscustomobject]@{
    vram_gb = $vram; ram_gb = $ramGb; disk_free_gb = $diskGb
    cpu_cores = $cores; gpu = $gpu; tier = $tier.id; ctx = $tier.ctx
  }
  $warn = @()
  if ($ramGb -lt 16) { $warn += "RAM ${ramGb} GB < 16: Docker Desktop + WSL2 want 8+, expect pressure" }
  if ($diskGb -lt 40) { $warn += "Disk ${diskGb} GB free < 40: models are 4-20 GB each" }
  if ($tier.id -eq 'CPU') {
    # P1.6: be honest when a real non-NVIDIA dGPU is present - it can't be used
    # (Ollama is CUDA-only), so name it and say CPU-only-and-slow rather than a
    # generic "no NVIDIA VRAM" that reads like a probe failure. Mirrors
    # installer_vet.non_nvidia_gpu_note.
    if ($gpu -and
        $gpu -notmatch '(?i)nvidia|geforce|rtx|gtx|quadro|tesla' -and
        $gpu -notmatch '(?i)microsoft basic|basic display|basic render|remote display|virtual|vmware|citrix|parsec') {
      $warn += "$gpu is not an NVIDIA GPU, which this stack requires - it will run on CPU only, which is much slower."
    } else {
      $warn += 'No NVIDIA VRAM detected: CPU tier, small models only, slow'
    }
  }

  $gpuLabel = if ($gpu) { $gpu } else { 'none detected' }
  $vramLabel = if ($null -ne $vram) { $vram } else { '?' }
  $lines = @(
    "GPU:  $gpuLabel   VRAM: $vramLabel GB",
    "RAM:  ${ramGb} GB   Cores: ${cores}   Free disk: ${diskGb} GB",
    "Tier: $($tier.id)  (context ceiling $($tier.ctx) tokens)"
  ) + @($warn | ForEach-Object { "! $_" })
  Write-Card 'Hardware' $lines
}

function Read-IntentByKeypress {
  # Single-keypress picker: the double-click flow promises "no typing", so the
  # only interactive phase must not ask anyone to type words. Chat is always
  # included; single keys toggle the extras; Enter continues.
  $extras = [ordered]@{ c = 'coding'; w = 'web'; v = 'voice' }
  $on = @{}
  foreach ($k in $extras.Keys) { $on[$k] = $false }
  Write-Card 'What do you want AFK AI for?' @(
    'Chat is always included. Want extras? Press a key to toggle them:',
    '  [C] coding      [W] web browsing      [V] voice',
    'Then press Enter to continue (or just press Enter for chat only).')
  while ($true) {
    $sel = @('chat') + @($extras.Keys | Where-Object { $on[$_] } | ForEach-Object { $extras[$_] })
    Write-Host ("`r   Selected: " + ($sel -join ', ').PadRight(40)) -NoNewline
    $key = [Console]::ReadKey($true)
    if ($key.Key -eq [ConsoleKey]::Enter) { Write-Host ''; return $sel }
    $ch = ([string]$key.KeyChar).ToLowerInvariant()
    if ($extras.Contains($ch)) { $on[$ch] = -not $on[$ch] }
  }
}

function Invoke-PhaseIntent {
  if ([string]::IsNullOrWhiteSpace($Intent)) {
    if ($AcceptDefaults -or [Console]::IsInputRedirected) {
      # No console to read keys from (piped/CI) behaves like -AcceptDefaults.
      $chosen = @('chat')
    } else {
      $chosen = Read-IntentByKeypress
    }
  } else {
    $chosen = $Intent -split '\s*,\s*'
  }
  $chosen = @($chosen | ForEach-Object { "$_".Trim().ToLowerInvariant() } | Where-Object { $_ })
  $valid = @($chosen | Where-Object { $IntentLabels.Contains($_) } | Select-Object -Unique)
  $ignored = @($chosen | Where-Object { -not $IntentLabels.Contains($_) } | Select-Object -Unique)
  if (-not $valid) { $valid = @('chat') }
  $State.intent = $valid
  $lines = @("Selected: $($valid -join ', ')")
  if ($ignored) {
    $lines += "Ignored (not an option): $($ignored -join ', ')   - options are chat, coding, web, voice"
  }
  Write-Card 'Intent' $lines
}

function Invoke-PhaseRuntime {
  <#
    Prove AFK AI's own Python runtime before anything depends on it: isolated
    from the PC's Python, the pinned version, and - for an installed build - the
    exact files Setup shipped. Then create the runtime configuration (and move
    an older setup's service secret out of the program folder).
  #>
  [CmdletBinding(SupportsShouldProcess)]
  param()
  Write-Card 'Preparing AFK AI' @('Checking AFK AI''s own runtime and configuration.')
  if (-not $PSCmdlet.ShouldProcess('AFK AI runtime', 'verify and configure')) { return }
  $info = Invoke-Engine -Arguments @('runtime-info') -TimeoutSec 120
  $facts = $null
  try { $facts = ($info.Text -split "`r?`n" | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1) | ConvertFrom-Json } catch { $facts = $null }
  if (-not $facts -or -not $facts.isolated -or -not $facts.site_disabled -or $facts.pinned_version -ne $facts.python_version) {
    throw "AFK AI's own runtime could not be verified. Reinstall AFK AI to repair it; your chats and settings are kept. ($($info.Text))"
  }
  if ($facts.installation.manifest_present -and -not $facts.installation.intact) {
    throw "AFK AI's program files do not match this version ($($facts.installation.mismatched_count) changed, $($facts.installation.missing_count) missing, $($facts.installation.unexpected_count) unexpected). Reinstall AFK AI; your chats and settings are kept."
  }
  $configure = Invoke-Engine -Arguments @('configure') -TimeoutSec 120
  if ($configure.Code -ne 0) { throw "AFK AI could not write its runtime configuration: $($configure.Text)" }
  Write-Host "   Runtime: CPython $($facts.python_version), isolated." -ForegroundColor DarkGray
}

function Invoke-PhaseScout {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  # Pick the model for the VETTED tier, not a fixed daily-driver: a 4 GB / CPU
  # box must NOT get the 9.5 GB qwen3.5:9b (it would spill or fail to load).
  # tiers.json carries a per-tier `pick` (source + ctx) proven to fit that tier's
  # VRAM (test_each_tier_pick_fits_min_vram).
  $tier = $Tiers.tiers | Where-Object { $_.id -eq $State.hardware.tier }
  $pick = $tier.pick
  $ctxK = [int]($pick.ctx / 1024)
  $tag = "$($pick.source)-${ctxK}k"
  $State.models = [pscustomobject]@{
    chat = [pscustomobject]@{ tag = $tag; source = $pick.source; num_ctx = $pick.ctx }
  }
  if ($PSCmdlet.ShouldProcess($tag, 'record the chat model in runtime configuration')) {
    $configure = Invoke-Engine -Arguments @('configure', '--model', $tag) -TimeoutSec 120
    if ($configure.Code -ne 0) { throw "AFK AI could not record the chat model: $($configure.Text)" }
  }
  Write-Card 'Chat model' @(
    "$tag   (from $($pick.source) with a $($pick.ctx)-token context, tier $($State.hardware.tier))")
}

function Invoke-PhaseOllamaDocker {
  Write-Card 'Model runtime' @('Installing Ollama if needed and applying its local settings.')
  [void](Install-WithWinget -Id 'Ollama.Ollama')
  # Load-bearing: Windows Ollama defaults to 127.0.0.1; Docker reaches it via
  # host.docker.internal, so we must bind 0.0.0.0 and set q8_0 KV (finding 1).
  Set-OllamaHostEnv
  if (@($State.intent) -contains 'web') {
    # WebBrain talks to Ollama directly (OpenAI-style /v1) - no Node, no proxy.
    # Ollama rejects extension origins unless allowlisted (docs/webbrain.md).
    Add-OllamaUserOrigin -Origin $script:WebBrainOrigin
  }

  # Docker readiness is proved by the environment preflight and re-proved by the
  # environment gate, both of which run before this phase. Reaching here with no
  # docker.exe means the machine changed underneath us mid-run, so hand it back
  # to the same live gate rather than starting a second install path.
  if (-not (Get-Command 'docker.exe' -ErrorAction SilentlyContinue)) {
    Assert-EnvironmentReady -Title 'AFK AI needs one thing first'
  }
}

function Invoke-PhasePulls {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  Write-Card 'Downloading the chat model' @(
    "$($State.models.chat.source) - this is the largest download and can take a while.")
  # Last live check before the expensive download. The failure this gate exists
  # to prevent was a multi-GB pull on a machine whose Docker engine could never
  # start; a phase marked done earlier is not evidence of that.
  Assert-EnvironmentReady -Title 'AFK AI needs one thing first'
  if ($PSCmdlet.ShouldProcess('models', 'download, size the context, refresh aliases')) {
    if (-not (Start-OllamaServer)) {
      throw 'Ollama did not become reachable at http://localhost:11434. Start Ollama from the Start menu, then open AFK AI again.'
    }
    $pull = Invoke-EngineStreaming -Arguments @(
      'pull-model', '--source', $State.models.chat.source,
      '--tag', $State.models.chat.tag, '--num-ctx', "$($State.models.chat.num_ctx)")
    if ($pull.Code -ne 0) {
      throw "The chat model $($State.models.chat.source) could not be downloaded (exit $($pull.Code))."
    }
    # A fresh box has only the picked model(s), not a full model zoo, so
    # missing alias sources are skipped rather than fatal (finding 2).
    $aliases = Invoke-Engine -Arguments @('aliases') -TimeoutSec 300
    if ($aliases.Code -ne 0) { Write-Host "   WARN: model aliases were not refreshed: $($aliases.Text)" -ForegroundColor Yellow }
  }
}

function Invoke-PhaseCompose {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  Write-Card 'Starting AFK AI' @(
    'Starting chat services, then checking that chat can really answer.')
  if ($PSCmdlet.ShouldProcess('AFK AI services', 'start and qualify')) {
    $start = Invoke-EngineStreaming -Arguments @('start')
    if ($start.Code -ne 0) {
      $reason = 'unknown'
      try { $reason = ($start.Status | ConvertFrom-Json).message } catch { }
      throw "AFK AI started but is not ready: $reason"
    }
  }
}

function Invoke-PhaseSeed {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  Write-Card 'Tuning chat for this model' @("Applying $($State.models.chat.tag) defaults in Open WebUI.")
  if ($PSCmdlet.ShouldProcess('open-webui DB', 'seed chat defaults')) {
    $seed = Invoke-Engine -Arguments @(
      'seed-webui', '--model', $State.models.chat.tag,
      '--num-ctx', "$($State.models.chat.num_ctx)") -TimeoutSec 120
    if ($seed.Code -ne 0) {
      # Chat still works without these defaults; say so rather than failing setup.
      Write-Host "   WARN: chat defaults were not applied ($($seed.Text))." -ForegroundColor Yellow
    }
  }
}

function Invoke-PhaseSecure {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  Write-Card 'Secure by default' @(
    'The model runtime listens on this PC''s network interfaces so the chat container can reach it.',
    'Blocking AFK AI''s ports on physical networks - Windows will ask for administrator approval.')
  if ($PSCmdlet.ShouldProcess('firewall', 'ai-firewall -Apply, then verify the AFK AI block rule')) {
    # Runs on THIS PowerShell (inbox Windows PowerShell 5.1 when the app runs
    # setup), never on an optional PowerShell 7. ai-firewall elevates itself.
    $fw = Invoke-AiProcess -FilePath (Get-Process -Id $PID).Path -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $RepoRoot 'ai-firewall.ps1'), '-Apply') -TimeoutSec 300 -WorkingDirectory $RepoRoot
    # The exit code is not the evidence (a declined prompt, a timeout or a
    # partial apply can all look alike). Read the rule back from Windows.
    $adapters = @()
    try { $adapters = @(Get-AfkPhysicalAdapterAliases) } catch { $adapters = @() }
    $problems = @(Get-AfkFirewallRuleProblems -Rule (Get-AfkFirewallRuleEvidence) -PhysicalAdapters $adapters)
    if ($problems.Count) {
      throw ("AFK AI could not confirm the Windows Firewall rule that keeps its model runtime (port 11434) " +
        "and chat ports off your local network: $($problems -join '; '). Until it is in place, other devices " +
        "on your network may be able to reach the model runtime. Choose Repair and approve the Windows " +
        "administrator prompt. (firewall tool exit $($fw.Code))")
    }
    Write-Host '   Firewall: AFK AI ports are blocked on physical networks (rule verified).' -ForegroundColor DarkGray
  }
  # WinNAT's dynamic port pool sometimes reserves 3000 after a reboot, which
  # blocks Docker's 127.0.0.1:3000 publish. Detect it and explain the fix (it
  # needs an Administrator shell, so we do not attempt it silently here).
  $reserved = (Invoke-AiProcess -FilePath 'netsh' -ArgumentList @(
      'int', 'ipv4', 'show', 'excludedportrange', 'protocol=tcp') -TimeoutSec 20).Text
  $port3000Reserved = $false
  foreach ($line in ($reserved -split "`r?`n")) {
    if ($line -match '^\s*(\d+)\s+(\d+)' -and 3000 -ge [int]$Matches[1] -and 3000 -le [int]$Matches[2]) {
      $port3000Reserved = $true
      break
    }
  }
  if ($port3000Reserved) {
    Write-Card 'Port 3000 is reserved by Windows' @(
      'Windows (WinNAT) has reserved port 3000, which the chat UI needs.',
      'Fix it from an Administrator PowerShell, then open AFK AI again:',
      '  net stop winnat',
      '  netsh int ipv4 add excludedportrange protocol=tcp startport=3000 numberofports=1',
      '  net start winnat')
  }
}

function Invoke-PhaseSelfTest {
  Write-Card 'Final check' @('Asking AFK AI whether chat is really usable.')
  if ($DryRun) {
    Write-Host '   [dry-run] would run: status --json' -ForegroundColor DarkGray
    return
  }
  $check = Invoke-Engine -Arguments @('status', '--json') -TimeoutSec 300
  $status = $null
  try { $status = ($check.Text -split "`r?`n" | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1) | ConvertFrom-Json } catch { $status = $null }
  if (-not $status -or $status.state -ne 'Ready') {
    $message = if ($status) { $status.message } else { $check.Text }
    Write-Host "AFK AI is not ready yet: $message" -ForegroundColor Red
    # Exit non-zero BEFORE the runner marks this phase done: a failed
    # self-test must neither print "Finished" nor be skipped on the retry.
    exit 1
  }
  $ready = @('Chat is ready. Open AFK AI and choose Open Chat.')
  if ($status.chat.onboarding_required) { $ready += 'The first account you create in chat becomes the owner of this PC''s chat.' }
  $ready += 'Security: chat is served on this PC only; AFK AI ports are blocked on physical networks; no autostart.'
  if (@($State.intent) -contains 'web') {
    $ready += 'Browser agent: install WebBrain from the Chrome Web Store, set its server URL'
    $ready += '  to http://localhost:11434, and keep the Chrome window visible during tasks.'
  }
  Write-Card 'AFK AI is ready' $ready
}

# ------------------------------------------------------- phase runner

# Phase order. The two environment phases come first and are never skipped on
# resume (Test-PhaseDone refuses to report them done), because the invariant this
# flow exists to hold is:
#
#   no model pull and no product setup begins until a LIVE gate has proved a
#   usable local Docker path.
#
# See docs/design/virtualization-docker-preflight.md.
$Phases = @(
  @{ Name = 'environment-preflight'; Run = { Invoke-PhaseEnvironmentPreflight } }
  @{ Name = 'environment-ready'; Run = { Invoke-PhaseEnvironmentReady } }
  @{ Name = 'vet';      Run = { Invoke-PhaseVet } }
  @{ Name = 'intent';   Run = { Invoke-PhaseIntent } }
  @{ Name = 'runtime';  Run = { Invoke-PhaseRuntime } }
  @{ Name = 'scout';    Run = { Invoke-PhaseScout } }
  @{ Name = 'ollama-docker'; Run = { Invoke-PhaseOllamaDocker } }
  @{ Name = 'pulls';    Run = { Invoke-PhasePulls } }
  @{ Name = 'compose';  Run = { Invoke-PhaseCompose } }
  @{ Name = 'seed';     Run = { Invoke-PhaseSeed } }
  @{ Name = 'secure';   Run = { Invoke-PhaseSecure } }
  @{ Name = 'self-test'; Run = { Invoke-PhaseSelfTest } }
)

# Ollama and Docker Desktop install via winget. A missing winget used to surface
# as ignored warnings followed by a dead-end throw. Fail fast, once, with the real
# fix - unless every tool winget would install is already present.
if (-not $DryRun -and -not (Get-Command 'winget.exe' -ErrorAction SilentlyContinue)) {
  Update-SessionPath
  $missing = @()
  if (-not (Get-Command 'ollama.exe' -ErrorAction SilentlyContinue)) { $missing += 'Ollama' }
  if (-not (Get-Command 'docker.exe' -ErrorAction SilentlyContinue)) { $missing += 'Docker Desktop' }
  if ($missing.Count) {
    throw @"
winget is not available on this PC, and setup needs it to install: $($missing -join ', ').
winget ships with Microsoft's App Installer - get it from https://aka.ms/getwinget
(Windows Sandbox and LTSC editions do not include it by default).
Or install the missing tools manually, then open AFK AI again.
"@
  }
}

Write-Card 'AFK AI setup' @(
  $(if ($DryRun) { 'DRY RUN - nothing will be changed.' } elseif ($Repair) { 'Repair: re-running product setup. Your chats and settings are kept.' } else { 'Setting up AFK AI on this PC.' }),
  'Chat is served on this PC only. Setup blocks AFK AI ports on your local network and stops if it cannot. No autostart.')

foreach ($phase in $Phases) {
  if (Test-PhaseDone -State $State -Phase $phase.Name) {
    Write-Host "-- skip $($phase.Name) (already done)" -ForegroundColor DarkGray
    continue
  }
  # A planned pause exits from inside the phase via Stop-ForUserAction; anything
  # that returns here completed, so record it. Safety-critical phases are never
  # recorded (Set-PhaseDone drops them) and so always re-run.
  Write-InstallerEvent -EventType 'phase-start' -Phase $phase.Name -Status 'running' `
    -Code 'phase-start' -Message "Starting $($phase.Name)."
  try {
    & $phase.Run | Out-Null
    Set-PhaseDone -State $State -Phase $phase.Name -Path $StatePath
    Write-InstallerEvent -EventType 'phase-success' -Phase $phase.Name -Status 'success' `
      -Code 'phase-success' -Message "Completed $($phase.Name)."
  } catch {
    Write-InstallerEvent -EventType 'phase-failure' -Phase $phase.Name -Status 'failure' `
      -Code 'phase-failure' -Message $_.Exception.Message
    throw
  }
}

Save-InstallerState -State $State -Path $StatePath
