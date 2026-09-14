#requires -Version 7.0
<#
  Install-LocalAI.ps1 - the guided "Friend Bootstrapper" orchestrator.

  Takes a clean-ish Windows box to a working, LOOPBACK-ONLY localai stack matched
  to its hardware and intent, with a passing self-test and zero network exposure
  unless explicitly opted in. It WRAPS this repo's existing scripts/compose/
  Modelfiles - it does not reimplement them.

  Resumable: phase completion is recorded in installer-state.json so a
  Docker-Desktop reboot mid-run can be resumed with -Resume. -DryRun prints every
  action and changes nothing. See installer/README.md for the design.

  Usage:
    pwsh -ExecutionPolicy Bypass -File installer/Install-LocalAI.ps1
    ... -Intent chat,web -AcceptDefaults        # non-interactive
    ... -DryRun                                  # print the plan, execute nothing
    ... -Resume                                  # continue after a reboot
#>
[CmdletBinding(SupportsShouldProcess)]
param(
  [string]$Intent = '',
  [switch]$AcceptDefaults,
  [switch]$Resume,
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
$LegacyInstallRoot = [System.IO.Path]::GetFullPath($LegacyInstallRoot)
$StatePath = Get-InstallerStatePath -Root $DataRoot
$TiersPath = Join-Path $PSScriptRoot 'tiers.json'
$Tiers = Get-Content -LiteralPath $TiersPath -Raw | ConvertFrom-Json
$State = Import-InstallerState -Path $StatePath
$State.legacy_install = [pscustomobject]@{ detected = [bool](Test-Path -LiteralPath $LegacyInstallRoot -PathType Container) }
if (-not $Resume) { $State.pending_reboot = [pscustomobject]@{ required = $false; reason = $null } }

# Canonical intent ids (audit finding 14: one id, display labels map to it).
$IntentLabels = [ordered]@{
  chat   = 'chat'
  coding = 'coding'
  web    = 'web browsing'
  voice  = 'voice'
}

function Test-PythonCandidate {
  # A candidate must actually run AND be >=3.12 before we trust it. Guards
  # against the Microsoft Store python.exe alias stub, stale py launchers that
  # don't know 3.12 (they exit with 'Python 3.12 not found' / 103), and old
  # pythons shadowing a fresh install (clean-VM finding, v0.1.4).
  param([Parameter(Mandatory)][string[]]$Candidate)
  $rest = Get-PyRest -Py $Candidate
  $probe = Invoke-AiProcess -FilePath $Candidate[0] `
    -ArgumentList ($rest + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)')) -TimeoutSec 20
  return ($probe.Code -eq 0)
}

function Resolve-Python {
  # Prefer the py launcher pinned to 3.12, else a real python on PATH, else
  # winget's default per-user install dir (PATH can be stale even after
  # Update-SessionPath). Every candidate is probed before use; only a probed
  # success is cached.
  # Unary commas below: a single-element candidate like @('...\python.exe')
  # otherwise unrolls to a bare string on return, and $py[0] then indexes the
  # STRING - the installer literally tried to start a process named 'C'
  # (clean-VM bug, v0.1.5).
  if ($script:ResolvedPython) { return , $script:ResolvedPython }
  Update-SessionPath
  $candidates = @()
  $py = Get-Command 'py.exe' -ErrorAction SilentlyContinue
  if ($py) { $candidates += , @($py.Source, '-3.12') }
  $python = Get-Command 'python.exe' -ErrorAction SilentlyContinue
  if ($python -and $python.Source -notmatch '\\WindowsApps\\') {
    # WindowsApps python.exe is the Store redirect stub, not an interpreter.
    $candidates += , @($python.Source)
  }
  $direct = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
  if (Test-Path -LiteralPath $direct) { $candidates += , @($direct) }
  foreach ($candidate in $candidates) {
    if (Test-PythonCandidate -Candidate $candidate) {
      $script:ResolvedPython = @($candidate)
      return , $script:ResolvedPython
    }
  }
  return $null
}

function Get-PyRest {
  # The launcher args after the executable (e.g. '-3.12'), or @() for python.exe.
  # The unary comma prevents PowerShell from unrolling a single-element array
  # into a bare string on return - without it, '-3.12' + @('-m','pip') string-
  # concatenates into the single mangled argument '-3.12-m pip' (clean-VM bug,
  # v0.1.5).
  param([Parameter(Mandatory)][string[]]$Py)
  if ($Py.Count -gt 1) { return , @($Py[1..($Py.Count - 1)]) }
  return , @()
}

function Invoke-Localai {
  # Run `python -m localai <args>` from the repo, streaming to the console.
  param([Parameter(Mandatory)][string[]]$Arguments, [int]$TimeoutSec = 600)
  $py = Resolve-Python
  if (-not $py) { throw 'Python not found; run the Prerequisites phase first.' }
  $full = (Get-PyRest -Py $py) + @('-m', 'localai') + $Arguments
  return (Invoke-AiProcess -FilePath $py[0] -ArgumentList $full -TimeoutSec $TimeoutSec -WorkingDirectory $RepoRoot)
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
    -Status 'action-required' -Code 'checkpoint' -Message ($Lines -join ' ')
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
    The live gate. Called before the Python/model work and again immediately
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
  Write-Card 'Phase 0 - Checking this PC' @(
    'Looking at Windows virtualization, WSL and Docker before downloading anything.')
  if ($DryRun) {
    Write-Host '   [dry-run] would probe Windows virtualization / WSL / Docker (read-only)' -ForegroundColor DarkGray
    return
  }

  $result = Invoke-EnvironmentProbe
  Write-Host "   $((Get-PreflightDiagnosticSummary -Result $result) -join '  ')" -ForegroundColor DarkGray

  if (Test-EnvironmentReady -Result $result) {
    $State.preflight = Get-PreflightCheckpoint -Result $result
    Write-Card 'Phase 0 - Ready' @('Windows virtualization and the local Docker engine look healthy.')
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
    Write-Card 'Phase 0 - Docker Desktop' @(
      'Docker Desktop is missing but this PC can run it. Installing it now...',
      'FIRST LAUNCH needs you to accept its license and finish setup, and may reboot.')
    [void](Install-WithWinget -Id 'Docker.DockerDesktop' -TimeoutSec 1200)
    # Re-probe live rather than assuming the install worked.
    $result = Invoke-EnvironmentProbe
    if (Test-EnvironmentReady -Result $result) {
      $State.preflight = Get-PreflightCheckpoint -Result $result
      Write-Card 'Phase 0 - Ready' @('Docker Desktop is installed and its local engine is healthy.')
      return
    }
    Stop-ForUserAction -Title 'Almost there' -Lines @(
      'Docker Desktop is installed but has not finished its own first-run setup.',
      'Next: open Docker Desktop from the Start menu, accept its terms and let it finish.',
      'If it asks to restart Windows, restart. Then run the AFK AI installer again.',
      'Your answers and setup progress are saved.'
    ) -Checkpoint (Get-PreflightCheckpoint -Result $result) -RebootReason 'docker-desktop-first-run'
  }

  Stop-ForUserAction -Title 'AFK AI needs one thing first' `
    -Lines (Get-PreflightUserMessage -Result $result) `
    -Checkpoint (Get-PreflightCheckpoint -Result $result) `
    -RebootReason $(if ($result.RebootRequired) { 'windows-virtualization-recovery' } else { '' })
}

function Invoke-PhaseEnvironmentReady {
  Write-Card 'Phase 0b - Environment gate' @(
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

  $lines = @(
    "GPU:  $($gpu ?? 'none detected')   VRAM: $($vram ?? '?') GB",
    "RAM:  ${ramGb} GB   Cores: ${cores}   Free disk: ${diskGb} GB",
    "Tier: $($tier.id)  (context ceiling $($tier.ctx) tokens)"
  ) + @($warn | ForEach-Object { "! $_" })
  Write-Card 'Phase 1 - Capability vet' $lines
}

function Read-IntentByKeypress {
  # Single-keypress picker: the double-click flow promises "no typing", so the
  # only interactive phase must not ask anyone to type words. Chat is always
  # included; single keys toggle the extras; Enter continues.
  $extras = [ordered]@{ c = 'coding'; w = 'web'; v = 'voice' }
  $on = @{}
  foreach ($k in $extras.Keys) { $on[$k] = $false }
  Write-Card 'Phase 2 - What do you want localai for?' @(
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
  Write-Card 'Phase 2 - Intent' $lines
}

function Invoke-PhasePython {
  Write-Card 'Phase 3a - Python 3.12' @('winget install Python.Python.3.12')
  [void](Install-WithWinget -Id 'Python.Python.3.12')
}

function Invoke-PhasePip {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  Write-Card 'Phase 3b - Install localai (editable)' @('py -3.12 -m pip install -e .[windows]')
  if ($PSCmdlet.ShouldProcess('.[windows]', 'pip install -e')) {
    $py = Resolve-Python
    if (-not $py) {
      throw 'No working Python >=3.12 found (checked py -3.12, python.exe outside WindowsApps, and the winget default dir). Close this window and run "Install Local AI.cmd" again - a fresh window picks up the new PATH.'
    }
    Write-Host "   using interpreter: $($py -join ' ')" -ForegroundColor DarkGray
    $pipArgs = (Get-PyRest -Py $py) + @('-m', 'pip', 'install', '-e', '.[windows]')
    $r = Invoke-AiProcess -FilePath $py[0] -ArgumentList $pipArgs -TimeoutSec 900 -WorkingDirectory $RepoRoot
    if ($r.Code -ne 0) {
      $tail = (($r.Text -split "`r?`n") | Select-Object -Last 6) -join "`n"
      throw "pip install failed (exit $($r.Code)) using '$($py -join ' ')':`n$tail"
    }
  }
}

function Invoke-PhaseScout {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  $budget = $State.hardware.vram_gb
  $budgetLabel = if ($null -ne $budget) { "$budget GB VRAM budget" } else { 'no VRAM (CPU-only)' }
  Write-Card 'Phase 4 - Choose models' @(
    "Running the scout at the vetted $budgetLabel, tier $($State.hardware.tier)")
  if ($PSCmdlet.ShouldProcess('scout', 'run localai scout')) {
    $scoutArgs = @('scout')
    if ($null -ne $budget) { $scoutArgs += @('--vram-gb', "$budget") }
    $out = Invoke-Localai -Arguments $scoutArgs -TimeoutSec 300
    Write-Host $out.Text
  }
  # Pick the model for the VETTED tier, not a fixed daily-driver: a 4 GB / CPU
  # box must NOT get the 9.5 GB qwen3.5:9b (it would spill or fail to load).
  # tiers.json carries a per-tier `pick` (source + ctx) proven to fit that tier's
  # VRAM (test_each_tier_pick_fits_min_vram). Scout output above informs a manual
  # override; parsing scout's grouped picks into state is a follow-up.
  $tier = $Tiers.tiers | Where-Object { $_.id -eq $State.hardware.tier }
  $pick = $tier.pick
  $ctxK = [int]($pick.ctx / 1024)
  $tag = "$($pick.source)-${ctxK}k"
  $State.models = [pscustomobject]@{
    chat = [pscustomobject]@{ tag = $tag; source = $pick.source; num_ctx = $pick.ctx }
  }
  Write-Card 'Phase 4 - Picks' @(
    "chat -> $tag   (source $($pick.source) @ $($pick.ctx) ctx, tier $($State.hardware.tier))")
}

function Invoke-PhaseOllamaDocker {
  Write-Card 'Phase 5a - Ollama, Docker' @('winget install Ollama.Ollama; set Ollama host env')
  [void](Install-WithWinget -Id 'Ollama.Ollama')
  # Load-bearing: Windows Ollama defaults to 127.0.0.1; Docker reaches it via
  # host.docker.internal, so we must bind 0.0.0.0 and set q8_0 KV (finding 1).
  Set-OllamaHostEnv
  if (@($State.intent) -contains 'web') {
    # WebBrain talks to Ollama directly (OpenAI-style /v1) - no Node, no proxy.
    # Ollama rejects extension origins unless allowlisted (docs/webbrain.md).
    Add-OllamaUserOrigin -Origin $script:WebBrainOrigin
  }

  # Docker readiness is now proved by the Phase 0 preflight and re-proved by the
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
  Write-Card 'Phase 5b - Pull models + aliases' @(
    'ollama pull picks; build Modelfiles; localai model-aliases (needs Ollama running)')
  # Last live check before the expensive download. The Friend Beta failure this
  # unit exists to prevent was a multi-GB pull on a machine whose Docker engine
  # could never start; a phase marked done earlier is not evidence of that.
  Assert-EnvironmentReady -Title 'AFK AI needs one thing first'
  if ($PSCmdlet.ShouldProcess('models', 'ollama pull + create + aliases')) {
    $ollama = Get-Command 'ollama.exe' -ErrorAction SilentlyContinue
    if (-not $ollama) { throw 'ollama is not on PATH yet; close this window and run "Install Local AI.cmd" again.' }
    if (-not (Start-OllamaServer)) {
      throw 'Ollama did not become reachable at http://localhost:11434. Start Ollama from the Start menu, then run "Install Local AI.cmd" again.'
    }
    # Invoke-AiProcess never throws, so a failed pull/create must be surfaced
    # here or the phase is marked done with no model on the box.
    $pull = Invoke-AiProcess -FilePath $ollama.Source -ArgumentList @('pull', $State.models.chat.source) -TimeoutSec 3600
    if ($pull.Code -ne 0) {
      $tail = (($pull.Text -split "`r?`n") | Select-Object -Last 4) -join ' | '
      throw "ollama pull $($State.models.chat.source) failed (exit $($pull.Code)): $tail"
    }
    # Build the picked model's Modelfile on the fly (FROM <source> + the tier's
    # num_ctx) so every tier gets a right-sized context, not a fixed 32k file
    # that would over-reserve KV on a smaller box. Written to TEMP; no dependency
    # on a repo Modelfile that bakes 32k.
    $mfName = 'localai-' + ($State.models.chat.tag -replace '[:\\/]', '-') + '.Modelfile'
    $mfPath = Join-Path ([System.IO.Path]::GetTempPath()) $mfName
    "FROM $($State.models.chat.source)`nPARAMETER num_ctx $($State.models.chat.num_ctx)" |
      Set-Content -LiteralPath $mfPath -Encoding ascii
    $create = Invoke-AiProcess -FilePath $ollama.Source -ArgumentList @('create', $State.models.chat.tag, '-f', $mfPath) -TimeoutSec 600 -WorkingDirectory $RepoRoot
    if ($create.Code -ne 0) {
      $tail = (($create.Text -split "`r?`n") | Select-Object -Last 4) -join ' | '
      throw "ollama create $($State.models.chat.tag) failed (exit $($create.Code)): $tail"
    }
    # --lenient: a fresh box has only the picked model(s), not this repo's full
    # zoo, so missing alias sources must be skipped, not fatal (finding 2).
    [void](Invoke-Localai -Arguments @('model-aliases', '--lenient'))
  }
}

function Invoke-PhaseCompose {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  Write-Card 'Phase 5c - Compose up' @(
    'point DEFAULT_MODELS at the pick; write .env (SEARXNG_SECRET); localai start')
  if ($PSCmdlet.ShouldProcess('.env + stack', 'write .env and localai start')) {
    # This repo's compose hardcodes the tier-A daily driver; rewrite it to the
    # model we actually pulled so warm/health/model-scout AND Open WebUI's env all
    # see the pick (finding 1), instead of forever warming a model a smaller box
    # does not have. A silent failure here resurrects the bug, so surface it.
    $setDefault = Invoke-Localai -Arguments @('set-default-model', '--model', $State.models.chat.tag)
    if ($setDefault.Code -ne 0) {
      throw "set-default-model $($State.models.chat.tag) failed (exit $($setDefault.Code)): $($setDefault.Text)"
    }
    $envPath = Join-Path $RepoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
      $secret = [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
      "SEARXNG_SECRET=$secret" | Set-Content -LiteralPath $envPath -Encoding UTF8
    }
    [void](Invoke-Localai -Arguments @('start'))
  }
}

function Invoke-PhaseSeed {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  Write-Card 'Phase 5d - Seed Open WebUI DB' @(
    "localai webui-seed --model $($State.models.chat.tag) --num-ctx $($State.models.chat.num_ctx)")
  if ($PSCmdlet.ShouldProcess('open-webui DB', 'localai webui-seed')) {
    [void](Invoke-Localai -Arguments @(
        'webui-seed', '--model', $State.models.chat.tag,
        '--num-ctx', "$($State.models.chat.num_ctx)",
        '--default-model', $State.models.chat.tag))
  }
}

function Invoke-PhaseSecure {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  Write-Card 'Phase 6 - Secure by default' @(
    'ai-firewall -Apply (block physical-adapter ports); WinNAT :3000 fix if reserved',
    'Loopback-only binds verified; NO LAN exposure; NO autostart registered')
  if ($PSCmdlet.ShouldProcess('firewall', 'ai-firewall -Apply')) {
    # ai-firewall exits 0 OK / 1 warnings / 2 failures; UAC decline or timeout
    # must not silently pass as "secured".
    $fw = Invoke-AiProcess -FilePath 'pwsh' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $RepoRoot 'ai-firewall.ps1'), '-Apply') -TimeoutSec 300 -WorkingDirectory $RepoRoot
    if ($fw.Code -gt 1) {
      Write-Host '   WARN: firewall hardening did not complete (was the admin prompt declined?).' -ForegroundColor Yellow
      Write-Host '   You can apply it any time with:  pwsh -File ai-firewall.ps1 -Apply' -ForegroundColor Yellow
    }
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
      'Fix it from an Administrator PowerShell, then run "Install Local AI.cmd" again:',
      '  net stop winnat',
      '  netsh int ipv4 add excludedportrange protocol=tcp startport=3000 numberofports=1',
      '  net start winnat')
  }
}

function Invoke-PhaseSelfTest {
  Write-Card 'Phase 7 - Self-test + hand off' @('localai health (must exit 0)')
  if ($DryRun) {
    Write-Host '   [dry-run] would run: python -m localai health' -ForegroundColor DarkGray
  } else {
    $health = Invoke-Localai -Arguments @('health') -TimeoutSec 300
    Write-Host $health.Text
    if ($health.Code -ne 0) {
      Write-Host 'Self-test did not pass. Read the lines above, then run "Install Local AI.cmd" again to retry.' -ForegroundColor Red
      # Exit non-zero BEFORE the runner marks this phase done: a failed
      # self-test must neither print "Finished" nor be skipped on the retry.
      exit 1
    }
  }
  $ready = @(
    'Chat:   http://127.0.0.1:3000        (first signup becomes admin)',
    'Search: http://127.0.0.1:8080',
    'Start / stop:  localai start  /  localai stop   (in any terminal)',
    'Change model:  Open WebUI dropdown, or  localai warm --model <id>',
    'Security: loopback-only, firewall-blocked on physical adapters, no autostart.')
  if (@($State.intent) -contains 'web') {
    $ready += 'Browser agent: install WebBrain from the Chrome Web Store, set its server URL'
    $ready += '  to http://localhost:11434, and keep the Chrome window visible during tasks.'
    $ready += '  Details + troubleshooting: docs/webbrain.md'
  }
  Write-Card 'localai is ready' $ready
}

# ------------------------------------------------------- phase runner

# Phase order. The two environment phases come first and are never skipped on
# resume (Test-PhaseDone refuses to report them done), because the invariant this
# flow exists to hold is:
#
#   no model pull and no product-specific Python/model setup begins until a LIVE
#   gate has proved a usable local Docker path.
#
# See docs/design/virtualization-docker-preflight.md.
$Phases = @(
  @{ Name = 'environment-preflight'; Run = { Invoke-PhaseEnvironmentPreflight } }
  @{ Name = 'environment-ready'; Run = { Invoke-PhaseEnvironmentReady } }
  @{ Name = 'vet';      Run = { Invoke-PhaseVet } }
  @{ Name = 'intent';   Run = { Invoke-PhaseIntent } }
  @{ Name = 'python';   Run = { Invoke-PhasePython } }
  @{ Name = 'pip';      Run = { Invoke-PhasePip } }
  @{ Name = 'scout';    Run = { Invoke-PhaseScout } }
  @{ Name = 'ollama-docker'; Run = { Invoke-PhaseOllamaDocker } }
  @{ Name = 'pulls';    Run = { Invoke-PhasePulls } }
  @{ Name = 'compose';  Run = { Invoke-PhaseCompose } }
  @{ Name = 'seed';     Run = { Invoke-PhaseSeed } }
  @{ Name = 'secure';   Run = { Invoke-PhaseSecure } }
  @{ Name = 'self-test'; Run = { Invoke-PhaseSelfTest } }
)

# Preflight (review finding localai-43n): Python/Ollama/Docker all install via
# winget, and a missing winget used to surface as three ignored yellow warnings
# followed by a dead-end throw in Phase 3b. Fail fast, once, with the real fix -
# unless every tool winget would install is already present.
if (-not $DryRun -and -not (Get-Command 'winget.exe' -ErrorAction SilentlyContinue)) {
  Update-SessionPath
  $missing = @()
  if (-not (Resolve-Python)) { $missing += 'Python 3.12' }
  if (-not (Get-Command 'ollama.exe' -ErrorAction SilentlyContinue)) { $missing += 'Ollama' }
  if (-not (Get-Command 'docker.exe' -ErrorAction SilentlyContinue)) { $missing += 'Docker Desktop' }
  if ($missing.Count) {
    throw @"
winget is not available on this PC, and the installer needs it to install: $($missing -join ', ').
winget ships with Microsoft's App Installer - get it from https://aka.ms/getwinget
(Windows Sandbox and LTSC editions do not include it by default).
Or install the missing tools manually, then run "Install Local AI.cmd" again.
"@
  }
}

Write-Card 'localai Friend Bootstrapper' @(
  "Repo: $RepoRoot",
  $(if ($DryRun) { 'DRY RUN - nothing will be changed.' } else { 'Live run.' }),
  'Loopback-only, no autostart, no LAN exposure unless you opt in.')

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
