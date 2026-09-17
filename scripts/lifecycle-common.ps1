#requires -Version 7.0

function Assert-ExpectedDigest {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$ExpectedSha256
  )
  if ($ExpectedSha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Expected SHA-256 must contain exactly 64 hexadecimal characters.' }
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Artifact does not exist: '$Path'." }
  $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
  if ($actual -ne $ExpectedSha256.ToUpperInvariant()) {
    throw "Artifact digest mismatch. Expected $($ExpectedSha256.ToUpperInvariant()), got $actual."
  }
  return $actual
}

function Assert-LifecycleEvidence {
  param([Parameter(Mandatory)]$Evidence)
  $required = @(
    'digest_verified', 'installer_exit_zero', 'executable_present', 'version_matches',
    'self_test_passed', 'owned_runtime_verified', 'uninstall_entry_present', 'start_menu_present',
    'uninstaller_exit_zero', 'program_files_removed', 'state_preserved'
  )
  # Every required fact must be proven, and no additional recorded fact may be false.
  $failed = @($required | Where-Object {
    -not $Evidence.Contains($_) -or -not [bool]$Evidence[$_]
  }) + @($Evidence.Keys | Where-Object { $_ -notin $required -and -not [bool]$Evidence[$_] })
  if ($failed.Count) { throw "Lifecycle evidence is not certifiable: $($failed -join ', ')." }
  return $true
}

function Test-OwnedRuntime {
  <#
    Run the INSTALLED engine on the INSTALLED interpreter and require it to prove:
    isolation from the PC's Python, the pinned version, and every shipped file
    matching the installed manifest. Read-only; touches no Docker or model state.
  #>
  param(
    [Parameter(Mandatory)][string]$InstallRoot,
    [Parameter(Mandatory)][string]$DataRoot
  )
  $python = Join-Path $InstallRoot 'runtime/python/python.exe'
  $entry = Join-Path $InstallRoot 'installer/afk-payload.py'
  if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $entry -PathType Leaf)) { return $false }
  $run = Invoke-BoundedProcess -FilePath $python -Arguments @(
    '-I', '-B', $entry, '--program-root', $InstallRoot, '--data-root', $DataRoot, 'runtime-info'
  ) -TimeoutSeconds 120 -CaptureOutput
  if ($run.ExitCode -ne 0) { return $false }
  try { $facts = $run.StandardOutput | ConvertFrom-Json -ErrorAction Stop } catch { return $false }
  return [bool]($facts.isolated -and $facts.site_disabled -and $facts.pinned_version -eq $facts.python_version -and
    $facts.installation.manifest_present -and $facts.installation.intact)
}

function Assert-SafeDisposablePath {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$AllowedParent
  )
  $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
  $parent = [IO.Path]::GetFullPath($AllowedParent).TrimEnd('\')
  if ($full -eq $parent -or -not $full.StartsWith($parent + '\', [StringComparison]::OrdinalIgnoreCase) -or
      $full.Length -lt 12 -or $full -eq [IO.Path]::GetPathRoot($full)) {
    throw "Unsafe disposable path '$full'; expected a child of '$parent'."
  }
  return $full
}

function Wait-PathAbsent {
  param(
    [Parameter(Mandatory)][string]$Path,
    [int]$TimeoutSeconds = 30,
    [int]$PollMilliseconds = 100
  )
  if ($TimeoutSeconds -lt 0) { throw 'TimeoutSeconds cannot be negative.' }
  if ($PollMilliseconds -lt 1) { throw 'PollMilliseconds must be positive.' }
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    if ([DateTime]::UtcNow -ge $deadline) { break }
    Start-Sleep -Milliseconds $PollMilliseconds
  } while ($true)
  throw "Path remained after uninstall cleanup timeout: '$Path'."
}

function Invoke-BoundedProcess {
  param(
    [Parameter(Mandatory)][string]$FilePath,
    [string[]]$Arguments = @(),
    [int]$TimeoutSeconds = 600,
    [switch]$CaptureOutput
  )
  $start = [Diagnostics.ProcessStartInfo]::new()
  $start.FileName = $FilePath
  $start.UseShellExecute = $false
  $start.CreateNoWindow = $true
  $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
  $start.RedirectStandardOutput = [bool]$CaptureOutput
  $start.RedirectStandardError = [bool]$CaptureOutput
  foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
  $process = [Diagnostics.Process]::new()
  $process.StartInfo = $start
  if (-not $process.Start()) { throw "Could not start '$FilePath'." }
  $stdoutTask = if ($CaptureOutput) { $process.StandardOutput.ReadToEndAsync() } else { $null }
  $stderrTask = if ($CaptureOutput) { $process.StandardError.ReadToEndAsync() } else { $null }
  if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    try { $process.Kill($true) } catch {}
    throw "Process '$FilePath' exceeded the $TimeoutSeconds second timeout."
  }
  [pscustomobject]@{
    ExitCode = $process.ExitCode
    StandardOutput = $(if ($stdoutTask) { $stdoutTask.GetAwaiter().GetResult() } else { '' })
    StandardError = $(if ($stderrTask) { $stderrTask.GetAwaiter().GetResult() } else { '' })
  }
}
