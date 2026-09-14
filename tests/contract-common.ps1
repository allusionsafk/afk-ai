#requires -Version 7.0
<#
  tests/contract-common.ps1 - shared helpers for the repository's contract suites.

  Dot-source this from a contract suite and read every file you intend to match
  against with Get-ContractText.
#>

function Get-ContractText {
  <#
    .SYNOPSIS
      Read a text file for line-oriented assertions, with newlines normalised to LF.

    .DESCRIPTION
      Contract suites assert with line-anchored patterns like
      '(?m)^Uninstallable=yes$'. In .NET regex, multiline '$' matches immediately
      BEFORE the '\n' - so on a CRLF checkout the '\r' is still inside the line and
      the pattern does not match, even though the file's content is correct.

      That makes these assertions depend on how the checkout materialised rather
      than on what the file says. A local clone with LF passes; GitHub's
      windows-latest runner, which checks out with core.autocrlf=true and therefore
      writes CRLF, fails the identical tree. The reverse failure mode is worse: the
      same break turns a '-notmatch' guard into a silent pass, so a contract meant
      to forbid something quietly stops forbidding it.

      Normalising once, here, keeps every assertion newline-agnostic and removes the
      hazard from assertions that have not been written yet. Fix the reader, not each
      individual regex.

    .PARAMETER Path
      The file to read.
  #>
  [CmdletBinding()]
  param([Parameter(Mandatory)][string]$Path)

  # -Raw yields $null for an empty file; callers match against a string.
  $text = Get-Content -LiteralPath $Path -Raw
  if ($null -eq $text) { return '' }
  # CRLF first, then any bare CR (old-Mac line endings), so no '\r' survives.
  return ($text -replace "`r`n", "`n") -replace "`r", "`n"
}
