<#
  installer/preflight.ps1 - read-only environment preflight for the AFK AI
  Windows installer.

  Implements docs/design/virtualization-docker-preflight.md: probe the LOCAL
  Windows virtualization / WSL / Docker environment, classify it into stable
  machine-readable states, and render one plain-language next action - all
  before the installer spends time on Python, model selection, or model pulls.

  Two layers, deliberately separated so the classifier is fixture-testable:

    Get-PreflightEvidence      I/O. Bounded, read-only, no network. Never throws;
                               a failed probe becomes evidence of UNKNOWN.
    Invoke-EnvironmentPreflight  Pure. Evidence -> component states -> one
                               aggregate disposition, reason code and action.

  This file MUST stay read-only. It does not enable Windows features, edit boot
  configuration, install or update WSL, start or stop services, elevate, sign in
  to a registry, or touch the network. Any machine mutation belongs in a
  separately reviewed recovery action in the orchestrator, not here.

  Dot-source after ai-common.ps1 (for Invoke-AiProcess):
      . (Join-Path $PSScriptRoot 'preflight.ps1')
#>

# Bump when the meaning of a persisted classification changes, so an old
# checkpoint can be recognised as having come from a different classifier.
$script:PreflightClassifierVersion = 2

# Docker documents WSL 2.1.5 as the minimum for its WSL 2 backend. This floor is
# only applied when the WSL backend is actually the relevant path.
$script:PreflightWslMinimumVersion = [version]'2.1.5'

# Windows 11 is the documented Friend Beta target (README, SUPPORT.md,
# docs/releases/0.1.7rc1.md). 22000 is the first Windows 11 build.
$script:PreflightMinimumWindowsBuild = 22000

# Bounded timeouts. Every external command below passes one explicitly; a probe
# that hangs is evidence of an unhealthy component, never of a healthy one.
$script:PreflightWslTimeoutSec = 20
$script:PreflightDockerTimeoutSec = 30

# ------------------------------------------------------------------ helpers

function Get-PreflightProperty {
    # Read one property off possibly-absent evidence without throwing. Missing
    # evidence must degrade to UNKNOWN, not to an exception three layers up.
    param($Evidence, [Parameter(Mandatory)][string]$Name, $Default = $null)
    if ($null -eq $Evidence) { return $Default }
    $prop = $Evidence.PSObject.Properties[$Name]
    if (-not $prop) { return $Default }
    if ($null -eq $prop.Value) { return $Default }
    return $prop.Value
}

function Get-PreflightFirstVersion {
    # First dotted version in a command's output. Locale-independent on purpose:
    # `wsl --version` localizes its LABELS ("WSL version" / "WSL-Version") but
    # not the digits, so never match on the prose.
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    $m = [regex]::Match($Text, '\d+(?:\.\d+){1,3}')
    if (-not $m.Success) { return $null }
    try { return [version]$m.Value } catch { return $null }
}

function Get-PreflightEndpointKind {
    # Classify a Docker endpoint by LOCALITY, not by context name: any context can
    # be renamed, and DOCKER_HOST can override the context entirely. Only the
    # kinds below are ever persisted - never the address itself.
    param([string]$Endpoint)
    if ([string]::IsNullOrWhiteSpace($Endpoint)) { return 'unknown' }
    $e = $Endpoint.Trim()
    if ($e -match '^npipe://') { return 'local-npipe' }
    if ($e -match '^unix://') { return 'local-unix' }
    if ($e -match '^ssh://') { return 'remote-ssh' }
    if ($e -match '^tcp://(?<host>[^:/]+|\[[^\]]+\])') {
        $h = $Matches['host'].Trim('[', ']')
        if ($h -eq 'localhost' -or $h -eq '::1' -or $h -match '^127\.\d+\.\d+\.\d+$') { return 'local-tcp' }
        return 'remote-tcp'
    }
    return 'unknown'
}

function Test-PreflightEndpointIsLocal {
    param([string]$Kind)
    return ($Kind -in @('local-npipe', 'local-unix', 'local-tcp'))
}

# --------------------------------------------------------------- classifiers
# Each returns [pscustomobject]@{ Status = <enum>; ... bounded evidence ... }.
# Pure functions: no I/O, no clock, no randomness - the same evidence always
# classifies the same way.

function Get-PreflightPlatformState {
    param($Evidence)
    $queried = Get-PreflightProperty $Evidence 'Queried' $false
    if (-not $queried) {
        return [pscustomobject]@{ Status = 'UNKNOWN'; Build = $null }
    }
    $isWindowsOs = Get-PreflightProperty $Evidence 'IsWindows' $null
    $build = Get-PreflightProperty $Evidence 'Build' $null
    if ($isWindowsOs -ne $true) {
        return [pscustomobject]@{ Status = 'UNSUPPORTED_PLATFORM'; Build = $build }
    }
    if ($null -eq $build) {
        return [pscustomobject]@{ Status = 'UNKNOWN'; Build = $null }
    }
    if ([int]$build -lt $script:PreflightMinimumWindowsBuild) {
        return [pscustomobject]@{ Status = 'UNSUPPORTED_PLATFORM'; Build = [int]$build }
    }
    return [pscustomobject]@{ Status = 'SUPPORTED'; Build = [int]$build }
}

function Get-PreflightFirmwareState {
    <#
      Win32_Processor.VirtualizationFirmwareEnabled is a Windows-visible signal
      that firmware enabled the virtualization extensions. Missing, null, mixed,
      or unreadable is UNKNOWN - never "disabled". Guessing disabled here would
      send a user into their BIOS for no reason.

      HYPERVISOR MASKING (found on a real Windows 11 26200 machine, i9-14900HX):
      once a hypervisor owns the virtualization extensions, Windows stops
      reporting the underlying firmware features and Win32_Processor returns
      VirtualizationFirmwareEnabled=False, SecondLevelAddressTranslationExtensions
      =False and VMMonitorModeExtensions=False while
      Win32_ComputerSystem.HypervisorPresent is True. `systeminfo` says the same
      thing in prose: "A hypervisor has been detected. Features required for
      Hyper-V will not be displayed."

      That combination is physically impossible as a genuine reading - a running
      hypervisor cannot exist without firmware virtualization - so a present
      hypervisor is treated as authoritative proof that firmware virtualization
      is enabled. Without this, any machine running Hyper-V or VBS/Credential
      Guard would be told to go change a BIOS setting that is already correct.
    #>
    param($Evidence, $HypervisorPresent)

    if ($HypervisorPresent -eq $true) {
        return [pscustomobject]@{ Status = 'READY'; Source = 'Win32_ComputerSystem.HypervisorPresent' }
    }

    $queried = Get-PreflightProperty $Evidence 'Queried' $false
    $values = @(Get-PreflightProperty $Evidence 'Values' @())
    if (-not $queried -or $values.Count -eq 0) {
        return [pscustomobject]@{ Status = 'UNKNOWN'; Source = 'Win32_Processor.VirtualizationFirmwareEnabled' }
    }
    if (@($values | Where-Object { $null -eq $_ }).Count -gt 0) {
        return [pscustomobject]@{ Status = 'UNKNOWN'; Source = 'Win32_Processor.VirtualizationFirmwareEnabled' }
    }
    $true_ = @($values | Where-Object { $_ -eq $true }).Count
    $false_ = @($values | Where-Object { $_ -eq $false }).Count
    if ($true_ -gt 0 -and $false_ -eq 0) {
        return [pscustomobject]@{ Status = 'READY'; Source = 'Win32_Processor.VirtualizationFirmwareEnabled' }
    }
    if ($false_ -gt 0 -and $true_ -eq 0) {
        return [pscustomobject]@{ Status = 'FIRMWARE_VIRTUALIZATION_DISABLED'; Source = 'Win32_Processor.VirtualizationFirmwareEnabled' }
    }
    # Processors disagreeing with each other is not a diagnosis.
    return [pscustomobject]@{ Status = 'UNKNOWN'; Source = 'Win32_Processor.VirtualizationFirmwareEnabled' }
}

function Get-PreflightWindowsVirtualizationState {
    # Deliberately separate from firmware state: "firmware says yes but no
    # hypervisor is running" and "firmware says no" need different actions.
    param($Evidence, [bool]$DockerIsHealthyLocal = $false)
    $queried = Get-PreflightProperty $Evidence 'Queried' $false
    if (-not $queried) {
        return [pscustomobject]@{ Status = 'UNKNOWN'; HypervisorPresent = $null }
    }
    $features = Get-PreflightProperty $Evidence 'Features' @{}
    $hypervisor = Get-PreflightProperty $Evidence 'HypervisorPresent' $null
    $pendingReboot = Get-PreflightProperty $Evidence 'PendingReboot' $null

    $known = @($features.Keys | Where-Object { $features[$_] -in @('Enabled', 'Disabled') })
    if ($known.Count -eq 0 -and $hypervisor -ne $true) {
        return [pscustomobject]@{ Status = 'UNKNOWN'; HypervisorPresent = $null }
    }
    $disabled = @($features.Keys | Where-Object {
        $features[$_] -eq 'Disabled' -and
        -not ($DockerIsHealthyLocal -and $_ -eq 'Microsoft-Windows-Subsystem-Linux')
    })
    if ($disabled.Count -gt 0) {
        return [pscustomobject]@{ Status = 'WINDOWS_VIRTUALIZATION_FEATURE_MISSING'; HypervisorPresent = $hypervisor }
    }
    # A pending reboot outranks "not running": the fix is a restart, not a repair.
    if ($pendingReboot -eq $true -and $hypervisor -ne $true) {
        return [pscustomobject]@{ Status = 'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED'; HypervisorPresent = $hypervisor }
    }
    if ($hypervisor -eq $false) {
        return [pscustomobject]@{ Status = 'WINDOWS_HYPERVISOR_NOT_RUNNING'; HypervisorPresent = $false }
    }
    if ($null -eq $hypervisor) {
        return [pscustomobject]@{ Status = 'UNKNOWN'; HypervisorPresent = $null }
    }
    return [pscustomobject]@{ Status = 'READY'; HypervisorPresent = $true }
}

function Get-PreflightWslRawState {
    # WSL on its own terms, before asking whether this machine needs it at all.
    param($Evidence)
    $found = Get-PreflightProperty $Evidence 'CommandFound' $null
    if ($null -eq $found) { return [pscustomobject]@{ Status = 'UNKNOWN'; Version = $null } }
    if ($found -ne $true) { return [pscustomobject]@{ Status = 'WSL_NOT_INSTALLED'; Version = $null } }
    if ((Get-PreflightProperty $Evidence 'TimedOut' $false) -eq $true) {
        return [pscustomobject]@{ Status = 'WSL_UNHEALTHY'; Version = $null }
    }
    $exit = Get-PreflightProperty $Evidence 'VersionExit' $null
    if ($exit -ne 0) {
        # wsl.exe is present but rejected `--version`: that is the older in-box
        # WSL, which is below the documented Docker WSL-backend floor.
        return [pscustomobject]@{ Status = 'WSL_UPDATE_REQUIRED'; Version = $null }
    }
    $version = Get-PreflightFirstVersion (Get-PreflightProperty $Evidence 'VersionText' '')
    if ($null -eq $version) { return [pscustomobject]@{ Status = 'UNKNOWN'; Version = $null } }
    if ($version -lt $script:PreflightWslMinimumVersion) {
        return [pscustomobject]@{ Status = 'WSL_UPDATE_REQUIRED'; Version = "$version" }
    }
    return [pscustomobject]@{ Status = 'READY'; Version = "$version" }
}

function Get-PreflightWslState {
    # WSL matters only for the WSL Docker backend. `$DockerIsHealthyLocal` lets a
    # verified local engine on another supported backend keep WSL out of the way
    # instead of inventing a blocker the machine does not have.
    param($Evidence, [bool]$DockerIsHealthyLocal = $false)

    $state = Get-PreflightWslRawState -Evidence $Evidence
    if ($state.Status -ne 'READY' -and $DockerIsHealthyLocal) {
        # Precedence rule 3: a proven local Linux engine outranks a WSL probe for
        # a backend that engine demonstrably is not using.
        return [pscustomobject]@{ Status = 'NOT_REQUIRED_FOR_CURRENT_HEALTHY_BACKEND'; Version = $state.Version }
    }
    return $state
}

function Get-PreflightDockerState {
    # `docker.exe` existing is NOT Docker being ready - that weak check is what
    # let the Friend Beta reach model setup on a machine Docker could never run
    # on. Readiness requires a reachable LOCAL Linux engine.
    param($Evidence, [string]$MinimumDockerVersion)

    if (-not (Get-PreflightProperty $Evidence 'CliFound' $false)) {
        return [pscustomobject]@{
            Status = 'DOCKER_NOT_INSTALLED'; EndpointKind = 'unknown'; EngineOs = $null; ServerVersion = $null
        }
    }

    # DOCKER_HOST overrides the selected context, so it decides where the CLI
    # actually points. A successful `docker info` against someone else's machine
    # says nothing about THIS one.
    $hostEnv = Get-PreflightProperty $Evidence 'DockerHostEnv' $null
    $endpoint = if ($hostEnv) { $hostEnv } else { Get-PreflightProperty $Evidence 'ContextEndpoint' $null }
    $kind = Get-PreflightEndpointKind -Endpoint $endpoint

    if (-not (Test-PreflightEndpointIsLocal -Kind $kind)) {
        if ($kind -eq 'unknown') {
            return [pscustomobject]@{ Status = 'UNKNOWN'; EndpointKind = $kind; EngineOs = $null; ServerVersion = $null }
        }
        return [pscustomobject]@{ Status = 'DOCKER_CONTEXT_REMOTE'; EndpointKind = $kind; EngineOs = $null; ServerVersion = $null }
    }

    if ((Get-PreflightProperty $Evidence 'TimedOut' $false) -eq $true) {
        return [pscustomobject]@{ Status = 'UNKNOWN'; EndpointKind = $kind; EngineOs = $null; ServerVersion = $null }
    }

    $exit = Get-PreflightProperty $Evidence 'InfoExit' $null
    if ($null -eq $exit) {
        return [pscustomobject]@{ Status = 'UNKNOWN'; EndpointKind = $kind; EngineOs = $null; ServerVersion = $null }
    }
    if ($exit -ne 0) {
        if ((Get-PreflightProperty $Evidence 'DesktopProcessRunning' $false) -eq $true) {
            # Desktop is up but the engine has not published yet: a bounded
            # "still starting", not a diagnosis.
            return [pscustomobject]@{ Status = 'DOCKER_STARTING'; EndpointKind = $kind; EngineOs = $null; ServerVersion = $null }
        }
        return [pscustomobject]@{ Status = 'DOCKER_INSTALLED_NOT_RUNNING'; EndpointKind = $kind; EngineOs = $null; ServerVersion = $null }
    }

    $osType = "$(Get-PreflightProperty $Evidence 'InfoOsType' '')".Trim().ToLowerInvariant()
    $serverVersion = Get-PreflightProperty $Evidence 'InfoServerVersion' $null
    if ($osType -eq 'windows') {
        return [pscustomobject]@{
            Status = 'DOCKER_LINUX_ENGINE_REQUIRED'; EndpointKind = $kind; EngineOs = 'windows'; ServerVersion = $serverVersion
        }
    }
    if ($osType -ne 'linux') {
        return [pscustomobject]@{ Status = 'UNKNOWN'; EndpointKind = $kind; EngineOs = $null; ServerVersion = $serverVersion }
    }
    # A version floor is only meaningful once the project needs a concrete
    # feature from it. With no floor configured, version alone never blocks.
    if ($MinimumDockerVersion) {
        $have = Get-PreflightFirstVersion "$serverVersion"
        $need = Get-PreflightFirstVersion $MinimumDockerVersion
        if ($have -and $need -and $have -lt $need) {
            return [pscustomobject]@{
                Status = 'DOCKER_VERSION_UNSUPPORTED'; EndpointKind = $kind; EngineOs = 'linux'; ServerVersion = $serverVersion
            }
        }
    }
    return [pscustomobject]@{
        Status = 'DOCKER_HEALTHY_LOCAL'; EndpointKind = $kind; EngineOs = 'linux'; ServerVersion = $serverVersion
    }
}

# ------------------------------------------------------- aggregate disposition

# Blocker -> stable reason code. Ordered most-actionable-first: a user with a
# disabled BIOS setting should be told about the BIOS, not about the Docker
# install that could never have worked anyway.
$script:PreflightBlockerCodes = [ordered]@{
    'FIRMWARE_VIRTUALIZATION_DISABLED'         = 'PREFLIGHT-FIRMWARE-VIRT-DISABLED'
    'WINDOWS_VIRTUALIZATION_FEATURE_MISSING'   = 'PREFLIGHT-WINDOWS-FEATURE-MISSING'
    'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED'   = 'PREFLIGHT-WINDOWS-REBOOT-REQUIRED'
    'WINDOWS_HYPERVISOR_NOT_RUNNING'           = 'PREFLIGHT-HYPERVISOR-NOT-RUNNING'
    'WSL_NOT_INSTALLED'                        = 'PREFLIGHT-WSL-NOT-INSTALLED'
    'WSL_UPDATE_REQUIRED'                      = 'PREFLIGHT-WSL-UPDATE-REQUIRED'
    'WSL_UNHEALTHY'                            = 'PREFLIGHT-WSL-UNHEALTHY'
    'DOCKER_CONTEXT_REMOTE'                    = 'PREFLIGHT-DOCKER-REMOTE-CONTEXT'
    'DOCKER_LINUX_ENGINE_REQUIRED'             = 'PREFLIGHT-DOCKER-LINUX-ENGINE-REQUIRED'
    'DOCKER_VERSION_UNSUPPORTED'               = 'PREFLIGHT-DOCKER-VERSION-UNSUPPORTED'
    'DOCKER_NOT_INSTALLED'                     = 'PREFLIGHT-DOCKER-NOT-INSTALLED'
    'DOCKER_STARTING'                          = 'PREFLIGHT-DOCKER-STARTING'
    'DOCKER_INSTALLED_NOT_RUNNING'             = 'PREFLIGHT-DOCKER-NOT-RUNNING'
}

# Docker's own failure text is used ONLY to lower confidence, never to raise it.
# If Windows says the virtualization stack is fine but Docker says it is not, the
# honest answer is "we do not know", not a confident wrong instruction. On a
# non-English Docker this simply does not match, and the machine falls back to
# the plainer blocker - conservative in the safe direction.
$script:PreflightDockerVirtErrorPattern = '(?i)virtualization|hypervisor|hyper-v|wsl\s*2|vmcompute'

function Invoke-EnvironmentPreflight {
    <#
      Evidence -> one disposition. Pure and deterministic.

      Returns Platform/Firmware/WindowsVirtualization/Wsl/Docker component states
      plus Overall, Code, Action, RebootRequired.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$Evidence,
        [string]$MinimumDockerVersion
    )

    $platform = Get-PreflightPlatformState (Get-PreflightProperty $Evidence 'Platform' $null)
    $windowsEvidence = Get-PreflightProperty $Evidence 'WindowsVirtualization' $null
    # The hypervisor signal is read before firmware because a running hypervisor
    # un-masks the firmware reading (see Get-PreflightFirmwareState).
    $firmware = Get-PreflightFirmwareState `
        -Evidence (Get-PreflightProperty $Evidence 'Firmware' $null) `
        -HypervisorPresent (Get-PreflightProperty $windowsEvidence 'HypervisorPresent' $null)
    $dockerEvidence = Get-PreflightProperty $Evidence 'Docker' $null
    $docker = Get-PreflightDockerState -Evidence $dockerEvidence -MinimumDockerVersion $MinimumDockerVersion
    $dockerHealthy = ($docker.Status -eq 'DOCKER_HEALTHY_LOCAL')
    $windows = Get-PreflightWindowsVirtualizationState `
        -Evidence $windowsEvidence -DockerIsHealthyLocal $dockerHealthy
    $wsl = Get-PreflightWslState -Evidence (Get-PreflightProperty $Evidence 'Wsl' $null) -DockerIsHealthyLocal $dockerHealthy

    $result = [pscustomobject]@{
        ClassifierVersion     = $script:PreflightClassifierVersion
        Platform              = $platform
        Firmware              = $firmware
        WindowsVirtualization = $windows
        Wsl                   = $wsl
        Docker                = $docker
        Overall               = 'UNKNOWN_BLOCKER'
        Reason                = 'UNKNOWN'
        Code                  = 'PREFLIGHT-UNKNOWN'
        Action                = $null
        RebootRequired        = $false
    }

    # Precedence 1: a documented-unsupported platform is never READY, no matter
    # how healthy Docker happens to be.
    if ($platform.Status -eq 'UNSUPPORTED_PLATFORM') {
        $result.Overall = 'UNSUPPORTED_PLATFORM'
        $result.Reason = 'UNSUPPORTED_PLATFORM'
        $result.Code = 'PREFLIGHT-UNSUPPORTED-PLATFORM'
        $result.Action = 'unsupported-platform'
        return $result
    }

    # Precedence 2: safety-critical evidence we could not obtain. A healthy local
    # engine can vouch for the backend layers below it, but nothing can vouch for
    # the platform check itself.
    if ($platform.Status -ne 'SUPPORTED') {
        $result.Overall = 'UNKNOWN_BLOCKER'
        $result.Reason = 'PLATFORM_UNKNOWN'
        $result.Code = 'PREFLIGHT-UNKNOWN'
        $result.Action = 'report-unknown'
        return $result
    }

    # Docker's error text contradicting healthy Windows evidence is a genuine
    # "we do not know", not a Docker-restart problem. This outranks every
    # blocker below: the layers we believe we know are actively in doubt, not
    # merely unobserved, so no recovery derived from them is trustworthy.
    $dockerText = "$(Get-PreflightProperty $dockerEvidence 'InfoText' '')"
    $conflict = (
        -not $dockerHealthy -and
        $firmware.Status -eq 'READY' -and
        $windows.Status -eq 'READY' -and
        $dockerText -match $script:PreflightDockerVirtErrorPattern
    )

    if (-not $dockerHealthy -and $conflict) {
        $result.Overall = 'UNKNOWN_BLOCKER'
        $result.Reason = 'CONFLICTING_VIRTUALIZATION_EVIDENCE'
        $result.Code = 'PREFLIGHT-UNKNOWN'
        $result.Action = 'report-unknown'
        $result.RebootRequired = ($windows.Status -eq 'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED')
        return $result
    }

    # Precedence 3/4: walk the prerequisite stack from the bottom up and report
    # the first layer that is either unobserved or conclusively blocked.
    #
    # The layers depend on each other in this order, so a conclusive blocker is
    # trustworthy exactly when everything BENEATH it is conclusively known - its
    # recovery rests on those layers. An UNKNOWN ABOVE it says nothing about
    # whether that recovery is safe or useful, so it must not hide it.
    #
    # This is the fix for a real installer run on a developer machine: firmware
    # READY, Windows virtualization READY, WSL conclusively WSL_UPDATE_REQUIRED,
    # Docker UNKNOWN. The flat "any unknown layer wins" rule collapsed that to
    # PREFLIGHT-UNKNOWN / report-unknown, so a user with one safe, bounded,
    # independently established step was told only that something was unknown.
    #
    # Unknown is still a first-class answer; it just no longer borrows authority
    # from a layer it sits above.
    if (-not $dockerHealthy) {
        $layers = @(
            @{ Unknown = 'FIRMWARE_UNKNOWN';               Status = $firmware.Status }
            @{ Unknown = 'WINDOWS_VIRTUALIZATION_UNKNOWN'; Status = $windows.Status }
            @{ Unknown = 'WSL_UNKNOWN';                    Status = $wsl.Status }
            @{ Unknown = 'DOCKER_UNKNOWN';                 Status = $docker.Status }
        )
        foreach ($layer in $layers) {
            if ($layer.Status -eq 'UNKNOWN') {
                $result.Overall = 'UNKNOWN_BLOCKER'
                $result.Reason = $layer.Unknown
                $result.Code = 'PREFLIGHT-UNKNOWN'
                $result.Action = 'report-unknown'
                $result.RebootRequired = ($windows.Status -eq 'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED')
                return $result
            }
            if ($script:PreflightBlockerCodes.Contains($layer.Status)) {
                $result.Overall = 'RECOVERABLE_BLOCKER'
                $result.Reason = $layer.Status
                $result.Code = $script:PreflightBlockerCodes[$layer.Status]
                $result.Action = 'fix-and-rerun'
                $result.RebootRequired = ($layer.Status -eq 'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED')
                return $result
            }
        }
    }

    # Precedence 5.
    if ($dockerHealthy) {
        $result.Overall = 'READY'
        $result.Reason = 'READY'
        $result.Code = 'PREFLIGHT-READY'
        $result.Action = $null
        return $result
    }

    $result.Overall = 'UNKNOWN_BLOCKER'
    $result.Reason = 'DOCKER_UNKNOWN'
    $result.Code = 'PREFLIGHT-UNKNOWN'
    $result.Action = 'report-unknown'
    return $result
}

# ------------------------------------------------------------------- checkpoint

function Get-PreflightCheckpoint {
    <#
      The persisted audit record. Enums, versions and an endpoint KIND only -
      never a hostname, address, user, path or environment-variable value. See
      the privacy section of the design document.
    #>
    param([Parameter(Mandatory)]$Result, [string]$ObservedAt)
    if (-not $ObservedAt) { $ObservedAt = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ') }
    return [pscustomobject]@{
        classifier_version = $script:PreflightClassifierVersion
        overall            = $Result.Overall
        reason             = $Result.Reason
        code               = $Result.Code
        action             = $Result.Action
        reboot_required    = [bool]$Result.RebootRequired
        components         = [pscustomobject]@{
            platform               = $Result.Platform.Status
            firmware               = $Result.Firmware.Status
            windows_virtualization = $Result.WindowsVirtualization.Status
            wsl                    = $Result.Wsl.Status
            docker                 = $Result.Docker.Status
            docker_endpoint_kind   = $Result.Docker.EndpointKind
        }
        # Audit metadata only. Readiness is never derived from this - see
        # Test-PreflightCheckpointIsProof.
        observed_at        = $ObservedAt
    }
}

# --------------------------------------------------------------------- UX copy

function Get-PreflightUserMessage {
    <#
      One blocker, one next action, in the customer's language. No CIM class
      names, no PowerShell, no Docker CLI internals - those belong in the
      checkpoint and the diagnostic summary, not in the normal install flow.
    #>
    param([Parameter(Mandatory)]$Result)

    $resume = 'Your answers and setup progress are saved. AFK AI re-checks your PC and carries on from there.'

    switch ($Result.Code) {
        'PREFLIGHT-READY' {
            return @('Your PC is ready.')
        }
        'PREFLIGHT-UNSUPPORTED-PLATFORM' {
            return @(
                'AFK AI cannot continue on this PC.',
                'This beta supports Windows 11. AFK AI did not find a supported Windows 11 system here.',
                'Next: run AFK AI on a Windows 11 PC. There is nothing to fix on this one.'
            )
        }
        'PREFLIGHT-FIRMWARE-VIRT-DISABLED' {
            return @(
                'AFK AI cannot continue yet.',
                'Hardware virtualization is turned off in your PC firmware, so the local Docker',
                'environment AFK AI needs cannot start.',
                'Next: restart, open your BIOS/UEFI setup screen, turn on Intel VT-x / AMD-V (sometimes',
                'called SVM or Virtualization Technology), save, and let Windows start.',
                'Then run the AFK AI installer again.',
                $resume
            )
        }
        'PREFLIGHT-WINDOWS-FEATURE-MISSING' {
            return @(
                'AFK AI cannot continue yet.',
                'A Windows component that Docker needs is switched off on this PC.',
                'Next: in Windows, open "Turn Windows features on or off", tick "Virtual Machine',
                'Platform", let Windows finish, and restart if it asks you to.',
                'Then run the AFK AI installer again.',
                $resume
            )
        }
        'PREFLIGHT-WINDOWS-REBOOT-REQUIRED' {
            return @(
                'AFK AI cannot continue yet.',
                'Windows has virtualization changes waiting for a restart.',
                'Next: restart Windows, then run the AFK AI installer again.',
                $resume
            )
        }
        'PREFLIGHT-HYPERVISOR-NOT-RUNNING' {
            return @(
                'AFK AI cannot continue yet.',
                'Virtualization is enabled in your firmware, but the Windows virtualization layer is',
                'not running, so Docker has nothing to start on.',
                'Next: restart Windows and try again. If it still does not run, follow Microsoft''s',
                'documented recovery for Windows virtualization, then run the AFK AI installer again.',
                'AFK AI will not change your boot settings for you.',
                $resume
            )
        }
        'PREFLIGHT-WSL-NOT-INSTALLED' {
            return @(
                'AFK AI cannot continue yet.',
                'Docker''s usual Windows setup needs the Windows Subsystem for Linux, which is not',
                'installed on this PC.',
                'Next: install WSL using Microsoft''s supported install path, restart if asked,',
                'then run the AFK AI installer again.',
                $resume
            )
        }
        'PREFLIGHT-WSL-UPDATE-REQUIRED' {
            return @(
                'AFK AI cannot continue yet.',
                'This PC has the Windows Subsystem for Linux, but Docker''s current Windows setup',
                'needs a newer version of it.',
                'Next: update it using Microsoft''s supported update path, then run the AFK AI',
                'installer again.',
                $resume
            )
        }
        'PREFLIGHT-WSL-UNHEALTHY' {
            return @(
                'AFK AI cannot continue yet.',
                'The Windows Subsystem for Linux is installed but did not respond, so AFK AI cannot',
                'confirm Docker will start.',
                'Next: restart Windows, then run the AFK AI installer again.',
                $resume
            )
        }
        'PREFLIGHT-DOCKER-REMOTE-CONTEXT' {
            return @(
                'AFK AI cannot continue yet.',
                'Docker on this PC is currently pointed at another machine, and AFK AI will not set',
                'itself up on someone else''s computer.',
                'Next: switch Docker back to the local Docker Desktop engine (or clear the Docker',
                'host/context override you set), then run the AFK AI installer again.',
                $resume
            )
        }
        'PREFLIGHT-DOCKER-LINUX-ENGINE-REQUIRED' {
            return @(
                'AFK AI cannot continue yet.',
                'Docker Desktop on this PC is running in Windows-container mode. AFK AI''s services',
                'need the Linux mode.',
                'Next: in Docker Desktop, switch to Linux containers, then run the AFK AI installer',
                'again.',
                $resume
            )
        }
        'PREFLIGHT-DOCKER-VERSION-UNSUPPORTED' {
            return @(
                'AFK AI cannot continue yet.',
                'The Docker version on this PC is older than AFK AI supports.',
                'Next: update Docker Desktop, then run the AFK AI installer again.',
                $resume
            )
        }
        'PREFLIGHT-DOCKER-NOT-INSTALLED' {
            return @(
                'AFK AI cannot continue yet.',
                'Docker Desktop is not installed on this PC. AFK AI uses it to run the chat and',
                'search services.',
                'Next: install Docker Desktop, open it once and accept its terms, restart if it asks,',
                'then run the AFK AI installer again.',
                $resume
            )
        }
        'PREFLIGHT-DOCKER-STARTING' {
            return @(
                'AFK AI cannot continue yet.',
                'Docker Desktop is open but has not finished starting.',
                'Next: wait until Docker Desktop reports that it is running, then run the AFK AI',
                'installer again.',
                $resume
            )
        }
        'PREFLIGHT-DOCKER-NOT-RUNNING' {
            return @(
                'AFK AI cannot continue yet.',
                'Docker Desktop is installed on this PC but is not running.',
                'Next: open Docker Desktop from the Start menu, let it finish starting, then run the',
                'AFK AI installer again.',
                $resume
            )
        }
        default {
            return @(
                'AFK AI could not confirm that this PC''s Windows and Docker setup is ready, so it',
                'stopped before downloading anything large.',
                "Reason code: $($Result.Code)",
                'Next: restart Windows and run the AFK AI installer again. If it stops here a second',
                'time, include this reason code in an installation report.',
                $resume
            )
        }
    }
}

function Get-PreflightDiagnosticSummary {
    # Bounded, privacy-safe, for a support report. Enums only - the same fields
    # the checkpoint persists.
    param([Parameter(Mandatory)]$Result)
    return @(
        "code=$($Result.Code)",
        "overall=$($Result.Overall)",
        "platform=$($Result.Platform.Status)",
        "firmware=$($Result.Firmware.Status)",
        "windows-virtualization=$($Result.WindowsVirtualization.Status)",
        "wsl=$($Result.Wsl.Status)",
        "docker=$($Result.Docker.Status)",
        "docker-endpoint=$($Result.Docker.EndpointKind)",
        "classifier=$($Result.ClassifierVersion)"
    )
}

# ----------------------------------------------------------------- live probes
# Read-only. Bounded. No network. Every failure becomes evidence, never an
# exception that escapes to the installer's generic error path.

function Get-PreflightPlatformEvidence {
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        return [pscustomobject]@{
            IsWindows   = $true
            Build       = [int]$os.BuildNumber
            ProductType = [int]$os.ProductType
            Queried     = $true
            Error       = $null
        }
    } catch {
        return [pscustomobject]@{
            IsWindows = $null; Build = $null; ProductType = $null
            Queried = $false; Error = "$($_.Exception.Message)"
        }
    }
}

function Get-PreflightFirmwareEvidence {
    try {
        $procs = @(Get-CimInstance Win32_Processor -ErrorAction Stop)
        return [pscustomobject]@{
            Values  = @($procs | ForEach-Object { $_.VirtualizationFirmwareEnabled })
            Queried = $true
            Error   = $null
        }
    } catch {
        return [pscustomobject]@{ Values = @(); Queried = $false; Error = "$($_.Exception.Message)" }
    }
}

function Get-PreflightPendingRebootEvidence {
    # Non-admin-readable keys only, and deliberately narrow: Component Based
    # Servicing and Windows Update reboot flags are the ones an optional-feature
    # change sets. PendingFileRenameOperations is excluded on purpose - almost
    # any installer sets it, so it would report a reboot on a healthy machine.
    try {
        $keys = @(
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
        )
        foreach ($k in $keys) {
            if (Test-Path -LiteralPath $k -ErrorAction SilentlyContinue) { return $true }
        }
        return $false
    } catch {
        return $null
    }
}

function Get-PreflightWindowsVirtualizationEvidence {
    $features = @{}
    $hypervisor = $null
    $errors = @()
    try {
        $hypervisor = [bool](Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).HypervisorPresent
    } catch {
        $errors += "hypervisor: $($_.Exception.Message)"
    }
    foreach ($name in @('VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux')) {
        try {
            # Read-only DISM query. It needs elevation on client Windows; when
            # that is unavailable we record UNKNOWN rather than elevating the
            # whole installer to answer one question.
            $f = Get-WindowsOptionalFeature -Online -FeatureName $name -ErrorAction Stop
            $features[$name] = "$($f.State)"
        } catch {
            $errors += "$name`: $($_.Exception.Message)"
        }
    }
    return [pscustomobject]@{
        HypervisorPresent = $hypervisor
        Features          = $features
        PendingReboot     = (Get-PreflightPendingRebootEvidence)
        Queried           = ($null -ne $hypervisor -or $features.Count -gt 0)
        Error             = $(if ($errors) { $errors -join '; ' } else { $null })
    }
}

function Get-PreflightWslEvidence {
    $wsl = Get-Command 'wsl.exe' -ErrorAction SilentlyContinue
    if (-not $wsl) {
        return [pscustomobject]@{
            CommandFound = $false; VersionExit = $null; VersionText = ''; TimedOut = $false; Error = $null
        }
    }
    try {
        $r = Invoke-AiProcess -FilePath $wsl.Source -ArgumentList @('--version') -TimeoutSec $script:PreflightWslTimeoutSec
        return [pscustomobject]@{
            CommandFound = $true
            VersionExit  = $r.Code
            # wsl.exe emits UTF-16LE; strip embedded NULs so the digits survive.
            VersionText  = ("$($r.Text)" -replace "`0", '')
            TimedOut     = ($r.Code -eq 124)
            Error        = $null
        }
    } catch {
        return [pscustomobject]@{
            CommandFound = $true; VersionExit = $null; VersionText = ''; TimedOut = $false
            Error = "$($_.Exception.Message)"
        }
    }
}

function Get-PreflightDockerEvidence {
    $docker = Get-Command 'docker.exe' -ErrorAction SilentlyContinue
    $desktopRunning = $false
    try {
        $desktopRunning = @(Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue).Count -gt 0
    } catch {
        $desktopRunning = $false
    }
    if (-not $docker) {
        return [pscustomobject]@{
            CliFound = $false; DesktopProcessRunning = $desktopRunning
            DockerHostEnv = $env:DOCKER_HOST; DockerContextEnv = $env:DOCKER_CONTEXT
            ContextEndpoint = $null; InfoExit = $null; InfoOsType = $null; InfoServerVersion = $null
            InfoText = ''; TimedOut = $false; Error = $null
        }
    }

    $endpoint = $null
    try {
        $ctx = Invoke-AiProcess -FilePath $docker.Source -ArgumentList @('context', 'inspect', '--format', '{{.Endpoints.docker.Host}}') -TimeoutSec $script:PreflightDockerTimeoutSec
        if ($ctx.Code -eq 0) { $endpoint = ("$($ctx.Text)" -split "`r?`n")[0].Trim() }
    } catch {
        $endpoint = $null
    }

    # Go template rather than prose: `docker info` text output is localized and
    # reformatted between versions; these two fields are not.
    $osType = $null; $serverVersion = $null; $exit = $null; $text = ''; $timedOut = $false
    try {
        $info = Invoke-AiProcess -FilePath $docker.Source -ArgumentList @('info', '--format', '{{.OSType}}|{{.ServerVersion}}') -TimeoutSec $script:PreflightDockerTimeoutSec
        $exit = $info.Code
        $timedOut = ($info.Code -eq 124)
        $text = "$($info.Text)"
        if ($exit -eq 0) {
            $parts = (($text -split "`r?`n") | Where-Object { $_ -match '\|' } | Select-Object -First 1) -split '\|'
            if ($parts.Count -ge 1) { $osType = "$($parts[0])".Trim() }
            if ($parts.Count -ge 2) { $serverVersion = "$($parts[1])".Trim() }
        }
    } catch {
        $exit = $null
        $text = "$($_.Exception.Message)"
    }

    return [pscustomobject]@{
        CliFound = $true; DesktopProcessRunning = $desktopRunning
        DockerHostEnv = $env:DOCKER_HOST; DockerContextEnv = $env:DOCKER_CONTEXT
        ContextEndpoint = $endpoint
        InfoExit = $exit; InfoOsType = $osType; InfoServerVersion = $serverVersion
        # Bounded: enough to spot a contradiction, never a whole log.
        InfoText = $(if ($text.Length -gt 600) { $text.Substring(0, 600) } else { $text })
        TimedOut = $timedOut; Error = $null
    }
}

function Get-PreflightEvidence {
    # The whole read-only sweep. Individual probes already swallow their own
    # failures; this wrapper is the last line so an unexpected throw becomes
    # PREFLIGHT-UNKNOWN rather than the installer's generic failure path.
    [CmdletBinding()]
    param()
    try {
        return [pscustomobject]@{
            Platform              = Get-PreflightPlatformEvidence
            Firmware              = Get-PreflightFirmwareEvidence
            WindowsVirtualization = Get-PreflightWindowsVirtualizationEvidence
            Wsl                   = Get-PreflightWslEvidence
            Docker                = Get-PreflightDockerEvidence
        }
    } catch {
        return [pscustomobject]@{ ProbeError = "$($_.Exception.Message)" }
    }
}

function Test-EnvironmentReady {
    # Convenience for callers that only need the yes/no.
    param([Parameter(Mandatory)]$Result)
    return ($Result.Overall -eq 'READY')
}
