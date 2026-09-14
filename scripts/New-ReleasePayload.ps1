#requires -Version 7.0
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$SourceRoot,
  [Parameter(Mandatory)][string]$StagingRoot,
  [Parameter(Mandatory)][string]$ShellPublishRoot,
  [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$Commit
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$shell = (Resolve-Path -LiteralPath $ShellPublishRoot).Path
$staging = [IO.Path]::GetFullPath($StagingRoot)
$manifestPath = Join-Path $source 'installer/payload-manifest.txt'
$sourcePrefix = $source.TrimEnd('\') + '\'

if ($staging -eq $source -or $staging -eq $shell -or $staging.Length -lt 12 -or
    $staging -eq [IO.Path]::GetPathRoot($staging)) {
  throw "Unsafe staging directory '$staging'."
}

$entries = @(Get-Content -LiteralPath $manifestPath | ForEach-Object { $_.Trim() } |
  Where-Object { $_ -and -not $_.StartsWith('#') })
if (($entries | Sort-Object -Unique).Count -ne $entries.Count) {
  throw 'installer/payload-manifest.txt contains duplicate entries.'
}

$validated = foreach ($entry in $entries) {
  $entryParts = ($entry -replace '\\', '/').Split('/')
  if ([IO.Path]::IsPathRooted($entry) -or $entryParts -contains '..') {
    throw "Payload entry must be a safe relative path: '$entry'."
  }
  if ($entry -match '(?i)(^|[\\/])(\.git|build|dist|tests?|skill-observations|logs|backups|__pycache__|bin|obj)([\\/]|$)|\.env$|\.gguf$|\.safetensors$|\.pyc$') {
    throw "Payload entry is generated, private, or too large: '$entry'."
  }
  $absolute = [IO.Path]::GetFullPath((Join-Path $source $entry))
  if (-not $absolute.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase) -or
      -not (Test-Path -LiteralPath $absolute -PathType Leaf)) {
    throw "Payload entry is missing or escapes the source root: '$entry'."
  }
  & git -C $source ls-files --error-unmatch -- $entry 1>$null 2>$null
  if ($LASTEXITCODE -ne 0) { throw "Payload entry is not tracked by Git: '$entry'." }
  [pscustomobject]@{ Relative = $entry.Replace('\', '/'); Absolute = $absolute }
}

$shellExe = Join-Path $shell 'AFKLocalAI.exe'
if (-not (Test-Path -LiteralPath $shellExe -PathType Leaf)) {
  throw "Published shell is missing: '$shellExe'."
}
$shellMetadata = Join-Path $shell 'version.json'
if (-not (Test-Path -LiteralPath $shellMetadata -PathType Leaf)) {
  throw "Published shell metadata is missing: '$shellMetadata'."
}

if (Test-Path -LiteralPath $staging) {
  Remove-Item -LiteralPath $staging -Recurse -Force
}
[void](New-Item -ItemType Directory -Path $staging -Force)

foreach ($file in $validated) {
  $destination = Join-Path $staging $file.Relative
  $parent = Split-Path -Parent $destination
  if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $parent -Force)
  }
  Copy-Item -LiteralPath $file.Absolute -Destination $destination
}
Copy-Item -LiteralPath $shellExe -Destination (Join-Path $staging 'AFKLocalAI.exe')
Copy-Item -LiteralPath $shellMetadata -Destination (Join-Path $staging 'version.json')

$normalizedTimestamp = [DateTime]::SpecifyKind([DateTime]'2000-01-01T00:00:00', [DateTimeKind]::Utc)
$payloadFiles = @(Get-ChildItem -LiteralPath $staging -Recurse -File | ForEach-Object {
  $_.LastWriteTimeUtc = $normalizedTimestamp
  $relative = [IO.Path]::GetRelativePath($staging, $_.FullName).Replace('\', '/')
  [pscustomobject]@{
    path = $relative
    size = $_.Length
    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
  }
} | Sort-Object path)

$version = Get-Content -LiteralPath (Join-Path $staging 'installer/version.json') -Raw | ConvertFrom-Json
$manifest = [ordered]@{
  schema_version = 1
  product_name = $version.product_name
  display_version = $version.display_version
  architecture = $version.architecture
  source_commit = $Commit.ToLowerInvariant()
  files = $payloadFiles
}
$outputManifest = Join-Path $staging 'payload-manifest.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outputManifest -Encoding utf8
(Get-Item -LiteralPath $outputManifest).LastWriteTimeUtc = $normalizedTimestamp

[pscustomobject]@{
  StagingRoot = $staging
  ManifestPath = $outputManifest
  FileCount = $payloadFiles.Count
}
