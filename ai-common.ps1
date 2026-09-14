<#
  ai-common.ps1 - shared helpers for the localai scripts.

  Dot-source it from a script that lives in this folder:
      . (Join-Path $PSScriptRoot 'ai-common.ps1')

  Behaviour-preserving home for logic that was copy-pasted across many
  scripts. Add a helper here only when at least one script uses it.
#>

function Resolve-AiCommandPath {
  # Full path of a command if resolvable, else the name unchanged.
  param([Parameter(Mandatory)][string]$Name)
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $Name
}

function ConvertTo-AiArgumentString {
  <#
    Quote an argument vector the way CommandLineToArgvW parses it back.

    Needed because Windows PowerShell 5.1 runs on .NET Framework, which has no
    ProcessStartInfo.ArgumentList - only the single Arguments string. Getting the
    quoting wrong there silently corrupts every argument, so the rules are
    applied explicitly: backslashes are literal except in the run immediately
    before a quote, where they double.
  #>
  param([string[]]$Arguments = @())
  $parts = foreach ($argument in @($Arguments)) {
    $value = [string]$argument
    if ($value -ne '' -and $value -notmatch '[\s"]') { $value; continue }
    $escaped = [regex]::Replace($value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    '"' + $escaped + '"'
  }
  return (@($parts) -join ' ')
}

function Invoke-AiProcess {
  # Run a process, capture stdout+stderr, enforce a timeout. Never throws.
  # Returns [pscustomobject]@{ Code; Text }. Code 124 = timed out, 1 = launch error.
  param(
    [Parameter(Mandatory)][string]$FilePath,
    [string[]]$ArgumentList = @(),
    [int]$TimeoutSec = 30,
    [string]$WorkingDirectory
  )
  $p = $null
  try {
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = (Resolve-AiCommandPath $FilePath)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
    # Windows PowerShell 5.1 is .NET Framework, where ProcessStartInfo has no
    # ArgumentList at all - $psi.ArgumentList is $null, and calling .Add() on it
    # threw, so EVERY probe that passed arguments came back as exit 1 with the
    # text "You cannot call a method on a null-valued expression."
    #
    # The environment preflight is deliberately 5.1-compatible so it can run
    # before pwsh exists, and the shell launches it with powershell.exe. So on a
    # real first run every wsl/docker probe failed identically: WSL looked
    # outdated because `wsl --version` "returned" non-zero, Docker's endpoint
    # looked unresolvable, and the whole check collapsed to PREFLIGHT-UNKNOWN.
    if ($null -ne $psi.ArgumentList) {
      foreach ($arg in @($ArgumentList)) { [void]$psi.ArgumentList.Add([string]$arg) }
    } else {
      $psi.Arguments = ConvertTo-AiArgumentString -Arguments @($ArgumentList)
    }

    $p = [System.Diagnostics.Process]::new()
    $p.StartInfo = $psi
    [void]$p.Start()
    $stdoutTask = $p.StandardOutput.ReadToEndAsync()
    $stderrTask = $p.StandardError.ReadToEndAsync()

    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
      try { $p.Kill($true) } catch { try { $p.Kill() } catch { } }
      $cmdLine = ("$FilePath $($ArgumentList -join ' ')").Trim()
      return [pscustomobject]@{ Code = 124; Text = "Timed out after ${TimeoutSec}s: $cmdLine" }
    }

    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $text = ((@($stdout, $stderr) | Where-Object { $_ }) -join "`n").Trim()
    return [pscustomobject]@{ Code = $p.ExitCode; Text = $text }
  } catch {
    return [pscustomobject]@{ Code = 1; Text = $_.Exception.Message }
  } finally {
    if ($p) { $p.Dispose() }
  }
}

function Resolve-AiPythonCommand {
  # The interpreter that can `import localai`, as @(exe, launcher-args...):
  # py -3.12 (what the installer pins) first, then py, then python. A bare
  # `python` can be a different install - or the Microsoft Store stub - without
  # the localai package; this mirrors the probe in `Start Local AI.cmd`.
  # Returns $null when no interpreter has localai. Cached per script.
  # The unary comma stops PowerShell unrolling a 1-element array to a string.
  if ($script:AiPythonCommand) { return ,$script:AiPythonCommand }
  foreach ($candidate in @(@('py.exe', '-3.12'), @('py.exe'), @('python.exe'))) {
    $exe = Get-Command $candidate[0] -ErrorAction SilentlyContinue
    if (-not $exe) { continue }
    $rest = @($candidate | Select-Object -Skip 1)
    $probe = Invoke-AiProcess $exe.Source ($rest + @('-c', 'import localai')) 30
    if ($probe.Code -eq 0) {
      $script:AiPythonCommand = @($exe.Source) + $rest
      return ,$script:AiPythonCommand
    }
  }
  return $null
}

function Invoke-AiLocalai {
  # Run `localai <args>` with the interpreter that actually has the package.
  # Same result shape as Invoke-AiProcess; Code 1 with a hint when none found.
  param(
    [Parameter(Mandatory)][string[]]$Arguments,
    [int]$TimeoutSec = 300,
    [string]$WorkingDirectory
  )
  $py = Resolve-AiPythonCommand
  if (-not $py) {
    return [pscustomobject]@{
      Code = 1
      Text = 'No Python with the localai package found (tried: py -3.12, py, python). Run the installer, or: py -3.12 -m pip install -e .'
    }
  }
  $py = @($py)
  $full = @($py | Select-Object -Skip 1) + @('-m', 'localai') + $Arguments
  return Invoke-AiProcess $py[0] $full $TimeoutSec $WorkingDirectory
}
