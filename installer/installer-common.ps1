<#
  installer-common.ps1 - shared helpers for the localai Friend Bootstrapper.

  Dot-source from Install-LocalAI.ps1 (which also dot-sources ai-common.ps1 for
  Invoke-AiProcess / Resolve-AiCommandPath):
      . (Join-Path $PSScriptRoot 'installer-common.ps1')

  OS-specific calls live here so a later macOS/Linux port can swap this one file
  and leave the orchestrator's phase logic intact.
#>

# ---------------------------------------------------------------- state file

# Schema version of installer-state.json. v1 -> v2 turned `pending_reboot` from a
# bare boolean into { required, reason } and added the `preflight` checkpoint.
$script:InstallerStateVersion = 2

# Phases that prove something about the LIVE machine. Persisted state is a resume
# hint and an audit record - never proof that the environment is still ready - so
# these are re-run on every attempt and are never recorded as done.
$script:InstallerAlwaysRunPhases = @('environment-preflight', 'environment-ready')

function Get-InstallerStatePath {
  param([string]$Root = $PSScriptRoot)
  return (Join-Path $Root 'installer-state.json')
}

function New-InstallerState {
  # A fresh skeleton at the current schema version.
  return [pscustomobject]@{
    version        = $script:InstallerStateVersion
    phases_done    = @()
    hardware       = $null
    intent         = @()
    models         = [pscustomobject]@{}
    preflight      = $null
    recovery       = $null
    legacy_install = [pscustomobject]@{ detected = $false }
    pending_reboot = [pscustomobject]@{ required = $false; reason = $null }
  }
}

function ConvertTo-CurrentInstallerState {
  # Migrate an imported object forward conservatively: keep every product choice
  # that is still meaningful, and never invent readiness that was not recorded.
  param([Parameter(Mandatory)]$Raw)
  $state = New-InstallerState
  foreach ($name in @('phases_done', 'hardware', 'intent', 'models', 'preflight', 'recovery', 'legacy_install')) {
    $prop = $Raw.PSObject.Properties[$name]
    if ($prop -and $null -ne $prop.Value) { $state.$name = $prop.Value }
  }
  # Safety-critical phases from an older run are dropped: they describe a machine
  # state that a rerun must re-observe, not a completed piece of work.
  $state.phases_done = @(@($state.phases_done) | Where-Object { $_ -and $_ -notin $script:InstallerAlwaysRunPhases })

  $pending = $Raw.PSObject.Properties['pending_reboot']
  if ($pending -and $null -ne $pending.Value) {
    $value = $pending.Value
    if ($value -is [bool]) {
      # v1 recorded only "a reboot is pending", and the only writer was the
      # Docker Desktop checkpoint - so that is the honest reason to record.
      $state.pending_reboot = [pscustomobject]@{
        required = [bool]$value
        reason   = $(if ($value) { 'legacy-docker-desktop-setup' } else { $null })
      }
    } else {
      $required = $value.PSObject.Properties['required']
      $reason = $value.PSObject.Properties['reason']
      $state.pending_reboot = [pscustomobject]@{
        required = [bool]($(if ($required) { $required.Value } else { $false }))
        reason   = $(if ($reason) { $reason.Value } else { $null })
      }
    }
  }
  return $state
}

function Import-InstallerState {
  <#
    Read the resume/audit state.

    Corrupt state is quarantined beside the original and replaced with a safe
    skeleton, rather than telling a customer to work out which file to delete.
    A state written by a NEWER AFK AI fails closed: silently reinterpreting a
    schema we do not understand is how false readiness gets invented.
  #>
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return (New-InstallerState) }

  $raw = $null
  try {
    $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  } catch {
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $quarantine = Join-Path (Split-Path -Parent $Path) `
      ("{0}.corrupt-{1}.json" -f [System.IO.Path]::GetFileNameWithoutExtension($Path), $stamp)
    # Quarantining can fail (file locked by another run, read-only directory).
    # That must not become a second failure on top of the first, but it does
    # change what we can honestly tell the user.
    $setAside = $true
    try {
      Move-Item -LiteralPath $Path -Destination $quarantine -Force -ErrorAction Stop
    } catch {
      $setAside = $false
      Write-Verbose "Could not quarantine corrupt installer state: $($_.Exception.Message)"
    }
    if ($setAside) {
      Write-Host '   Saved setup state was unreadable; it has been set aside and AFK AI will start it fresh.' -ForegroundColor Yellow
    } else {
      Write-Host '   Saved setup state was unreadable; AFK AI will start it fresh and overwrite it.' -ForegroundColor Yellow
    }
    return (New-InstallerState)
  }
  if ($null -eq $raw) { return (New-InstallerState) }

  $version = 1
  $vProp = $raw.PSObject.Properties['version']
  if ($vProp -and $null -ne $vProp.Value) { $version = [int]$vProp.Value }
  if ($version -gt $script:InstallerStateVersion) {
    throw "This setup state was written by a newer version of AFK AI (state format $version, this build understands $($script:InstallerStateVersion)). Install the newer AFK AI, or move installer-state.json aside to start fresh."
  }
  return (ConvertTo-CurrentInstallerState -Raw $raw)
}

function Save-InstallerState {
  <#
    Crash-consistent replacement: serialize, validate, write a same-directory
    temp file, then atomically replace the target. An interrupted run must leave
    either the previous complete state or the new complete state - never half a
    JSON document, which the next run would quarantine and discard.
  #>
  [CmdletBinding(SupportsShouldProcess)]
  param(
    [Parameter(Mandatory)]$State,
    [Parameter(Mandatory)][string]$Path
  )
  if (-not $PSCmdlet.ShouldProcess($Path, 'write installer state')) { return }

  $convertWarnings = $null
  $json = $State | ConvertTo-Json -Depth 8 -WarningAction SilentlyContinue -WarningVariable convertWarnings
  if ($convertWarnings) {
    # Depth truncation would silently drop fields; refuse rather than persist a
    # lossy state that a later run would trust.
    throw "Refusing to write installer state: it could not be serialized completely ($($convertWarnings[0]))."
  }
  $reparsed = $null
  try { $reparsed = $json | ConvertFrom-Json -ErrorAction Stop } catch {
    throw "Refusing to write installer state: serialized form did not parse back ($($_.Exception.Message))."
  }
  if ($null -eq $reparsed -or $null -eq $reparsed.PSObject.Properties['version']) {
    throw 'Refusing to write installer state: serialized form is missing its schema version.'
  }
  if (@($reparsed.phases_done).Count -ne @($State.phases_done).Count) {
    throw 'Refusing to write installer state: completed phases did not survive serialization.'
  }

  $dir = Split-Path -Parent $Path
  if (-not $dir) { $dir = '.' }
  if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
    New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null
  }
  $temp = Join-Path $dir ("{0}.tmp-{1}" -f (Split-Path -Leaf $Path), [guid]::NewGuid().ToString('n'))
  $backup = Join-Path $dir ("{0}.bak-{1}" -f (Split-Path -Leaf $Path), [guid]::NewGuid().ToString('n'))
  try {
    Set-Content -LiteralPath $temp -Value $json -Encoding UTF8 -ErrorAction Stop
    # Read the temp file back before it becomes the only copy.
    [void](Get-Content -LiteralPath $temp -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop)
    # Windows PowerShell 5.1 runs on .NET Framework, whose File.Move has no
    # three-argument overwrite overload. File.Replace is atomic on the same
    # volume and is available there; the two-argument Move handles first write.
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
      [System.IO.File]::Replace($temp, $Path, $backup)
      Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    } else {
      [System.IO.File]::Move($temp, $Path)
    }
  } catch {
    Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    throw
  }
}

# ------------------------------------------------------- structured event stream

$script:InstallerEventStreamEnabled = $false

function Set-InstallerEventStream {
  param([bool]$Enabled)
  $script:InstallerEventStreamEnabled = $Enabled
}

function Write-InstallerEvent {
  <# Emit one versioned line for the native AFK LocalAI shell. #>
  param(
    [Parameter(Mandatory)][string]$EventType,
    [Parameter(Mandatory)][string]$Phase,
    [string]$Status = '',
    [string]$Code = '',
    [string]$Message = '',
    $Data = $null,
    [switch]$Enabled
  )
  if (-not ($Enabled -or $script:InstallerEventStreamEnabled)) { return }
  $payload = [ordered]@{
    schema_version = 1
    timestamp_utc = [DateTime]::UtcNow.ToString('o')
    event_type = $EventType
    phase = $Phase
    status = $Status
    code = $Code
    message = $Message
  }
  if ($null -ne $Data) { $payload.data = $Data }
  Write-Output ('AFK-EVENT:' + ($payload | ConvertTo-Json -Depth 8 -Compress))
}

function Test-PreflightCheckpointIsProof {
  <#
    Always false, by design and by contract.

    The persisted preflight checkpoint exists for resume messaging and support
    diagnostics. It is never evidence that the machine is ready now: firmware,
    Windows features, WSL and Docker can all change between runs, so readiness is
    only ever established by a fresh live probe.
  #>
  param([Parameter(Mandatory)]$State)
  $recorded = $null
  if ($State -and $State.PSObject.Properties['preflight']) { $recorded = $State.preflight }
  if ($null -ne $recorded) {
    Write-Verbose "Ignoring persisted preflight verdict '$($recorded.overall)' from $($recorded.observed_at): readiness is only ever established by a fresh live probe."
  }
  return $false
}

function Test-PhaseDone {
  param([Parameter(Mandatory)]$State, [Parameter(Mandatory)][string]$Phase)
  if ($Phase -in $script:InstallerAlwaysRunPhases) { return $false }
  return (@($State.phases_done) -contains $Phase)
}

function Set-PhaseDone {
  [CmdletBinding(SupportsShouldProcess)]
  param(
    [Parameter(Mandatory)]$State,
    [Parameter(Mandatory)][string]$Phase,
    [Parameter(Mandatory)][string]$Path
  )
  if ($Phase -in $script:InstallerAlwaysRunPhases) { return }
  if (Test-PhaseDone -State $State -Phase $Phase) { return }
  if ($PSCmdlet.ShouldProcess($Phase, 'mark phase done')) {
    $State.phases_done = @($State.phases_done) + $Phase
    Save-InstallerState -State $State -Path $Path
  }
}

# ----------------------------------------------------------------- messaging

function Write-Card {
  param(
    [Parameter(Mandatory)][string]$Title,
    [string[]]$Lines = @()
  )
  Write-Host ''
  Write-Host "== $Title ==" -ForegroundColor Cyan
  foreach ($line in $Lines) { Write-Host "   $line" }
}

function Get-InstallerChoice {
  # Prompt for a yes/no; -AcceptDefaults skips the prompt and returns the default.
  param(
    [Parameter(Mandatory)][string]$Question,
    [bool]$Default = $true,
    [switch]$AcceptDefaults
  )
  if ($AcceptDefaults) { return $Default }
  $suffix = if ($Default) { '[Y/n]' } else { '[y/N]' }
  $answer = Read-Host "$Question $suffix"
  if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
  return ($answer.Trim().ToLowerInvariant() -in @('y', 'yes'))
}

# ------------------------------------------------------------- PATH / winget

function Update-SessionPath {
  # winget installs update the registry PATH but not this process; refresh it so
  # freshly installed tools resolve without a new shell (audit finding 9).
  $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $user = [Environment]::GetEnvironmentVariable('Path', 'User')
  $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

function Install-WithWinget {
  # Install one package id via winget, idempotently. Returns $true on success or
  # already-present. Mirrors ai-anywhere.ps1's Install-TailscaleWithWinget.
  [CmdletBinding(SupportsShouldProcess)]
  param(
    [Parameter(Mandatory)][string]$Id,
    [int]$TimeoutSec = 900
  )
  $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
  if (-not $winget) {
    Write-Host "   winget not found; install $Id manually." -ForegroundColor Yellow
    return $false
  }
  $cmdArgs = @(
    'install', '--id', $Id, '-e',
    '--accept-package-agreements', '--accept-source-agreements'
  )
  if (-not $PSCmdlet.ShouldProcess($Id, 'winget install')) { return $true }
  $result = Invoke-AiProcess -FilePath $winget.Source -ArgumentList $cmdArgs -TimeoutSec $TimeoutSec
  Update-SessionPath
  if ($result.Code -eq 0) { return $true }
  # winget exit code for "no applicable upgrade / already installed" is non-fatal.
  Write-Host "   winget $Id -> exit $($result.Code): $($result.Text)" -ForegroundColor Yellow
  return $false
}

# -------------------------------------------------------- user-scope env vars

function Set-UserEnvVar {
  # Set a user-scope env var AND this session's copy so it takes effect now.
  # Used for the load-bearing Ollama vars (audit finding 1).
  [CmdletBinding(SupportsShouldProcess)]
  param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$Value
  )
  if ($PSCmdlet.ShouldProcess("$Name=$Value", 'set user env var')) {
    [Environment]::SetEnvironmentVariable($Name, $Value, 'User')
    Set-Item -LiteralPath "Env:$Name" -Value $Value
  }
}

# The Ollama host env a fresh box lacks. OLLAMA_HOST is required for
# Docker->Ollama; OLLAMA_KV_CACHE_TYPE=q8_0 is load-bearing for the tier VRAM
# math (tiers.json assumes it).
$script:OllamaUserEnv = [ordered]@{
  OLLAMA_HOST           = '0.0.0.0:11434'
  OLLAMA_KV_CACHE_TYPE  = 'q8_0'
  OLLAMA_FLASH_ATTENTION = '1'
  OLLAMA_KEEP_ALIVE     = '30m'
}

function Set-OllamaHostEnv {
  [CmdletBinding(SupportsShouldProcess)]
  param()
  foreach ($name in $script:OllamaUserEnv.Keys) {
    Set-UserEnvVar -Name $name -Value $script:OllamaUserEnv[$name]
  }
}

# WebBrain (the supported browser-agent extension) calls Ollama's OpenAI-style
# /v1 endpoints straight from its Chrome extension origin, and Ollama 403s
# extension origins it does not know. Allowlist exactly WebBrain's id (stable
# across installs) - deliberately narrower than chrome-extension://*, which
# would let ANY installed extension talk to Ollama. See docs/webbrain.md.
$script:WebBrainOrigin = 'chrome-extension://ljhijonmfahplgbbacgcfnaihbjljhhb'

function Test-OllamaApi {
  try {
    [void](Invoke-RestMethod -Uri 'http://localhost:11434/api/version' -TimeoutSec 3)
    return $true
  } catch {
    return $false
  }
}

function Start-OllamaServer {
  # Phase 5b needs the server up for `ollama pull`, but `localai start` (which
  # launches Ollama) only runs in Phase 5c - review finding localai-wgz. Mirror
  # start.py's launch-the-app-then-wait pattern. Launched from this process, so
  # it inherits the session copies of the Ollama env vars set in Phase 5a.
  [CmdletBinding(SupportsShouldProcess)]
  param([int]$TimeoutSec = 60)
  if (Test-OllamaApi) { return $true }
  if (-not $PSCmdlet.ShouldProcess('Ollama', 'launch detached')) { return $true }
  $app = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
  if (Test-Path -LiteralPath $app) {
    Start-Process -FilePath $app -WindowStyle Hidden | Out-Null
  } else {
    $cli = Get-Command 'ollama.exe' -ErrorAction SilentlyContinue
    if (-not $cli) { return $false }
    Start-Process -FilePath $cli.Source -ArgumentList 'serve' -WindowStyle Hidden | Out-Null
  }
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
  while ([DateTime]::UtcNow -lt $deadline) {
    if (Test-OllamaApi) { return $true }
    Start-Sleep -Seconds 2
  }
  return $false
}

function Add-OllamaUserOrigin {
  # Append one origin to the user-scope OLLAMA_ORIGINS. Never clobbers: a
  # user's existing custom entries are kept and the origin is only added once.
  [CmdletBinding(SupportsShouldProcess)]
  param([Parameter(Mandatory)][string]$Origin)
  $existing = [Environment]::GetEnvironmentVariable('OLLAMA_ORIGINS', 'User')
  if ($existing -and (@($existing -split '\s*,\s*') -contains $Origin)) { return }
  $value = if ($existing) { "$existing,$Origin" } else { $Origin }
  Set-UserEnvVar -Name 'OLLAMA_ORIGINS' -Value $value
}

# ----------------------------------------------- Phase 1 hardware vet (pure PS)

function Get-VetVramGb {
  # Total VRAM (GB) from nvidia-smi, or $null when absent (never a false 12 -
  # audit finding 4). Win32_VideoController is presence/name only; its AdapterRAM
  # caps at 4 GB, so it is never used for VRAM math.
  $smi = Get-Command 'nvidia-smi' -ErrorAction SilentlyContinue
  if (-not $smi) { return $null }
  $out = Invoke-AiProcess -FilePath $smi.Source -ArgumentList @(
    '--query-gpu=memory.total', '--format=csv,noheader,nounits') -TimeoutSec 15
  if ($out.Code -ne 0 -or -not $out.Text) { return $null }
  $first = ($out.Text -split "`n")[0].Trim()
  $mib = 0.0
  # Invariant culture: TryParse honours the OS locale by default, and e.g. a
  # comma decimal separator would silently mis-parse on non-en-US Windows.
  if (-not [double]::TryParse($first, [System.Globalization.NumberStyles]::Float,
      [System.Globalization.CultureInfo]::InvariantCulture, [ref]$mib)) { return $null }
  return [math]::Round($mib / 1024, 1)
}

function Get-VetGpuName {
  $smi = Get-Command 'nvidia-smi' -ErrorAction SilentlyContinue
  if ($smi) {
    $out = Invoke-AiProcess -FilePath $smi.Source -ArgumentList @(
      '--query-gpu=name', '--format=csv,noheader') -TimeoutSec 15
    if ($out.Code -eq 0 -and $out.Text) { return ($out.Text -split "`n")[0].Trim() }
  }
  $gpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty Name
  return $gpu
}

function Get-CapabilityTier {
  # Highest tier whose min_vram_gb the card meets, from tiers.json (the single
  # source shared with installer_vet.classify_tier). Threshold pick only - no fit
  # math duplicated here. $null / 0 VRAM -> CPU.
  param(
    [Parameter(Mandatory)]$Tiers,
    [Nullable[double]]$VramGb
  )
  $usable = if ($null -eq $VramGb) { 0.0 } else { [double]$VramGb }
  $eligible = @($Tiers.tiers | Where-Object { $usable -ge $_.min_vram_gb })
  if (-not $eligible) { return ($Tiers.tiers | Where-Object { $_.id -eq 'CPU' }) }
  return ($eligible | Sort-Object min_vram_gb -Descending | Select-Object -First 1)
}
