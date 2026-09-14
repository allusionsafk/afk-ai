#requires -Version 7.0
<#
  tests/Invoke-Checks.ps1 - the repo's verification gate (local + CI).

  Runs the cheap, behaviour-safe checks that should pass before any commit:
  PowerShell parse, node --check, JSON parse, docker compose config, the
  dashboard self-test, a static-analysis pass, and a regression test for the
  $args automatic-variable footgun that once broke every dashboard button.

  Exit code 0 = all gates passed; 1 = at least one failed. No side effects:
  nothing starts the stack, loads a model, or touches the network.

  Usage:  pwsh -File tests/Invoke-Checks.ps1 [-SkipAnalyzer]
#>
[CmdletBinding()]
param([switch]$SkipAnalyzer)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$script:Fail = 0
$results = [System.Collections.Generic.List[object]]::new()

function Add-Result([string]$Gate, [bool]$Ok, [string]$Detail = '') {
    $results.Add([pscustomobject]@{ Gate = $Gate; Status = $(if ($Ok) { 'PASS' } else { 'FAIL' }); Detail = $Detail })
    if (-not $Ok) { $script:Fail++ }
}

# Repo source files, excluding generated/data dirs so local runs match CI (which
# only checks out tracked files).
$excluded = '[\\/](\.git|logs|backups|node_modules)[\\/]'
$psFiles  = @(Get-ChildItem $Root -Recurse -Filter *.ps1  -File | Where-Object { $_.FullName -notmatch $excluded })
$mjsFiles = @(Get-ChildItem $Root -Recurse -Filter *.mjs  -File | Where-Object { $_.FullName -notmatch $excluded })
$jsonFiles = @(Get-ChildItem $Root -Recurse -Filter *.json -File | Where-Object { $_.FullName -notmatch $excluded })

# 1. PowerShell parses cleanly.
$parseBad = foreach ($f in $psFiles) {
    $errs = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$null, [ref]$errs)
    if ($errs) { "$($f.Name) ($($errs.Count))" }
}
Add-Result 'PowerShell parse' (-not $parseBad) ("$($psFiles.Count) files" + $(if ($parseBad) { "; bad: $($parseBad -join ', ')" }))

# 2. JavaScript modules syntax-check.
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    $mjsBad = foreach ($f in $mjsFiles) {
        & $node.Source --check $f.FullName 2>$null
        if ($LASTEXITCODE -ne 0) { $f.Name }
    }
    Add-Result 'node --check (.mjs)' (-not $mjsBad) ("$($mjsFiles.Count) files" + $(if ($mjsBad) { "; bad: $($mjsBad -join ', ')" }))
} else {
    Add-Result 'node --check (.mjs)' $true 'SKIPPED (node not found)'
}

# 3. JSON parses.
$jsonBad = foreach ($f in $jsonFiles) {
    try { Get-Content -LiteralPath $f.FullName -Raw | ConvertFrom-Json -ErrorAction Stop | Out-Null }
    catch { $f.Name }
}
Add-Result 'JSON parse' (-not $jsonBad) ("$($jsonFiles.Count) files" + $(if ($jsonBad) { "; bad: $($jsonBad -join ', ')" }))

# 4. docker compose config (syntax only; client-side, no daemon needed).
$compose = Join-Path $Root 'docker-compose.yml'
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker -and (Test-Path $compose)) {
    $out = & $docker.Source compose -f $compose config -q 2>&1
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Add-Result 'docker compose config' $true
    } elseif ("$out" -match 'daemon|pipe|connect|dial') {
        Add-Result 'docker compose config' $true 'SKIPPED (docker daemon unavailable)'
    } else {
        Add-Result 'docker compose config' $false ("$out" -replace '\s+', ' ').Trim()
    }
} else {
    Add-Result 'docker compose config' $true 'SKIPPED (docker not found)'
}

# 5. Dashboard self-test (config wiring) must report 0 FAIL.
#    AI-Dashboard.ps1 is not part of the public starter; skip both dashboard
#    gates when it is absent so the shipped checkout's gate can pass.
$dash = Join-Path $Root 'AI-Dashboard.ps1'
if (-not (Test-Path -LiteralPath $dash)) {
    Add-Result 'Dashboard self-test' $true 'SKIPPED (AI-Dashboard.ps1 not in this checkout)'
    Add-Result 'Regression: $args footgun' $true 'SKIPPED (AI-Dashboard.ps1 not in this checkout)'
} else {
$pwsh = (Get-Process -Id $PID).Path
$stOut = & $pwsh -NoProfile -STA -ExecutionPolicy Bypass -File $dash -SelfTest 2>&1
Add-Result 'Dashboard self-test' ($LASTEXITCODE -eq 0) (("$stOut" -split "`n" | Where-Object { $_ -match 'Summary' } | Select-Object -First 1))

# 6. Regression: the $args automatic-variable footgun. Extract the real
#    Join-ProcessArguments from AI-Dashboard.ps1 and prove it returns a full
#    argument line (an empty string is the symptom of param($args)).
try {
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($dash, [ref]$null, [ref]$null)
    $fnAst = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Join-ProcessArguments' }, $true)
    if (-not $fnAst) {
        Add-Result 'Regression: $args footgun' $false 'Join-ProcessArguments not found'
    } else {
        . ([scriptblock]::Create($fnAst.Extent.Text))
        $argLine = Join-ProcessArguments @('-NoProfile', '-File', 'C:\dir with space\s.ps1', '-Flag')
        $ok = (-not [string]::IsNullOrWhiteSpace($argLine)) -and ($argLine -match '-NoProfile') -and ($argLine -match '-Flag') -and ($argLine -match '"C:\\dir with space\\s\.ps1"')
        Add-Result 'Regression: $args footgun' $ok "output='$argLine'"
    }
} catch {
    Add-Result 'Regression: $args footgun' $false $_.Exception.Message
}
}

# 6b. Installer environment-preflight classifier + state regression suite.
#     Fixture-only: it starts no Docker/WSL, changes no Windows state, and does
#     not touch the network.
$preflightTests = Join-Path $PSScriptRoot 'Test-InstallerPreflight.ps1'
if (Test-Path -LiteralPath $preflightTests) {
    $pwshPath = (Get-Process -Id $PID).Path
    $pfOut = & $pwshPath -NoProfile -ExecutionPolicy Bypass -File $preflightTests 2>&1
    $pfOk = ($LASTEXITCODE -eq 0)
    $pfLines = @($pfOut | ForEach-Object { "$_" })
    $pfSummary = @($pfLines | Where-Object { $_ -match 'PREFLIGHT TESTS' } | Select-Object -First 1)
    $pfDetail = if ($pfSummary) { "$($pfSummary[0])".Trim() } else { ($pfLines | Select-Object -Last 1) }
    if (-not $pfOk) { $pfDetail = (@($pfLines | Select-Object -Last 12) -join ' | ') }
    Add-Result 'Installer preflight tests' $pfOk $pfDetail
} else {
    Add-Result 'Installer preflight tests' $true 'SKIPPED (Test-InstallerPreflight.ps1 not in this checkout)'
}

# 6b2. First-run state machine: machine evidence -> diagnosis -> bounded
#      recovery -> checkpoint -> resume, as a transition matrix.
$transitionTests = Join-Path $PSScriptRoot 'Test-FirstRunTransitions.ps1'
if (Test-Path -LiteralPath $transitionTests) {
    $pwshPath = (Get-Process -Id $PID).Path
    $trOut = & $pwshPath -NoProfile -ExecutionPolicy Bypass -File $transitionTests 2>&1
    $trOk = ($LASTEXITCODE -eq 0)
    $trLines = @($trOut | ForEach-Object { "$_" })
    $trSummary = @($trLines | Where-Object { $_ -match 'FIRST-RUN TRANSITION TESTS' } | Select-Object -First 1)
    $trDetail = if ($trSummary) { "$($trSummary[0])".Trim() } else { ($trLines | Select-Object -Last 1) }
    if (-not $trOk) { $trDetail = (@($trLines | Select-Object -Last 12) -join ' | ') }
    Add-Result 'First-run transition matrix' $trOk $trDetail
} else {
    Add-Result 'First-run transition matrix' $true 'SKIPPED (Test-FirstRunTransitions.ps1 not in this checkout)'
}

# 6c. Bounded prerequisite recovery mapping, state, and event protocol.
$recoveryTests = Join-Path $PSScriptRoot 'Test-InstallerRecovery.ps1'
if (Test-Path -LiteralPath $recoveryTests) {
    $pwshPath = (Get-Process -Id $PID).Path
    $recoveryOut = & $pwshPath -NoProfile -ExecutionPolicy Bypass -File $recoveryTests 2>&1
    $recoveryOk = ($LASTEXITCODE -eq 0)
    $recoveryLines = @($recoveryOut | ForEach-Object { "$_" })
    $recoverySummary = @($recoveryLines | Where-Object { $_ -match 'RECOVERY TESTS' } | Select-Object -First 1)
    $recoveryDetail = if ($recoverySummary) { "$($recoverySummary[0])".Trim() } else { ($recoveryLines | Select-Object -Last 1) }
    if (-not $recoveryOk) { $recoveryDetail = (@($recoveryLines | Select-Object -Last 12) -join ' | ') }
    Add-Result 'Installer recovery tests' $recoveryOk $recoveryDetail
}

# 6d. Native installer and deterministic payload contracts.
$installerTests = Join-Path $PSScriptRoot 'Test-InstallerContracts.ps1'
if (Test-Path -LiteralPath $installerTests) {
    $pwshPath = (Get-Process -Id $PID).Path
    $installerOut = & $pwshPath -NoProfile -ExecutionPolicy Bypass -File $installerTests 2>&1
    $installerOk = ($LASTEXITCODE -eq 0)
    $installerLines = @($installerOut | ForEach-Object { "$_" })
    $installerSummary = @($installerLines | Where-Object { $_ -match 'INSTALLER CONTRACTS' } | Select-Object -First 1)
    $installerDetail = if ($installerSummary) { "$($installerSummary[0])".Trim() } else { ($installerLines | Select-Object -Last 1) }
    if (-not $installerOk) { $installerDetail = (@($installerLines | Select-Object -Last 12) -join ' | ') }
    Add-Result 'Installer distribution contracts' $installerOk $installerDetail
}

# 6e. Exact-artifact lifecycle harness fail-closed unit tests.
$lifecycleTests = Join-Path $PSScriptRoot 'Test-LifecycleHarness.ps1'
if (Test-Path -LiteralPath $lifecycleTests) {
    $pwshPath = (Get-Process -Id $PID).Path
    $lifecycleOut = & $pwshPath -NoProfile -ExecutionPolicy Bypass -File $lifecycleTests 2>&1
    $lifecycleOk = ($LASTEXITCODE -eq 0)
    $lifecycleLines = @($lifecycleOut | ForEach-Object { "$_" })
    $lifecycleSummary = @($lifecycleLines | Where-Object { $_ -match 'LIFECYCLE HARNESS TESTS' } | Select-Object -First 1)
    $lifecycleDetail = if ($lifecycleSummary) { "$($lifecycleSummary[0])".Trim() } else { ($lifecycleLines | Select-Object -Last 1) }
    if (-not $lifecycleOk) { $lifecycleDetail = (@($lifecycleLines | Select-Object -Last 12) -join ' | ') }
    Add-Result 'Installer lifecycle harness tests' $lifecycleOk $lifecycleDetail
}

# 6f. Candidate/publish workflows must preserve tested-bits-equal-published-bits.
$releaseWorkflowTests = Join-Path $PSScriptRoot 'Test-ReleaseWorkflowContracts.ps1'
if (Test-Path -LiteralPath $releaseWorkflowTests) {
    $pwshPath = (Get-Process -Id $PID).Path
    $releaseOut = & $pwshPath -NoProfile -ExecutionPolicy Bypass -File $releaseWorkflowTests 2>&1
    $releaseOk = ($LASTEXITCODE -eq 0)
    $releaseLines = @($releaseOut | ForEach-Object { "$_" })
    $releaseSummary = @($releaseLines | Where-Object { $_ -match 'RELEASE WORKFLOW CONTRACTS' } | Select-Object -First 1)
    $releaseDetail = if ($releaseSummary) { "$($releaseSummary[0])".Trim() } else { ($releaseLines | Select-Object -Last 1) }
    if (-not $releaseOk) { $releaseDetail = (@($releaseLines | Select-Object -Last 12) -join ' | ') }
    Add-Result 'Release workflow contracts' $releaseOk $releaseDetail
}

# 6g. Public instructions must describe the native AFK LocalAI distribution.
$productDocsTests = Join-Path $PSScriptRoot 'Test-ProductDocsContracts.ps1'
if (Test-Path -LiteralPath $productDocsTests) {
    $pwshPath = (Get-Process -Id $PID).Path
    $docsOut = & $pwshPath -NoProfile -ExecutionPolicy Bypass -File $productDocsTests 2>&1
    $docsOk = ($LASTEXITCODE -eq 0)
    $docsLines = @($docsOut | ForEach-Object { "$_" })
    $docsSummary = @($docsLines | Where-Object { $_ -match 'PRODUCT DOCS CONTRACTS' } | Select-Object -First 1)
    $docsDetail = if ($docsSummary) { "$($docsSummary[0])".Trim() } else { ($docsLines | Select-Object -Last 1) }
    if (-not $docsOk) { $docsDetail = (@($docsLines | Select-Object -Last 12) -join ' | ') }
    Add-Result 'Product documentation contracts' $docsOk $docsDetail
}

# 7. Static analysis: zero Error-severity, and no NEW automatic-variable
#    assignments beyond the known legacy baseline (burndown list in AGENTS.md).
$autoVarBaseline = 0    # all known automatic-variable shadows fixed; any new hit fails the gate.
if ($SkipAnalyzer) {
    Add-Result 'Static analysis' $true 'SKIPPED (-SkipAnalyzer)'
} elseif (-not (Get-Module -ListAvailable PSScriptAnalyzer)) {
    Add-Result 'Static analysis' $true 'SKIPPED (PSScriptAnalyzer not installed)'
} elseif (-not (Test-Path (Join-Path $Root 'PSScriptAnalyzerSettings.psd1'))) {
    Add-Result 'Static analysis' $true 'SKIPPED (PSScriptAnalyzerSettings.psd1 not in this checkout)'
} else {
    Import-Module PSScriptAnalyzer
    $settings = Join-Path $Root 'PSScriptAnalyzerSettings.psd1'
    $analysis = foreach ($f in $psFiles) { Invoke-ScriptAnalyzer -Path $f.FullName -Settings $settings -ErrorAction SilentlyContinue }
    $errCount = @($analysis | Where-Object Severity -eq 'Error').Count
    Add-Result 'Analyzer: 0 errors' ($errCount -eq 0) "errors=$errCount"
    $autoVar = @($analysis | Where-Object RuleName -eq 'PSAvoidAssignmentToAutomaticVariable').Count
    Add-Result "Analyzer: no new `$args-class (<=$autoVarBaseline)" ($autoVar -le $autoVarBaseline) "current=$autoVar baseline=$autoVarBaseline"
}

# Summary.
$results | Format-Table -AutoSize | Out-String | Write-Host
if ($script:Fail -gt 0) {
    Write-Host "GATE FAILED: $script:Fail check(s) failed." -ForegroundColor Red
    exit 1
}
Write-Host "GATE PASSED: all checks green." -ForegroundColor Green
exit 0
