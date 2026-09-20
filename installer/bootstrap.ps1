<#
  bootstrap.ps1 - tiny downloadable entry point for the localai Friend Bootstrapper.

  Gets a novice from "nothing" to running the guided installer:
    1. ensure PowerShell 7 (winget-install it, then relaunch under pwsh),
    2. fetch this repo (git clone, else the GitHub zip with path-containment guards),
    3. hand off to installer/Install-LocalAI.ps1.

  Runs under Windows PowerShell 5.1 on a clean box (the orchestrator needs 7.0,
  so this stub deliberately does NOT #require it). Launch it explicitly - a
  double-clicked .ps1 opens Notepad:
    pwsh  -ExecutionPolicy Bypass -File installer\bootstrap.ps1     (if you have 7)
    powershell -ExecutionPolicy Bypass -File installer\bootstrap.ps1 (clean box)

  Distribution is PINNED (audit finding 7): -Ref is a released tag, and the
  download is verified against a known commit SHA (git path) or zip SHA256 (no-git
  path). It fails closed if the pins are unset, so a friend can never run an
  unverified download. MAINTAINER: after cutting the tag, fill $ExpectedCommit
  (git rev-list -n1 <tag>) and $ExpectedZipSha256 (SHA256 of the tag source zip) -
  see installer/README.md. Use -AllowUnverified only for local dev testing.
#>
[CmdletBinding()]
param(
  [string]$Owner = 'allusionsafk',   # canonical source: allusionsafk/afk-ai
  [string]$Repo = 'afk-ai',
  [string]$Ref = 'v0.1.7rc1',
  # Filled once the tag is cut; verified after fetch. Empty = not yet pinned.
  [string]$ExpectedCommit = '0a03df5f64471b8299c29e8bc86bee1bb1bd4158',
  [string]$ExpectedZipSha256 = '6AC5D0B06E1164B7F47348DC4270EA228192548F5176AC06500B0EDED44EE3C2',
  [switch]$AllowUnverified,
  [string]$Destination = (Join-Path $env:USERPROFILE 'localai'),
  [string[]]$InstallerArgs = @()
)

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 on older Win10 builds may not offer TLS 1.2 by
# default, which GitHub requires; opting in is harmless where it already is.
if ($PSVersionTable.PSVersion.Major -lt 6) {
  [Net.ServicePointManager]::SecurityProtocol = `
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
}

if (-not $AllowUnverified -and -not $ExpectedCommit -and -not $ExpectedZipSha256) {
  throw @"
This bootstrap is not yet pinned for distribution ($Owner/$Repo@$Ref).
A maintainer must set -ExpectedCommit and -ExpectedZipSha256 for the release tag
(see installer/README.md). For local dev testing only, pass -AllowUnverified.
"@
}

function Resolve-Pwsh7 {
  # Get-Command first; fall back to the default install dir because a
  # just-finished MSI/winget install does NOT refresh this process's PATH.
  $cmd = Get-Command 'pwsh.exe' -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $default = Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe'
  if (Test-Path -LiteralPath $default) { return $default }
  return $null
}

function Install-Pwsh7 {
  $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
  if ($winget) {
    Write-Host 'Installing PowerShell 7 via winget...' -ForegroundColor Cyan
    & $winget.Source install --id 'Microsoft.PowerShell' -e `
      --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -eq 0) { return }
    Write-Host "winget could not install PowerShell 7 (exit $LASTEXITCODE); falling back to the signed MSI." -ForegroundColor Yellow
  } else {
    Write-Host 'winget not found (normal on Windows Sandbox / stripped installs); downloading the signed PowerShell 7 MSI instead...' -ForegroundColor Cyan
  }

  # Fallback: latest stable MSI from the official release, verified by
  # Microsoft's Authenticode signature (durable; a pinned hash would go stale
  # every release). Requires admin for msiexec /qn - failure messages carry
  # the manual URL.
  $manual = 'https://aka.ms/powershell-release'
  try {
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/PowerShell/PowerShell/releases/latest' -TimeoutSec 60
  } catch {
    throw "Could not reach the PowerShell release API ($($_.Exception.Message)). Install PowerShell 7 from $manual then re-run."
  }
  $arch = if ([Environment]::Is64BitOperatingSystem) { 'x64' } else { 'x86' }
  $asset = @($release.assets | Where-Object { $_.name -like "PowerShell-*-win-$arch.msi" })[0]
  if (-not $asset) { throw "No win-$arch MSI on the latest PowerShell release. Install from $manual then re-run." }
  $msi = Join-Path $env:TEMP $asset.name
  Write-Host "Downloading $($asset.name)..." -ForegroundColor Cyan
  Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $msi -TimeoutSec 600
  $sig = Get-AuthenticodeSignature -LiteralPath $msi
  if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    Remove-Item -LiteralPath $msi -Force -ErrorAction SilentlyContinue
    throw "Downloaded MSI failed signature verification (status: $($sig.Status)). Refusing to run it. Install from $manual instead."
  }
  Write-Host 'Signature verified (Microsoft Corporation). Installing (may take a minute)...' -ForegroundColor Green
  $proc = Start-Process 'msiexec.exe' -ArgumentList "/i `"$msi`" /qn /norestart" -Wait -PassThru
  Remove-Item -LiteralPath $msi -Force -ErrorAction SilentlyContinue
  if ($proc.ExitCode -ne 0) {
    throw "MSI install failed (exit $($proc.ExitCode)); admin rights are required. Install from $manual then re-run."
  }
}

function Move-OldCopyAside {
  # A failed or older install is in the way. Move it aside (NEVER delete a
  # folder this run did not create) so a fresh verified copy can land, and tell
  # the user what happened - they should not have to clean up by hand.
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Reason
  )
  $parent = Split-Path -Parent $Path
  $leaf = Split-Path -Leaf $Path
  $target = Join-Path $parent "$leaf-old"
  $n = 1
  while (Test-Path -LiteralPath $target) {
    $n++
    $target = Join-Path $parent "$leaf-old$n"
  }
  Write-Host "Found $Reason at $Path." -ForegroundColor Yellow
  Write-Host "Moving it to $target and installing a fresh copy." -ForegroundColor Yellow
  Write-Host '(Nothing is deleted - you can remove that folder once the new install works.)' -ForegroundColor Yellow
  try {
    Move-Item -LiteralPath $Path -Destination $target -ErrorAction Stop
  } catch {
    throw "Could not move $Path out of the way ($($_.Exception.Message)). Close any window or program using that folder, then run this installer again."
  }
}

function Get-Repo {
  param([Parameter(Mandatory)][string]$Into)

  $parent = Split-Path -Parent $Into
  if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  $parentResolved = (Resolve-Path -LiteralPath $parent).Path

  $git = Get-Command 'git.exe' -ErrorAction SilentlyContinue
  # Written after a successful zip install; lets a re-run tell "resume of THIS
  # release" apart from "stale copy of an older one".
  $refMarker = Join-Path $Into (Join-Path 'installer' '.localai-ref')

  if (Test-Path -LiteralPath (Join-Path $Into '.git')) {
    # Re-run over an existing clone: re-verify the pin instead of trusting it.
    # A match is a resume; a mismatch (failed or older install) is moved aside
    # automatically so the user never has to delete a folder by hand.
    if ($git -and $ExpectedCommit) {
      $head = (& $git.Source -C $Into rev-parse HEAD).Trim()
      if ($head -eq $ExpectedCommit) {
        Write-Host "Repo already present at $Into (verified commit $head); using it." -ForegroundColor DarkGray
        return
      }
      Move-OldCopyAside -Path $Into -Reason "a previous localai copy at commit $head (this installer expects $ExpectedCommit)"
      # fall through to a fresh clone below
    } else {
      Write-Host "Repo already present at $Into; skipping clone." -ForegroundColor DarkGray
      return
    }
  } elseif (Test-Path -LiteralPath $Into) {
    # A non-git folder is already there (zip install). Reuse it only when its
    # ref marker proves it is this release (a mid-install resume); anything
    # older, unmarked, or incomplete is moved aside for a fresh verified copy.
    $markerRef = ''
    if (Test-Path -LiteralPath $refMarker) {
      $raw = Get-Content -LiteralPath $refMarker -Raw -ErrorAction SilentlyContinue
      if ($raw) { $markerRef = $raw.Trim() }
    }
    $orchestratorPresent = Test-Path -LiteralPath (Join-Path $Into (Join-Path 'installer' 'Install-LocalAI.ps1'))
    if ($markerRef -eq $Ref -and $orchestratorPresent) {
      Write-Host "Folder already present at $Into (release $Ref); using it." -ForegroundColor DarkGray
      return
    }
    Move-OldCopyAside -Path $Into -Reason 'a previous (older or incomplete) localai folder'
  }
  if ($git) {
    & $git.Source clone --depth 1 --branch $Ref "https://github.com/$Owner/$Repo.git" $Into
    if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)." }
    if ($ExpectedCommit) {
      $head = (& $git.Source -C $Into rev-parse HEAD).Trim()
      if ($head -ne $ExpectedCommit) {
        Remove-Item -LiteralPath $Into -Recurse -Force -ErrorAction SilentlyContinue
        throw "Commit mismatch: expected $ExpectedCommit, got $head. Refusing to continue."
      }
      Write-Host "Verified commit $head." -ForegroundColor Green
    } elseif (-not $AllowUnverified) {
      throw "No -ExpectedCommit pinned; refusing an unverified clone."
    }
    return
  }

  # No git: download the GitHub tag zip and extract, guarding every path against
  # escaping the destination parent (hardened path-containment pattern).
  $zip = Join-Path $parentResolved "$Repo-$Ref.zip"
  $extract = Join-Path $parentResolved "$Repo-extract"
  $parentPrefix = $parentResolved.TrimEnd('\') + '\'
  foreach ($path in @($zip, $extract, $Into)) {
    $full = [System.IO.Path]::GetFullPath($path)
    if (-not $full.StartsWith($parentPrefix, [StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to write outside ${parentResolved}: $full"
    }
  }
  $url = "https://github.com/$Owner/$Repo/archive/refs/tags/$Ref.zip"
  Write-Host "Downloading $url ..." -ForegroundColor Cyan
  $oldProgress = $ProgressPreference
  $ProgressPreference = 'SilentlyContinue'  # 5.1 progress rendering slows downloads badly
  try {
    Invoke-WebRequest -Uri $url -OutFile $zip -TimeoutSec 180
  } finally {
    $ProgressPreference = $oldProgress
  }
  if ($ExpectedZipSha256) {
    $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
    if ($actual -ne $ExpectedZipSha256.ToUpperInvariant()) {
      Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
      throw "Zip SHA256 mismatch: expected $ExpectedZipSha256, got $actual. Refusing to continue."
    }
    Write-Host "Verified zip SHA256 $actual." -ForegroundColor Green
  } elseif (-not $AllowUnverified) {
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    throw "No -ExpectedZipSha256 pinned and no git available; refusing an unverified download."
  }
  if (Test-Path -LiteralPath $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
  Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
  $inner = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1
  if (-not $inner) { throw 'Downloaded zip had no top-level folder.' }
  Move-Item -LiteralPath $inner.FullName -Destination $Into
  # Stamp which release this folder is (see $refMarker above).
  Set-Content -LiteralPath $refMarker -Value $Ref -Encoding ascii
  Remove-Item -LiteralPath $zip, $extract -Recurse -Force -ErrorAction SilentlyContinue
}

# 1. Fetch the repo (works under 5.1 or 7) - unless this bootstrap is already
# running from inside the destination (the .cmd next to a completed install).
# In that case the repo is by definition present; re-verifying pins would be
# wrong, because by the two-commit release dance the copy at any tag carries
# the PREVIOUS release's pins and would move its own working install aside.
$selfRepo = ''
if ($PSScriptRoot) {
  try {
    $selfRepo = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
  } catch { $selfRepo = '' }
}
$destFull = [System.IO.Path]::GetFullPath($Destination)
if ($selfRepo -and ($selfRepo.TrimEnd('\') -ieq $destFull.TrimEnd('\'))) {
  Write-Host "Running from inside $Destination; using this copy." -ForegroundColor DarkGray
} else {
  Get-Repo -Into $Destination
}

# 2. Hand off to the orchestrator under pwsh 7.
# NOTE: 5.1-safe nested Join-Path; the 3-argument form is PowerShell 7 only.
$orchestrator = Join-Path $Destination (Join-Path 'installer' 'Install-LocalAI.ps1')
if (-not (Test-Path -LiteralPath $orchestrator)) {
  throw "Orchestrator not found at $orchestrator - download may be incomplete."
}

$pwshPath = Resolve-Pwsh7
if (-not $pwshPath) { Install-Pwsh7; $pwshPath = Resolve-Pwsh7 }
if (-not $pwshPath) {
  throw 'PowerShell 7 still not found after install. Open a new terminal and run the orchestrator manually.'
}

Write-Host "Launching the guided installer under PowerShell 7..." -ForegroundColor Cyan
& $pwshPath -NoProfile -ExecutionPolicy Bypass -File $orchestrator @InstallerArgs
exit $LASTEXITCODE
