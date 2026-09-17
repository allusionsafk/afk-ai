#requires -Version 7.0
<#
  Stage the pinned, verified CPython runtime AFK AI installs and executes.

  Output layout under -StagingRoot:
    python\              the embeddable distribution, byte-for-byte as verified
    afk-runtime.json     AFK's identity record the engine checks at every start

  Nothing here is trusted by name or by transport alone. The archive must match
  the pinned size and SHA-256, the interpreter binaries must carry a valid
  Authenticode signature from the pinned publisher, the ._pth file must contain
  exactly the pinned search path (no "import site", no extra directories), and
  the extracted interpreter must prove at runtime that it starts isolated.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$StagingRoot,
  [string]$CacheRoot = '',
  [string]$PinPath = '',
  [switch]$Offline
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $PinPath) { $PinPath = Join-Path $Root 'installer/python-runtime.json' }
if (-not $CacheRoot) { $CacheRoot = Join-Path $Root 'build/cache/python-runtime' }

# Relative paths mean "relative to where the caller is", not to the process's
# .NET current directory, which PowerShell does not keep in step with Set-Location.
function Resolve-CallerPath([string]$Path) {
  return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}
$buildRoot = [IO.Path]::GetFullPath((Join-Path $Root 'build')).TrimEnd('\') + '\'
$staging = [IO.Path]::GetFullPath((Resolve-CallerPath $StagingRoot)).TrimEnd('\')
$cache = [IO.Path]::GetFullPath((Resolve-CallerPath $CacheRoot)).TrimEnd('\')
foreach ($path in @($staging, $cache)) {
  if (-not ($path + '\').StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase) -or $path.Length -le $buildRoot.Length) {
    throw "Refusing a runtime path outside the build directory: '$path'."
  }
}

$pin = Get-Content -LiteralPath $PinPath -Raw | ConvertFrom-Json
if ($pin.implementation -ne 'CPython' -or $pin.version -notmatch '^3\.\d+\.\d+$') { throw 'Runtime pin must name a CPython 3.x.y release.' }
if ($pin.sha256 -notmatch '^[0-9A-F]{64}$') { throw 'Runtime pin SHA-256 must be 64 upper-case hexadecimal characters.' }
if ($pin.source_url -notmatch '^https://www\.python\.org/ftp/python/[0-9.]+/python-[0-9.]+-embed-amd64\.zip$') {
  throw 'Runtime pin must reference an official python.org embeddable amd64 archive.'
}
if (@($pin.third_party_packages).Count -ne 0) { throw 'The product runtime ships no third-party packages.' }

function Test-Archive([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
  if ((Get-Item -LiteralPath $Path).Length -ne [int64]$pin.size) { return $false }
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant() -eq $pin.sha256
}

[void](New-Item -ItemType Directory -Path $cache -Force)
$archive = Join-Path $cache ([IO.Path]::GetFileName(([Uri]$pin.source_url).AbsolutePath))
if (-not (Test-Archive $archive)) {
  if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
  if ($Offline) { throw "Pinned runtime archive is not cached at '$archive' and -Offline was requested." }
  $download = "$archive.partial"
  $oldProgress = $ProgressPreference
  $ProgressPreference = 'SilentlyContinue'
  try {
    Invoke-WebRequest -Uri $pin.source_url -OutFile $download -TimeoutSec 600
  } finally {
    $ProgressPreference = $oldProgress
  }
  if (-not (Test-Archive $download)) {
    Remove-Item -LiteralPath $download -Force -ErrorAction SilentlyContinue
    throw 'Downloaded runtime archive does not match the pinned size and SHA-256. Refusing to use it.'
  }
  Move-Item -LiteralPath $download -Destination $archive -Force
}

$extract = Join-Path $cache ('extract-' + [guid]::NewGuid().ToString('n'))
try {
  Expand-Archive -LiteralPath $archive -DestinationPath $extract
  foreach ($name in @($pin.signed_files)) {
    $file = Join-Path $extract $name
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Runtime archive is missing '$name'." }
    $signature = Get-AuthenticodeSignature -LiteralPath $file
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -ne $pin.authenticode_subject) {
      throw "Runtime file '$name' is not validly signed by the pinned publisher (status $($signature.Status))."
    }
  }

  $pathFile = Join-Path $extract $pin.path_file
  if (-not (Test-Path -LiteralPath $pathFile -PathType Leaf)) { throw "Runtime archive is missing '$($pin.path_file)'." }
  $entries = @(Get-Content -LiteralPath $pathFile | ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith('#') })
  if (($entries -join '|') -ne (@($pin.path_file_entries) -join '|')) {
    # An uncommented "import site" (or any extra directory) would reopen
    # site-packages, .pth files and PYTHON* environment variables.
    throw "Runtime search path is not the pinned isolated set: '$($entries -join ', ')'."
  }

  $probe = & (Join-Path $extract 'python.exe') -c "import json, platform, sys; print(json.dumps({'version': platform.python_version(), 'isolated': sys.flags.isolated, 'no_site': sys.flags.no_site, 'ignore_environment': sys.flags.ignore_environment, 'path': sys.path}))"
  if ($LASTEXITCODE -ne 0) { throw 'The extracted runtime did not start.' }
  $facts = $probe | ConvertFrom-Json
  if ($facts.version -ne $pin.version) { throw "Extracted runtime reports $($facts.version), pinned $($pin.version)." }
  if (-not ($facts.isolated -and $facts.no_site -and $facts.ignore_environment)) {
    throw 'The extracted runtime does not start isolated from ambient Python configuration.'
  }
  $extractPrefix = [IO.Path]::GetFullPath($extract).TrimEnd('\') + '\'
  foreach ($entry in @($facts.path)) {
    $full = [IO.Path]::GetFullPath($entry)
    if (-not ($full + '\').StartsWith($extractPrefix, [StringComparison]::OrdinalIgnoreCase) -and $full -ne $extractPrefix.TrimEnd('\')) {
      throw "The extracted runtime searches outside itself: '$entry'."
    }
  }

  if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
  [void](New-Item -ItemType Directory -Path $staging -Force)
  Move-Item -LiteralPath $extract -Destination (Join-Path $staging 'python')
  $identity = [ordered]@{
    schema_version = 1
    implementation = $pin.implementation
    version = $pin.version
    distribution = $pin.distribution
    source_url = $pin.source_url
    sha256 = $pin.sha256
    third_party_packages = @()
  }
  $identity | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $staging 'afk-runtime.json') -Encoding utf8NoBOM
} finally {
  if (Test-Path -LiteralPath $extract) { Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue }
}

[pscustomobject]@{
  RuntimeRoot = $staging
  Version = $pin.version
  Sha256 = $pin.sha256
}
