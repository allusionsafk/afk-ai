#requires -Version 7.0
<#
  tests/Test-InstallerPreflight.ps1 - regression gate for the installer's
  environment preflight classifier and the versioned installer state.

  Implements the RED test matrix in docs/design/virtualization-docker-preflight.md
  (sections 14.1 classifier, 14.2 resume/state, 14.3 control flow). Every case is
  fixture-driven: nothing here enables a Windows feature, edits BCD, starts
  Docker or WSL, reboots, touches the network, or downloads a model.

  Same hand-rolled shape as tests/Invoke-Checks.ps1 - the repo has no Pester
  dependency and this gate must run unchanged on a clean box and in CI.

  Exit code 0 = all cases passed; 1 = at least one failed.

  Usage:  pwsh -File tests/Test-InstallerPreflight.ps1 [-Verbose]
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'contract-common.ps1')

. (Join-Path $Root 'ai-common.ps1')
. (Join-Path $Root 'installer/installer-common.ps1')
. (Join-Path $Root 'installer/preflight.ps1')

$script:Pass = 0
$script:Fail = 0
$script:Failures = [System.Collections.Generic.List[string]]::new()

function Assert-Equal {
    param(
        [Parameter(Mandatory)][string]$Case,
        $Expected,
        $Actual
    )
    if ("$Expected" -ceq "$Actual") {
        $script:Pass++
        Write-Verbose "PASS  $Case"
    } else {
        $script:Fail++
        $script:Failures.Add("$Case`n        expected: '$Expected'`n        actual:   '$Actual'")
    }
}

function Assert-True {
    param([Parameter(Mandatory)][string]$Case, $Condition, [string]$Detail = '')
    Assert-Equal -Case $Case -Expected 'True' -Actual ([bool]$Condition)
    if (-not $Condition -and $Detail) { $script:Failures[-1] += "`n        detail:   $Detail" }
}

# ---------------------------------------------------------------- fixtures

function New-Evidence {
    # A machine that is fully ready. Individual cases override one layer, so a
    # test only states the thing it is about.
    param([hashtable]$Override = @{})
    $base = @{
        Platform = [pscustomobject]@{
            IsWindows = $true; Build = 26100; ProductType = 1
            Queried = $true; Error = $null
        }
        Firmware = [pscustomobject]@{
            Values = @($true); Queried = $true; Error = $null
        }
        WindowsVirtualization = [pscustomobject]@{
            HypervisorPresent = $true
            Features = @{ 'VirtualMachinePlatform' = 'Enabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' }
            PendingReboot = $false
            Queried = $true; Error = $null
        }
        Wsl = [pscustomobject]@{
            CommandFound = $true; VersionExit = 0; VersionText = 'WSL version: 2.6.1.0'
            TimedOut = $false; Error = $null
        }
        Docker = [pscustomobject]@{
            CliFound = $true; DesktopProcessRunning = $true
            DockerHostEnv = $null; DockerContextEnv = $null
            ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '28.1.1'
            InfoText = ''; TimedOut = $false; Error = $null
        }
    }
    foreach ($k in $Override.Keys) { $base[$k] = $Override[$k] }
    return [pscustomobject]$base
}

function New-WinVirt {
    # Windows-virtualization evidence with an explicit hypervisor state.
    # Firmware cases must state this: a running hypervisor is itself proof that
    # firmware virtualization is on, so "firmware disabled" is only a coherent
    # fixture when no hypervisor is running.
    param($Hypervisor, [hashtable]$Features, $PendingReboot = $false, [bool]$Queried = $true)
    if ($null -eq $Features) {
        $Features = @{ 'VirtualMachinePlatform' = 'Enabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' }
    }
    return [pscustomobject]@{
        HypervisorPresent = $Hypervisor; Features = $Features
        PendingReboot = $PendingReboot; Queried = $Queried; Error = $null
    }
}

function New-DockerAbsent {
    return [pscustomobject]@{
        CliFound = $false; DesktopProcessRunning = $false; DockerHostEnv = $null
        DockerContextEnv = $null; ContextEndpoint = $null; InfoExit = $null; InfoOsType = $null
        InfoServerVersion = $null; InfoText = ''; TimedOut = $false; Error = $null
    }
}

function New-TempStateDir {
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ("afkai-preflight-test-" + [guid]::NewGuid().ToString('n'))
    [void](New-Item -ItemType Directory -Path $dir -Force)
    return $dir
}

# ================================================================ 14.1 classifier

Write-Host '-- classifier: platform' -ForegroundColor Cyan

Assert-Equal -Case 'supported Windows 11 + healthy local Docker Linux engine -> READY' `
    -Expected 'READY' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence)).Overall

Assert-Equal -Case 'Windows 10 build + healthy Docker -> UNSUPPORTED_PLATFORM (not READY)' `
    -Expected 'UNSUPPORTED_PLATFORM' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Platform = [pscustomobject]@{ IsWindows = $true; Build = 19045; ProductType = 1; Queried = $true; Error = $null }
    })).Overall

Assert-Equal -Case 'non-Windows platform -> UNSUPPORTED_PLATFORM' `
    -Expected 'UNSUPPORTED_PLATFORM' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Platform = [pscustomobject]@{ IsWindows = $false; Build = 0; ProductType = 0; Queried = $true; Error = $null }
    })).Overall

Assert-Equal -Case 'platform query failed -> platform UNKNOWN' `
    -Expected 'UNKNOWN' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Platform = [pscustomobject]@{ IsWindows = $null; Build = $null; ProductType = $null; Queried = $false; Error = 'access denied' }
    })).Platform.Status

Write-Host '-- classifier: firmware virtualization' -ForegroundColor Cyan

Assert-Equal -Case 'all firmware signals false -> FIRMWARE_VIRTUALIZATION_DISABLED' `
    -Expected 'FIRMWARE_VIRTUALIZATION_DISABLED' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @($false, $false); Queried = $true; Error = $null }
        WindowsVirtualization = (New-WinVirt -Hypervisor $false)
        Docker = (New-DockerAbsent)
    })).Firmware.Status

Assert-Equal -Case 'firmware processor signals disagree -> firmware UNKNOWN' `
    -Expected 'UNKNOWN' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @($true, $false); Queried = $true; Error = $null }
        WindowsVirtualization = (New-WinVirt -Hypervisor $false)
    })).Firmware.Status

Assert-Equal -Case 'firmware property missing (null) -> UNKNOWN, never guessed disabled' `
    -Expected 'UNKNOWN' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @($null); Queried = $true; Error = $null }
        WindowsVirtualization = (New-WinVirt -Hypervisor $false)
    })).Firmware.Status

Assert-Equal -Case 'firmware CIM access denied -> UNKNOWN' `
    -Expected 'UNKNOWN' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @(); Queried = $false; Error = 'Access is denied' }
        WindowsVirtualization = (New-WinVirt -Hypervisor $false)
    })).Firmware.Status

Assert-Equal -Case 'firmware disabled + no healthy Docker -> RECOVERABLE_BLOCKER' `
    -Expected 'RECOVERABLE_BLOCKER' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @($false); Queried = $true; Error = $null }
        WindowsVirtualization = (New-WinVirt -Hypervisor $false)
        Docker = (New-DockerAbsent)
    })).Overall

Assert-Equal -Case 'firmware UNKNOWN + no healthy Docker -> UNKNOWN_BLOCKER' `
    -Expected 'UNKNOWN_BLOCKER' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @(); Queried = $false; Error = 'rpc unavailable' }
        WindowsVirtualization = (New-WinVirt -Hypervisor $null)
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 1; InfoOsType = $null; InfoServerVersion = $null
            InfoText = 'error during connect'; TimedOut = $false; Error = $null }
    })).Overall

Assert-Equal -Case 'firmware UNKNOWN but local Linux engine proven healthy -> READY' `
    -Expected 'READY' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @(); Queried = $false; Error = 'Access is denied' }
        WindowsVirtualization = (New-WinVirt -Hypervisor $null)
    })).Overall

# Regression: hypervisor masking. Reproduced on a real Windows 11 26200 machine
# (i9-14900HX) running VBS/Credential Guard, where Win32_Processor reports
# VirtualizationFirmwareEnabled=False purely because a hypervisor already owns
# the extensions. Reading that literally sent a fully capable PC to its BIOS.
$masked = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    Firmware = [pscustomobject]@{ Values = @($false); Queried = $true; Error = $null }
    WindowsVirtualization = (New-WinVirt -Hypervisor $true -Features @{})
    Wsl = [pscustomobject]@{ CommandFound = $true; VersionExit = 0; VersionText = 'WSL version: 2.7.3.0'
        TimedOut = $false; Error = $null }
    Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null
        DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
        InfoExit = 1; InfoOsType = $null; InfoServerVersion = $null
        InfoText = 'error during connect: open //./pipe/dockerDesktopLinuxEngine'
        TimedOut = $false; Error = $null }
})
Assert-Equal -Case 'a running hypervisor un-masks a False firmware reading -> firmware READY' `
    -Expected 'READY' -Actual $masked.Firmware.Status
Assert-Equal -Case 'a machine running a hypervisor is never sent to its BIOS' `
    -Expected 'PREFLIGHT-DOCKER-NOT-RUNNING' -Actual $masked.Code
Assert-True -Case 'the un-masked firmware verdict names the hypervisor as its source' `
    -Condition ($masked.Firmware.Source -match 'HypervisorPresent') -Detail $masked.Firmware.Source
Assert-Equal -Case 'a hypervisor also outranks mixed/unreadable firmware signals' `
    -Expected 'READY' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @(); Queried = $false; Error = 'Access is denied' }
        WindowsVirtualization = (New-WinVirt -Hypervisor $true -Features @{})
    })).Firmware.Status

Write-Host '-- classifier: Windows virtualization layer' -ForegroundColor Cyan

Assert-Equal -Case 'firmware ok + VirtualMachinePlatform missing -> WINDOWS_VIRTUALIZATION_FEATURE_MISSING' `
    -Expected 'WINDOWS_VIRTUALIZATION_FEATURE_MISSING' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        WindowsVirtualization = [pscustomobject]@{ HypervisorPresent = $true
            Features = @{ 'VirtualMachinePlatform' = 'Disabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' }
            PendingReboot = $false; Queried = $true; Error = $null }
    })).WindowsVirtualization.Status

$healthyNonWslBackend = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    WindowsVirtualization = [pscustomobject]@{ HypervisorPresent = $true
        Features = @{ 'VirtualMachinePlatform' = 'Enabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Disabled' }
        PendingReboot = $false; Queried = $true; Error = $null }
    Wsl = [pscustomobject]@{ CommandFound = $false; VersionExit = $null; VersionText = ''
        TimedOut = $false; Error = $null }
})
Assert-Equal -Case 'healthy local Linux Docker makes a disabled WSL feature non-blocking' `
    -Expected 'READY' -Actual $healthyNonWslBackend.WindowsVirtualization.Status
Assert-Equal -Case 'healthy local Linux Docker + disabled WSL feature -> overall READY' `
    -Expected 'READY' -Actual $healthyNonWslBackend.Overall

Assert-Equal -Case 'features present + hypervisor not launched -> WINDOWS_HYPERVISOR_NOT_RUNNING' `
    -Expected 'WINDOWS_HYPERVISOR_NOT_RUNNING' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        WindowsVirtualization = [pscustomobject]@{ HypervisorPresent = $false
            Features = @{ 'VirtualMachinePlatform' = 'Enabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' }
            PendingReboot = $false; Queried = $true; Error = $null }
    })).WindowsVirtualization.Status

Assert-Equal -Case 'feature enabled but relevant reboot pending -> WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED' `
    -Expected 'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        WindowsVirtualization = [pscustomobject]@{ HypervisorPresent = $false
            Features = @{ 'VirtualMachinePlatform' = 'Enabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' }
            PendingReboot = $true; Queried = $true; Error = $null }
    })).WindowsVirtualization.Status

Assert-Equal -Case 'reboot-required blocker asks for a Windows restart' `
    -Expected 'True' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        WindowsVirtualization = [pscustomobject]@{ HypervisorPresent = $false
            Features = @{ 'VirtualMachinePlatform' = 'Enabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' }
            PendingReboot = $true; Queried = $true; Error = $null }
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 1; InfoOsType = $null; InfoServerVersion = $null
            InfoText = 'error during connect'; TimedOut = $false; Error = $null }
    })).RebootRequired

Assert-Equal -Case 'Windows feature query unavailable -> layer UNKNOWN' `
    -Expected 'UNKNOWN' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        WindowsVirtualization = [pscustomobject]@{ HypervisorPresent = $null; Features = @{}
            PendingReboot = $null; Queried = $false; Error = 'DISM unavailable' }
    })).WindowsVirtualization.Status

$unknownFeaturesWithoutHypervisor = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    WindowsVirtualization = [pscustomobject]@{ HypervisorPresent = $false; Features = @{}
        PendingReboot = $null; Queried = $true; Error = 'No usable optional-feature evidence' }
    Docker = (New-DockerAbsent)
})
Assert-Equal -Case 'empty feature evidence + HypervisorPresent false -> Windows virtualization UNKNOWN' `
    -Expected 'UNKNOWN' -Actual $unknownFeaturesWithoutHypervisor.WindowsVirtualization.Status
Assert-Equal -Case 'empty feature evidence + HypervisorPresent false + unhealthy Docker -> UNKNOWN_BLOCKER' `
    -Expected 'UNKNOWN_BLOCKER' -Actual $unknownFeaturesWithoutHypervisor.Overall
Assert-Equal -Case 'empty feature evidence preserves the Windows virtualization UNKNOWN reason' `
    -Expected 'WINDOWS_VIRTUALIZATION_UNKNOWN' -Actual $unknownFeaturesWithoutHypervisor.Reason
Assert-Equal -Case 'empty feature evidence preserves the stable UNKNOWN reason code' `
    -Expected 'PREFLIGHT-UNKNOWN' -Actual $unknownFeaturesWithoutHypervisor.Code

Assert-Equal -Case 'Windows layer UNKNOWN but healthy local engine proves the backend -> READY' `
    -Expected 'READY' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        WindowsVirtualization = [pscustomobject]@{ HypervisorPresent = $null; Features = @{}
            PendingReboot = $null; Queried = $false; Error = 'DISM unavailable' }
    })).Overall

Write-Host '-- classifier: WSL' -ForegroundColor Cyan

Assert-Equal -Case 'WSL absent on a new-install path -> WSL_NOT_INSTALLED' `
    -Expected 'WSL_NOT_INSTALLED' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Wsl = [pscustomobject]@{ CommandFound = $false; VersionExit = $null; VersionText = ''
            TimedOut = $false; Error = $null }
        Docker = [pscustomobject]@{ CliFound = $false; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = $null; InfoExit = $null; InfoOsType = $null
            InfoServerVersion = $null; InfoText = ''; TimedOut = $false; Error = $null }
    })).Wsl.Status

Assert-Equal -Case 'WSL below Docker WSL-backend minimum -> WSL_UPDATE_REQUIRED' `
    -Expected 'WSL_UPDATE_REQUIRED' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Wsl = [pscustomobject]@{ CommandFound = $true; VersionExit = 0; VersionText = 'WSL version: 2.0.9.0'
            TimedOut = $false; Error = $null }
        Docker = [pscustomobject]@{ CliFound = $false; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = $null; InfoExit = $null; InfoOsType = $null
            InfoServerVersion = $null; InfoText = ''; TimedOut = $false; Error = $null }
    })).Wsl.Status

Assert-Equal -Case 'WSL at the documented minimum is READY' `
    -Expected 'READY' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Wsl = [pscustomobject]@{ CommandFound = $true; VersionExit = 0; VersionText = 'WSL version: 2.1.5.0'
            TimedOut = $false; Error = $null }
    })).Wsl.Status

Assert-Equal -Case 'localized WSL output classifies identically to English' `
    -Expected 'READY' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Wsl = [pscustomobject]@{ CommandFound = $true; VersionExit = 0
            VersionText = "WSL-Version: 2.6.1.0`nKernelversion: 6.6.87.2-1"
            TimedOut = $false; Error = $null }
    })).Wsl.Status

Assert-Equal -Case 'WSL command hangs -> WSL_UNHEALTHY, never READY' `
    -Expected 'WSL_UNHEALTHY' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Wsl = [pscustomobject]@{ CommandFound = $true; VersionExit = 124; VersionText = 'Timed out after 20s: wsl.exe --version'
            TimedOut = $true; Error = $null }
        Docker = [pscustomobject]@{ CliFound = $false; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = $null; InfoExit = $null; InfoOsType = $null
            InfoServerVersion = $null; InfoText = ''; TimedOut = $false; Error = $null }
    })).Wsl.Status

Assert-Equal -Case 'inbox WSL without --version support -> WSL_UPDATE_REQUIRED, not UNKNOWN' `
    -Expected 'WSL_UPDATE_REQUIRED' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Wsl = [pscustomobject]@{ CommandFound = $true; VersionExit = 1
            VersionText = 'Invalid command line option: --version'
            TimedOut = $false; Error = $null }
        Docker = [pscustomobject]@{ CliFound = $false; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = $null; InfoExit = $null; InfoOsType = $null
            InfoServerVersion = $null; InfoText = ''; TimedOut = $false; Error = $null }
    })).Wsl.Status

Assert-Equal -Case 'healthy local Docker + WSL absent -> WSL NOT_REQUIRED_FOR_CURRENT_HEALTHY_BACKEND' `
    -Expected 'NOT_REQUIRED_FOR_CURRENT_HEALTHY_BACKEND' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Wsl = [pscustomobject]@{ CommandFound = $false; VersionExit = $null; VersionText = ''
            TimedOut = $false; Error = $null }
    })).Wsl.Status

Assert-Equal -Case 'healthy local Docker + WSL absent -> overall READY' `
    -Expected 'READY' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Wsl = [pscustomobject]@{ CommandFound = $false; VersionExit = $null; VersionText = ''
            TimedOut = $false; Error = $null }
    })).Overall

Write-Host '-- classifier: Docker' -ForegroundColor Cyan

Assert-Equal -Case 'Docker absent -> DOCKER_NOT_INSTALLED' `
    -Expected 'DOCKER_NOT_INSTALLED' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $false; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = $null; InfoExit = $null; InfoOsType = $null
            InfoServerVersion = $null; InfoText = ''; TimedOut = $false; Error = $null }
    })).Docker.Status

Assert-Equal -Case 'docker.exe exists but local engine pipe unavailable -> not READY' `
    -Expected 'DOCKER_INSTALLED_NOT_RUNNING' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 1; InfoOsType = $null; InfoServerVersion = $null
            InfoText = 'error during connect: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.'
            TimedOut = $false; Error = $null }
    })).Docker.Status

Assert-Equal -Case 'Docker Desktop process running but engine not ready -> DOCKER_STARTING' `
    -Expected 'DOCKER_STARTING' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $true; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 1; InfoOsType = $null; InfoServerVersion = $null
            InfoText = 'error during connect'; TimedOut = $false; Error = $null }
    })).Docker.Status

$sshContext = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null
        DockerContextEnv = 'prod'; ContextEndpoint = 'ssh://deploy@build-server'
        InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '28.1.1'
        InfoText = ''; TimedOut = $false; Error = $null }
})
Assert-Equal -Case 'docker info succeeds through a remote SSH context -> DOCKER_CONTEXT_REMOTE' `
    -Expected 'DOCKER_CONTEXT_REMOTE' -Actual $sshContext.Docker.Status
Assert-Equal -Case 'an ssh context classifies as the remote-ssh endpoint kind' `
    -Expected 'remote-ssh' -Actual $sshContext.Docker.EndpointKind

Assert-Equal -Case 'DOCKER_HOST points at a remote TCP daemon -> DOCKER_CONTEXT_REMOTE' `
    -Expected 'DOCKER_CONTEXT_REMOTE' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $true
            DockerHostEnv = 'tcp://10.0.0.5:2375'; DockerContextEnv = $null
            ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '28.1.1'
            InfoText = ''; TimedOut = $false; Error = $null }
    })).Docker.Status

Assert-Equal -Case 'remote Docker never satisfies the gate -> RECOVERABLE_BLOCKER' `
    -Expected 'RECOVERABLE_BLOCKER' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = 'prod'; ContextEndpoint = 'ssh://deploy@build-server'
            InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '28.1.1'
            InfoText = ''; TimedOut = $false; Error = $null }
    })).Overall

Assert-Equal -Case 'DOCKER_HOST on loopback TCP is local, not remote' `
    -Expected 'DOCKER_HEALTHY_LOCAL' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $true
            DockerHostEnv = 'tcp://127.0.0.1:2375'; DockerContextEnv = $null
            ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '28.1.1'
            InfoText = ''; TimedOut = $false; Error = $null }
    })).Docker.Status

Assert-Equal -Case 'local engine in Windows-container mode -> DOCKER_LINUX_ENGINE_REQUIRED' `
    -Expected 'DOCKER_LINUX_ENGINE_REQUIRED' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $true; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/docker_engine_windows'
            InfoExit = 0; InfoOsType = 'windows'; InfoServerVersion = '28.1.1'
            InfoText = ''; TimedOut = $false; Error = $null }
    })).Docker.Status

Assert-Equal -Case 'docker info times out -> Docker UNKNOWN, never healthy' `
    -Expected 'UNKNOWN' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 124; InfoOsType = $null; InfoServerVersion = $null
            InfoText = 'Timed out after 30s: docker info'; TimedOut = $true; Error = $null }
    })).Docker.Status

Assert-Equal -Case 'no project Docker minimum configured -> version alone does not block' `
    -Expected 'DOCKER_HEALTHY_LOCAL' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $true; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '20.10.0'
            InfoText = ''; TimedOut = $false; Error = $null }
    })).Docker.Status

Assert-Equal -Case 'Docker version below an explicitly configured minimum -> DOCKER_VERSION_UNSUPPORTED' `
    -Expected 'DOCKER_VERSION_UNSUPPORTED' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $true; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '20.10.0'
            InfoText = ''; TimedOut = $false; Error = $null }
    }) -MinimumDockerVersion '24.0.0').Docker.Status

Write-Host '-- classifier: conflicting evidence and reason codes' -ForegroundColor Cyan

Assert-Equal -Case 'Docker virtualization error conflicting with Windows evidence -> UNKNOWN_BLOCKER' `
    -Expected 'UNKNOWN_BLOCKER' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @($true); Queried = $true; Error = $null }
        WindowsVirtualization = [pscustomobject]@{ HypervisorPresent = $true
            Features = @{ 'VirtualMachinePlatform' = 'Enabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' }
            PendingReboot = $false; Queried = $true; Error = $null }
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 1; InfoOsType = $null; InfoServerVersion = $null
            InfoText = 'Virtualization support not detected'; TimedOut = $false; Error = $null }
    })).Overall

Assert-Equal -Case 'READY carries the PREFLIGHT-READY code' `
    -Expected 'PREFLIGHT-READY' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence)).Code

Assert-Equal -Case 'firmware-disabled blocker carries its stable reason code' `
    -Expected 'PREFLIGHT-FIRMWARE-VIRT-DISABLED' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @($false); Queried = $true; Error = $null }
        WindowsVirtualization = (New-WinVirt -Hypervisor $false)
        Docker = (New-DockerAbsent)
    })).Code

Assert-Equal -Case 'remote-context blocker carries its stable reason code' `
    -Expected 'PREFLIGHT-DOCKER-REMOTE-CONTEXT' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null
            DockerContextEnv = 'prod'; ContextEndpoint = 'ssh://deploy@build-server'
            InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '28.1.1'
            InfoText = ''; TimedOut = $false; Error = $null }
    })).Code

Assert-Equal -Case 'unsupported platform carries its stable reason code' `
    -Expected 'PREFLIGHT-UNSUPPORTED-PLATFORM' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Platform = [pscustomobject]@{ IsWindows = $true; Build = 19045; ProductType = 1; Queried = $true; Error = $null }
    })).Code

Assert-Equal -Case 'firmware disabled outranks a merely-absent Docker in the reported action' `
    -Expected 'PREFLIGHT-FIRMWARE-VIRT-DISABLED' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Firmware = [pscustomobject]@{ Values = @($false); Queried = $true; Error = $null }
        WindowsVirtualization = (New-WinVirt -Hypervisor $false)
        Wsl = [pscustomobject]@{ CommandFound = $false; VersionExit = $null; VersionText = ''; TimedOut = $false; Error = $null }
        Docker = (New-DockerAbsent)
    })).Code

Write-Host '-- classifier: robustness and UX' -ForegroundColor Cyan

Assert-Equal -Case 'entirely missing evidence object does not throw; yields UNKNOWN_BLOCKER' `
    -Expected 'UNKNOWN_BLOCKER' -Actual (Invoke-EnvironmentPreflight -Evidence ([pscustomobject]@{})).Overall

Assert-Equal -Case 'a probe that threw is reported as PREFLIGHT-UNKNOWN, not a generic failure' `
    -Expected 'PREFLIGHT-UNKNOWN' -Actual (Invoke-EnvironmentPreflight -Evidence ([pscustomobject]@{})).Code

Assert-True -Case 'repeated classification of identical evidence is deterministic' `
    -Condition ((Invoke-EnvironmentPreflight -Evidence (New-Evidence)).Code -eq
                (Invoke-EnvironmentPreflight -Evidence (New-Evidence)).Code)

$copy = Get-PreflightUserMessage -Result (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    Firmware = [pscustomobject]@{ Values = @($false); Queried = $true; Error = $null }
    WindowsVirtualization = (New-WinVirt -Hypervisor $false)
    Docker = (New-DockerAbsent)
}))
Assert-True -Case 'customer copy names the blocker in plain language' `
    -Condition (($copy -join ' ') -match '(?i)virtualization is (turned off|disabled)') -Detail ($copy -join ' | ')
Assert-True -Case 'customer copy gives one next action' `
    -Condition (($copy -join ' ') -match '(?i)^.*next:') -Detail ($copy -join ' | ')
Assert-True -Case 'customer copy says rerunning is safe' `
    -Condition (($copy -join ' ') -match '(?i)again') -Detail ($copy -join ' | ')
Assert-True -Case 'customer copy leaks no CIM/PowerShell/Docker internals' `
    -Condition (($copy -join ' ') -notmatch '(?i)Win32_|Get-CimInstance|npipe:|docker info|DOCKER_HOST|OSType') `
    -Detail ($copy -join ' | ')

$unknownCopy = Get-PreflightUserMessage -Result (Invoke-EnvironmentPreflight -Evidence ([pscustomobject]@{}))
Assert-True -Case 'UNKNOWN copy still surfaces the stable reason code for a support report' `
    -Condition (($unknownCopy -join ' ') -match 'PREFLIGHT-UNKNOWN') -Detail ($unknownCopy -join ' | ')

Write-Host '-- classifier: privacy of persisted evidence' -ForegroundColor Cyan

$remote = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $false
        DockerHostEnv = 'tcp://build.internal.example.com:2376'; DockerContextEnv = 'prod'
        ContextEndpoint = 'ssh://deploy@build-server'
        InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '28.1.1'
        InfoText = ''; TimedOut = $false; Error = $null }
})
$checkpoint = ConvertTo-Json (Get-PreflightCheckpoint -Result $remote) -Depth 8
Assert-True -Case 'checkpoint records only the endpoint KIND, never the remote address' `
    -Condition ($checkpoint -notmatch 'build-server|build\.internal|deploy@|2376') -Detail $checkpoint
# DOCKER_HOST outranks the selected context - that is where the CLI actually
# points - so the kind reported is the DOCKER_HOST one, not the context's.
Assert-Equal -Case 'DOCKER_HOST outranks the context when classifying the endpoint kind' `
    -Expected 'remote-tcp' -Actual $remote.Docker.EndpointKind
Assert-Equal -Case 'checkpoint carries the current classifier version for later migration' `
    -Expected 2 -Actual (Get-PreflightCheckpoint -Result $remote).classifier_version

Assert-Equal -Case 'loopback TCP host classifies as a local endpoint kind' `
    -Expected 'local-tcp' -Actual (Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
        Docker = [pscustomobject]@{ CliFound = $true; DesktopProcessRunning = $true
            DockerHostEnv = 'tcp://localhost:2375'; DockerContextEnv = $null
            ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            InfoExit = 0; InfoOsType = 'linux'; InfoServerVersion = '28.1.1'
            InfoText = ''; TimedOut = $false; Error = $null }
    })).Docker.EndpointKind

# ================================================================ 14.2 state

Write-Host '-- state: versioning, migration and atomic replacement' -ForegroundColor Cyan

$dir = New-TempStateDir
try {
    $statePath = Join-Path $dir 'installer-state.json'

    # v1 -> v2 conservative migration.
    @'
{"version":1,"phases_done":["vet","intent"],"hardware":{"tier":"B"},"intent":["chat"],"models":{},"pending_reboot":true}
'@ | Set-Content -LiteralPath $statePath -Encoding UTF8
    $migrated = Import-InstallerState -Path $statePath
    Assert-Equal -Case 'v1 state migrates to the current schema version' -Expected 2 -Actual $migrated.version
    Assert-Equal -Case 'v1 migration preserves completed phases' -Expected 'vet intent' -Actual (@($migrated.phases_done) -join ' ')
    Assert-Equal -Case 'v1 migration preserves the vetted hardware tier' -Expected 'B' -Actual $migrated.hardware.tier
    Assert-Equal -Case 'v1 boolean pending_reboot becomes a structured required flag' `
        -Expected 'True' -Actual $migrated.pending_reboot.required
    Assert-Equal -Case 'migrated pending_reboot records a reason rather than a bare boolean' `
        -Expected 'legacy-docker-desktop-setup' -Actual $migrated.pending_reboot.reason

    # Round trip.
    Save-InstallerState -State $migrated -Path $statePath
    $again = Import-InstallerState -Path $statePath
    Assert-Equal -Case 'v2 state round-trips without losing the reboot reason' `
        -Expected 'legacy-docker-desktop-setup' -Actual $again.pending_reboot.reason

    # Corrupt state must be quarantined, not fatal.
    'not json at all {{{' | Set-Content -LiteralPath $statePath -Encoding UTF8
    $recovered = $null
    $threw = $false
    try { $recovered = Import-InstallerState -Path $statePath } catch { $threw = $true }
    Assert-Equal -Case 'corrupt state does not throw at the user' -Expected 'False' -Actual $threw
    Assert-Equal -Case 'corrupt state recovers to a fresh current-version skeleton' -Expected 2 -Actual $recovered.version
    Assert-True -Case 'corrupt state is quarantined next to the original, not silently deleted' `
        -Condition (@(Get-ChildItem -LiteralPath $dir -Filter 'installer-state.corrupt-*.json').Count -ge 1) `
        -Detail ((Get-ChildItem -LiteralPath $dir | ForEach-Object Name) -join ', ')

    # An unsupported FUTURE schema must fail closed, not be reinterpreted.
    '{"version":99,"phases_done":["vet"]}' | Set-Content -LiteralPath $statePath -Encoding UTF8
    $futureErr = $null
    try { [void](Import-InstallerState -Path $statePath) } catch { $futureErr = "$($_.Exception.Message)" }
    Assert-True -Case 'a future state schema is refused with a stable compatibility message' `
        -Condition ($futureErr -and $futureErr -match '(?i)newer version of AFK AI') -Detail "$futureErr"

    # Atomic replacement: an abandoned temp file must never become the state, and
    # the previous complete state must survive a failed write.
    $statePath2 = Join-Path $dir 'atomic-state.json'
    $good = New-InstallerState
    $good.phases_done = @('vet')
    Save-InstallerState -State $good -Path $statePath2
    "$([guid]::NewGuid())" | Set-Content -LiteralPath (Join-Path $dir 'atomic-state.json.tmp-abandoned') -Encoding UTF8
    $afterAbandoned = Import-InstallerState -Path $statePath2
    Assert-Equal -Case 'an abandoned temp file from an interrupted run is ignored' `
        -Expected 'vet' -Actual (@($afterAbandoned.phases_done) -join ' ')
    Assert-True -Case 'the state write leaves no temp file of its own behind' `
        -Condition (@(Get-ChildItem -LiteralPath $dir -Filter 'atomic-state.json.tmp-*' |
            Where-Object { $_.Name -ne 'atomic-state.json.tmp-abandoned' }).Count -eq 0)

    # The installed bootstrapper deliberately invokes inbox Windows PowerShell
    # 5.1 on a clean Windows 11 machine. Exercise the actual runtime here: newer
    # .NET overloads available to pwsh are not sufficient evidence of support.
    $windowsPowerShell = Get-Command 'powershell.exe' -ErrorAction SilentlyContinue
    if ($windowsPowerShell) {
        $compatState = Join-Path $dir 'windows-powershell-state.json'
        $commonPath = (Join-Path $Root 'installer/installer-common.ps1').Replace("'", "''")
        $compatPath = $compatState.Replace("'", "''")
        $compatScript = @"
`$ErrorActionPreference = 'Stop'
. '$commonPath'
`$state = New-InstallerState
Save-InstallerState -State `$state -Path '$compatPath'
`$state.intent = @('chat')
Save-InstallerState -State `$state -Path '$compatPath'
"@
        $compatOutput = @(& $windowsPowerShell.Source -NoLogo -NoProfile -NonInteractive `
            -ExecutionPolicy Bypass -Command $compatScript 2>&1)
        $compatExit = $LASTEXITCODE
        Assert-Equal -Case 'state checkpoint replacement works in inbox Windows PowerShell 5.1' `
            -Expected 0 -Actual $compatExit
        if ($compatExit -ne 0) {
            $script:Failures[-1] += "`n        output:   $($compatOutput -join ' | ')"
        }
    }

    # A write that cannot be verified must leave the previous state intact.
    $before = Get-ContractText -Path $statePath2
    $bad = [pscustomobject]@{ version = 2; phases_done = @('vet', 'intent'); self = $null }
    $bad.self = $bad     # cyclic: ConvertTo-Json -Depth cannot serialize this faithfully
    $writeThrew = $false
    try { Save-InstallerState -State $bad -Path $statePath2 } catch { $writeThrew = $true }
    Assert-True -Case 'a state write that cannot be validated is rejected' -Condition $writeThrew
    Assert-Equal -Case 'the previous complete state survives a rejected write' `
        -Expected $before.Trim() -Actual ((Get-ContractText -Path $statePath2).Trim())

    # A stale READY checkpoint must never be treated as current readiness.
    $stale = New-InstallerState
    $stale.preflight = [pscustomobject]@{
        classifier_version = 1; overall = 'READY'; reason = 'PREFLIGHT-READY'
        action = $null; observed_at = '2020-01-01T00:00:00Z'
    }
    Assert-Equal -Case 'a persisted READY checkpoint is never proof of current readiness' `
        -Expected 'False' -Actual (Test-PreflightCheckpointIsProof -State $stale)

    # System clock changes must not create or remove readiness.
    $future = New-InstallerState
    $future.preflight = [pscustomobject]@{
        classifier_version = 1; overall = 'READY'; reason = 'PREFLIGHT-READY'
        action = $null; observed_at = '2999-01-01T00:00:00Z'
    }
    Assert-Equal -Case 'a future-dated checkpoint is equally not proof (clock changes are inert)' `
        -Expected 'False' -Actual (Test-PreflightCheckpointIsProof -State $future)

    # The safety-critical phases must never be recorded as skippable.
    $done = New-InstallerState
    $done.phases_done = @('environment-preflight', 'environment-ready', 'vet')
    Assert-Equal -Case 'environment-preflight is never skipped even if state claims it is done' `
        -Expected 'False' -Actual (Test-PhaseDone -State $done -Phase 'environment-preflight')
    Assert-Equal -Case 'environment-ready is never skipped even if state claims it is done' `
        -Expected 'False' -Actual (Test-PhaseDone -State $done -Phase 'environment-ready')
    Assert-Equal -Case 'ordinary completed phases are still skipped on resume' `
        -Expected 'True' -Actual (Test-PhaseDone -State $done -Phase 'vet')
    Assert-Equal -Case 'a safety-critical phase is never written into phases_done' `
        -Expected 'False' -Actual (
            (Set-PhaseDone -State (New-InstallerState) -Phase 'environment-ready' -Path (Join-Path $dir 'never.json')) -or
            (Test-Path -LiteralPath (Join-Path $dir 'never.json'))
        )
} finally {
    Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
}

# ================================================================ 14.3 control flow

Write-Host '-- control flow: the orchestrator ordering invariant' -ForegroundColor Cyan

$orchestrator = Join-Path $Root 'installer/Install-LocalAI.ps1'
$src = Get-ContractText -Path $orchestrator

# Read the declared phase order out of the real $Phases array rather than
# trusting a comment: the invariant is about what actually runs.
$phaseNames = @(
    [regex]::Matches($src, "(?m)^\s*@\{\s*Name\s*=\s*'([a-z\-]+)'") | ForEach-Object { $_.Groups[1].Value }
)
Assert-True -Case 'the orchestrator declares a readable phase order' -Condition ($phaseNames.Count -ge 12) `
    -Detail ($phaseNames -join ' -> ')

$idx = @{}
for ($i = 0; $i -lt $phaseNames.Count; $i++) { $idx[$phaseNames[$i]] = $i }

Assert-Equal -Case 'environment-preflight is the first phase' -Expected 'environment-preflight' -Actual $phaseNames[0]
foreach ($later in @('python', 'pip', 'scout', 'pulls', 'compose')) {
    Assert-True -Case "environment-preflight runs before $later" `
        -Condition ($idx.ContainsKey($later) -and $idx['environment-preflight'] -lt $idx[$later]) `
        -Detail ($phaseNames -join ' -> ')
}
Assert-True -Case 'environment-ready gates the model pull' `
    -Condition ($idx.ContainsKey('environment-ready') -and $idx.ContainsKey('pulls') -and
                $idx['environment-ready'] -lt $idx['pulls']) -Detail ($phaseNames -join ' -> ')
Assert-True -Case 'environment-ready runs before the Python product setup' `
    -Condition ($idx['environment-ready'] -lt $idx['python']) -Detail ($phaseNames -join ' -> ')

# The planned action-required exit must stay 10: already-distributed pinned
# copies of "Install Local AI.cmd" treat >=11 as an unexpected failure.
Assert-True -Case 'the planned action-required pause still exits 10 (pinned .cmd contract)' `
    -Condition ($src -match '(?m)^\s*exit\s+10\b') -Detail 'no `exit 10` found in the orchestrator'
Assert-True -Case 'no new planned-pause exit code above the pinned .cmd failure threshold' `
    -Condition (-not ([regex]::Matches($src, '(?m)^\s*exit\s+(\d+)') |
        Where-Object { [int]$_.Groups[1].Value -gt 10 -and [int]$_.Groups[1].Value -lt 100 })) `
    -Detail 'an exit code >10 would render as "Something went wrong" on already-downloaded installers'

$cmd = Get-ContractText -Path (Join-Path $Root 'Install Local AI.cmd')
Assert-True -Case 'the outer .cmd still routes errorlevel 10 to the planned pause' `
    -Condition ($cmd -match 'errorlevel 10') -Detail 'exit-code contract broken'
Assert-True -Case 'the outer .cmd planned-pause copy is no longer Docker-only' `
    -Condition ($cmd -match '(?i)steps? above' -or $cmd -match '(?i)read the steps') `
    -Detail 'the generalized action-required pause must defer to the orchestrator message'

# The expensive phases must be guarded by the readiness gate, not by phases_done.
Assert-True -Case 'the pull phase asserts live readiness before downloading a model' `
    -Condition ($src -match 'Assert-EnvironmentReady' ) `
    -Detail 'Invoke-PhasePulls must not rely on a persisted checkpoint'
Assert-True -Case 'the preflight module is dot-sourced by the orchestrator' `
    -Condition ($src -match "preflight\.ps1")

# No probe may run without a bounded timeout, and none may mutate the machine.
$module = Get-ContractText -Path (Join-Path $Root 'installer/preflight.ps1')
# Real call sites only - a comment mentioning the helper is not a probe.
$invocations = @(Get-Content -LiteralPath (Join-Path $Root 'installer/preflight.ps1') |
    Where-Object { $_ -match 'Invoke-AiProcess\s+-FilePath' })
Assert-True -Case 'the probe layer actually issues external commands through the bounded helper' `
    -Condition ($invocations.Count -ge 3) -Detail "found $($invocations.Count)"
Assert-True -Case 'every external probe command passes an explicit timeout' `
    -Condition (@($invocations | Where-Object { $_ -notmatch '-TimeoutSec' }).Count -eq 0) `
    -Detail (@($invocations | Where-Object { $_ -notmatch '-TimeoutSec' }) -join ' ; ')
foreach ($forbidden in @('Enable-WindowsOptionalFeature', 'Disable-WindowsOptionalFeature', 'bcdedit',
        'Set-Service', 'Start-Service', 'Stop-Service', 'Restart-Computer', 'wsl.*--install',
        'wsl.*--update', 'Set-MpPreference', 'Set-ExecutionPolicy')) {
    Assert-True -Case "the read-only probe layer never calls $forbidden" `
        -Condition ($module -notmatch $forbidden) -Detail 'mutation found in the read-only preflight module'
}
Assert-True -Case 'the probe layer performs no network I/O' `
    -Condition ($module -notmatch 'Invoke-WebRequest|Invoke-RestMethod|System\.Net\.WebClient|HttpClient')
Assert-True -Case 'the probe layer never requires elevation' `
    -Condition ($module -notmatch '(?i)-Verb\s+RunAs|RequireAdministrator')
Assert-True -Case 'no Docker Hub sign-in appears anywhere in the readiness contract' `
    -Condition ($module -notmatch '(?i)docker\s+login|hub\.docker\.com')

# The installed app owns mutable state outside its immutable payload. A caller
# may override the root for lifecycle tests, but the default must be LocalAppData.
Assert-True -Case 'orchestrator accepts an explicit data root' `
    -Condition ($src -match '(?m)^\s*\[string\]\$DataRoot\s*=') `
    -Detail 'Install-LocalAI.ps1 has no DataRoot parameter'
Assert-True -Case 'orchestrator stores resume state under the data root' `
    -Condition ($src -match 'Get-InstallerStatePath\s+-Root\s+\$DataRoot') `
    -Detail 'installer state is still stored beside program files'
Assert-True -Case 'default data root uses LocalAppData AFK LocalAI State' `
    -Condition ($src -match [regex]::Escape("Join-Path `$env:LOCALAPPDATA 'AFK LocalAI\State'"))

$jsonEntry = Join-Path $Root 'installer/Get-Preflight.ps1'
Assert-True -Case 'shell-facing JSON preflight entry point exists' `
    -Condition (Test-Path -LiteralPath $jsonEntry -PathType Leaf)
if (Test-Path -LiteralPath $jsonEntry -PathType Leaf) {
    $jsonEntryText = Get-ContractText -Path $jsonEntry
    Assert-True -Case 'JSON entry point emits compressed machine-readable output' `
        -Condition ($jsonEntryText -match 'ConvertTo-Json.+-Compress')
    Assert-True -Case 'JSON entry point persists a bounded checkpoint' `
        -Condition ($jsonEntryText -match 'Get-PreflightCheckpoint' -and
                    $jsonEntryText -match 'Save-InstallerState')
}


# --------------------------------------------------------- layer precedence
# Regression from a real installer run on this machine. The persisted checkpoint
# recorded firmware READY, windows_virtualization READY, wsl WSL_UPDATE_REQUIRED
# and docker UNKNOWN - and the classifier reported PREFLIGHT-UNKNOWN /
# report-unknown, hiding a conclusive, independently established, safely
# recoverable WSL step behind an unrelated Docker unknown.
#
# The prerequisite layers form a stack: firmware -> Windows virtualization ->
# WSL -> Docker. A conclusive blocker at a layer is only trustworthy when every
# layer BENEATH it is conclusively known, because its recovery depends on those.
# An UNKNOWN ABOVE it says nothing about whether that recovery is safe, so it
# must not suppress it.

function New-DockerEndpointUnknown {
    # Exactly what the real run recorded: docker_endpoint_kind "unknown",
    # so the engine's own state could not be established at all.
    return [pscustomobject]@{
        CliFound = $true; DesktopProcessRunning = $false; DockerHostEnv = $null
        DockerContextEnv = $null; ContextEndpoint = $null
        InfoExit = $null; InfoOsType = $null; InfoServerVersion = $null
        InfoText = ''; TimedOut = $false; Error = $null
    }
}

function New-WslOutdated {
    return [pscustomobject]@{
        CommandFound = $true; VersionExit = 0; VersionText = 'WSL version: 1.2.5.0'
        TimedOut = $false; Error = $null
    }
}

$realRun = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    Wsl = (New-WslOutdated)
    Docker = (New-DockerEndpointUnknown)
})

Assert-Equal -Case 'real run: conclusive WSL blocker is not hidden by an unrelated Docker UNKNOWN' `
    -Expected 'RECOVERABLE_BLOCKER' -Actual $realRun.Overall
Assert-Equal -Case 'real run: reason names the WSL layer' `
    -Expected 'WSL_UPDATE_REQUIRED' -Actual $realRun.Reason
Assert-Equal -Case 'real run: code stays the stable WSL code' `
    -Expected 'PREFLIGHT-WSL-UPDATE-REQUIRED' -Actual $realRun.Code
Assert-Equal -Case 'real run: the user is offered a bounded action' `
    -Expected 'fix-and-rerun' -Actual $realRun.Action
Assert-Equal -Case 'real run: the Docker layer is still reported UNKNOWN, not invented' `
    -Expected 'UNKNOWN' -Actual $realRun.Docker.Status

# An unknown BENEATH the blocker still wins: the blocker's own prerequisites are
# unproven, so its recovery is not established as useful.
$unknownBelow = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    Firmware = [pscustomobject]@{ Values = @(); Queried = $false; Error = 'rpc unavailable' }
    WindowsVirtualization = (New-WinVirt -Hypervisor $null)
    Wsl = (New-WslOutdated)
    Docker = (New-DockerEndpointUnknown)
})
Assert-Equal -Case 'an UNKNOWN beneath a blocker still outranks it' `
    -Expected 'UNKNOWN_BLOCKER' -Actual $unknownBelow.Overall
Assert-Equal -Case 'and it names the lowest unknown layer' `
    -Expected 'FIRMWARE_UNKNOWN' -Actual $unknownBelow.Reason

# The lowest conclusive blocker wins over a higher conclusive blocker.
$twoBlockers = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    WindowsVirtualization = (New-WinVirt -Hypervisor $true -Features @{
        'VirtualMachinePlatform' = 'Disabled'; 'Microsoft-Windows-Subsystem-Linux' = 'Enabled' })
    Wsl = (New-WslOutdated)
    Docker = (New-DockerAbsent)
})
Assert-Equal -Case 'the lowest conclusive blocker is reported first' `
    -Expected 'WINDOWS_VIRTUALIZATION_FEATURE_MISSING' -Actual $twoBlockers.Reason

# Docker UNKNOWN with every layer beneath it READY is still honestly UNKNOWN:
# there is no lower blocker to surface instead.
$dockerOnly = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    Docker = (New-DockerEndpointUnknown)
})
Assert-Equal -Case 'Docker UNKNOWN alone remains UNKNOWN_BLOCKER' `
    -Expected 'UNKNOWN_BLOCKER' -Actual $dockerOnly.Overall
Assert-Equal -Case 'Docker UNKNOWN alone names the Docker layer' `
    -Expected 'DOCKER_UNKNOWN' -Actual $dockerOnly.Reason
Assert-Equal -Case 'Docker UNKNOWN alone offers no machine change' `
    -Expected 'report-unknown' -Actual $dockerOnly.Action

# Contradictory virtualization evidence must still outrank a lower blocker:
# there the layers beneath are actively in doubt, not merely unobserved.
$contradiction = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    Wsl = (New-WslOutdated)
    Docker = [pscustomobject]@{
        CliFound = $true; DesktopProcessRunning = $true; DockerHostEnv = $null
        DockerContextEnv = $null; ContextEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
        InfoExit = 1; InfoOsType = $null; InfoServerVersion = $null
        InfoText = 'virtualization support is disabled in the BIOS'
        TimedOut = $false; Error = $null }
})
Assert-Equal -Case 'contradictory virtualization evidence still outranks a lower blocker' `
    -Expected 'CONFLICTING_VIRTUALIZATION_EVIDENCE' -Actual $contradiction.Reason

# A reboot-required blocker must keep carrying the reboot flag through the walk.
$rebootBlocker = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    # Hypervisor not yet running is what makes a pending reboot coherent: once
    # it is running, the change has already taken effect.
    WindowsVirtualization = (New-WinVirt -Hypervisor $false -PendingReboot $true)
    Wsl = (New-WslOutdated)
    Docker = (New-DockerEndpointUnknown)
})
Assert-Equal -Case 'a pending reboot beneath WSL is reported instead of the WSL step' `
    -Expected 'WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED' -Actual $rebootBlocker.Reason
Assert-True -Case 'and it still requires a restart' -Condition $rebootBlocker.RebootRequired

# A proven healthy local engine still short-circuits everything beneath it.
$healthyWithOldWsl = Invoke-EnvironmentPreflight -Evidence (New-Evidence @{
    Wsl = (New-WslOutdated)
})
Assert-Equal -Case 'a proven healthy local engine still vouches for the layers beneath it' `
    -Expected 'READY' -Actual $healthyWithOldWsl.Overall


# -------------------------------------- evidence probes on Windows PowerShell 5.1
# Regression from a real first run. ProcessStartInfo.ArgumentList does not exist
# on .NET Framework, so on Windows PowerShell 5.1 - which is exactly what the
# shell launches the environment preflight with, deliberately, so the check can
# run before pwsh is installed - `$psi.ArgumentList.Add()` threw and every probe
# that passed arguments came back as exit 1 with the text
# "You cannot call a method on a null-valued expression."
#
# The classifier then read that faithfully: `wsl --version` looked like a
# non-zero exit (so WSL looked outdated), Docker's endpoint looked unresolvable,
# and a machine that was actually READY was told PREFLIGHT-UNKNOWN.

Write-Host '-- argument quoting' -ForegroundColor Cyan

Assert-Equal -Case 'a plain argument is not quoted' `
    -Expected 'start' -Actual (ConvertTo-AiArgumentString -Arguments @('start'))
Assert-Equal -Case 'arguments are separated by a single space' `
    -Expected '-Json -DataRoot' -Actual (ConvertTo-AiArgumentString -Arguments @('-Json', '-DataRoot'))
Assert-Equal -Case 'an argument containing a space is quoted as one argument' `
    -Expected '"C:\Program Files\x"' -Actual (ConvertTo-AiArgumentString -Arguments @('C:\Program Files\x'))
Assert-Equal -Case 'a trailing backslash before the closing quote is doubled' `
    -Expected '"C:\dir with space\\"' -Actual (ConvertTo-AiArgumentString -Arguments @('C:\dir with space\'))
Assert-Equal -Case 'an embedded quote is escaped' `
    -Expected '"say \"hi\""' -Actual (ConvertTo-AiArgumentString -Arguments @('say "hi"'))
Assert-Equal -Case 'an empty argument survives as an empty quoted argument' `
    -Expected '""' -Actual (ConvertTo-AiArgumentString -Arguments @(''))
Assert-Equal -Case 'no arguments produce an empty command line' `
    -Expected '' -Actual (ConvertTo-AiArgumentString -Arguments @())

Write-Host '-- probes actually pass arguments on Windows PowerShell 5.1' -ForegroundColor Cyan

$windowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf)) {
    Assert-True -Case 'Windows PowerShell 5.1 probe (skipped: host not present)' -Condition $true
} else {
    $probe = Join-Path ([IO.Path]::GetTempPath()) ("afk-argprobe-" + [guid]::NewGuid().ToString('n') + ".ps1")
    $common = Join-Path $Root 'ai-common.ps1'
    @"
. '$common'
`$result = Invoke-AiProcess -FilePath 'cmd.exe' -ArgumentList @('/c', 'exit', '7') -TimeoutSec 60
Write-Output "EXIT=`$(`$result.Code)"
Write-Output "TEXT=`$(`$result.Text)"
`$psv = Invoke-AiProcess -FilePath 'cmd.exe' -ArgumentList @('/c', 'echo', 'AFK-MARKER') -TimeoutSec 60
Write-Output "ECHO_EXIT=`$(`$psv.Code)"
Write-Output "ECHO_TEXT=`$(`$psv.Text)"
"@ | Set-Content -LiteralPath $probe -Encoding UTF8

    try {
        $output = & $windowsPowerShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $probe 2>&1
        $lines = @($output | ForEach-Object { "$_" })
        $exitLine = @($lines | Where-Object { $_ -like 'EXIT=*' } | Select-Object -First 1)
        $textLine = @($lines | Where-Object { $_ -like 'TEXT=*' } | Select-Object -First 1)
        $echoLine = @($lines | Where-Object { $_ -like 'ECHO_TEXT=*' } | Select-Object -First 1)

        Assert-Equal -Case 'arguments reach the child process on Windows PowerShell 5.1' `
            -Expected 'EXIT=7' -Actual "$exitLine"
        Assert-True -Case 'no null-valued-expression error on Windows PowerShell 5.1' `
            -Condition ("$textLine" -notmatch 'null-valued expression') "$textLine"
        Assert-True -Case 'child output is captured on Windows PowerShell 5.1' `
            -Condition ("$echoLine" -match 'AFK-MARKER') "$echoLine"
    } finally {
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------- summary

Write-Host ''
if ($script:Fail -gt 0) {
    Write-Host "FAILURES ($script:Fail):" -ForegroundColor Red
    foreach ($f in $script:Failures) { Write-Host "  - $f" -ForegroundColor Red }
    Write-Host ''
    Write-Host "PREFLIGHT TESTS FAILED: $script:Pass passed, $script:Fail failed." -ForegroundColor Red
    exit 1
}
Write-Host "PREFLIGHT TESTS PASSED: $script:Pass passed, 0 failed." -ForegroundColor Green
exit 0
