#requires -Version 7.0
[CmdletBinding()]
param(
  [ValidateSet('Release')][string]$Configuration = 'Release',
  [switch]$SkipTests,
  [switch]$AllowDirty,
  [string]$InnoCompiler = ''
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$version = Get-Content -LiteralPath (Join-Path $Root 'installer/version.json') -Raw | ConvertFrom-Json
$toolchain = Get-Content -LiteralPath (Join-Path $Root 'installer/toolchain.json') -Raw | ConvertFrom-Json
$commit = (& git -C $Root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw 'Could not resolve the source commit.' }
if (-not $AllowDirty) {
  $dirty = @(& git -C $Root status --porcelain --untracked-files=all)
  if ($dirty.Count) { throw 'Release builds require a clean worktree. Commit the intended payload or pass -AllowDirty for a non-certifiable development build.' }
}

if (-not $SkipTests) {
  & (Join-Path $Root 'scripts/Test-DistributionContracts.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'Distribution contract gate failed.' }
}

$buildRoot = Join-Path $Root 'build/release'
$shellRoot = Join-Path $buildRoot 'shell'
$payloadRoot = Join-Path $buildRoot 'payload'
$distRoot = Join-Path $Root 'dist'
foreach ($path in @($shellRoot, $payloadRoot)) {
  $resolved = [IO.Path]::GetFullPath($path)
  if (-not $resolved.StartsWith(([IO.Path]::GetFullPath($buildRoot).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing unsafe build path '$resolved'."
  }
  if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
[void](New-Item -ItemType Directory -Path $shellRoot -Force)
[void](New-Item -ItemType Directory -Path $distRoot -Force)

$publishArguments = @(
  'publish', (Join-Path $Root 'src/AFKLocalAI.App/AFKLocalAI.App.csproj'),
  '-c', $Configuration, '-r', 'win-x64', '--self-contained', 'true',
  '-p:PublishSingleFile=true', '-p:IncludeNativeLibrariesForSelfExtract=true',
  '-p:DebugSymbols=false', '-p:DebugType=None', '-o', $shellRoot
)
& dotnet @publishArguments
if ($LASTEXITCODE -ne 0) { throw 'Native shell publish failed.' }

$payloadParameters = @{
  SourceRoot = $Root
  StagingRoot = $payloadRoot
  ShellPublishRoot = $shellRoot
  Commit = $commit
}
$payload = & (Join-Path $Root 'scripts/New-ReleasePayload.ps1') @payloadParameters
if (-not $payload -or -not (Test-Path -LiteralPath $payload.ManifestPath)) {
  throw 'Release payload staging failed.'
}

if (-not $InnoCompiler) {
  $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
  $candidates = @(
    $(if ($programFilesX86) { Join-Path $programFilesX86 'Inno Setup 6/ISCC.exe' }),
    $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'Inno Setup 6/ISCC.exe' }),
    $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs/Inno Setup 6/ISCC.exe' })
  )
  $InnoCompiler = @($candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1)
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
  throw 'Inno Setup 6 compiler not found. Install the pinned release toolchain, then rerun this command.'
}
$compilerVersion = ([Diagnostics.FileVersionInfo]::GetVersionInfo($InnoCompiler)).ProductVersion
if (-not $compilerVersion -or $compilerVersion -eq '0.0.0.0') {
  $compilerDirectory = (Split-Path -Parent $InnoCompiler).TrimEnd('\') + '\'
  $registrations = @(
    Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue
    Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue
    Get-ItemProperty 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue
  )
  $compilerVersion = @($registrations | Where-Object {
      $_.InstallLocation -and $_.InstallLocation.TrimEnd('\') + '\' -eq $compilerDirectory
    } | Select-Object -ExpandProperty DisplayVersion -First 1)
}
if ("$compilerVersion" -ne "$($toolchain.inno_setup.version)") {
  throw "Inno Setup version '$compilerVersion' does not match pinned version '$($toolchain.inno_setup.version)'."
}

$defines = @(
  ('/DSourceRoot=' + $payloadRoot)
  ('/DOutputDir=' + $distRoot)
  ('/DAppVersion=' + $version.display_version)
  ('/DFileVersion=' + $version.file_version)
  ('/DChannel=' + $version.channel)
  ('/DSourceCommit=' + $commit)
)
& $InnoCompiler @defines (Join-Path $Root 'installer/AFKLocalAI.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }

$installer = Join-Path $distRoot $version.installer_name
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw "Expected installer was not emitted: '$installer'." }
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToUpperInvariant()
$sidecar = "$installer.sha256.txt"
"$hash  $($version.installer_name)" | Set-Content -LiteralPath $sidecar -Encoding ascii
$payloadEvidence = Join-Path $distRoot ($version.installer_name + '.payload-manifest.json')
Copy-Item -LiteralPath $payload.ManifestPath -Destination $payloadEvidence -Force

[pscustomobject]@{
  InstallerPath = $installer
  Sha256 = $hash
  SidecarPath = $sidecar
  PayloadManifestPath = $payloadEvidence
  SourceCommit = $commit
}
