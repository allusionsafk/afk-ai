#requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'contract-common.ps1')
$script:Pass = 0
$script:Fail = 0
$script:Failures = [System.Collections.Generic.List[string]]::new()

function Assert-True {
  param([string]$Case, $Condition, [string]$Detail = '')
  if ([bool]$Condition) { $script:Pass++; return }
  $script:Fail++
  $script:Failures.Add($(if ($Detail) { "$Case :: $Detail" } else { $Case }))
}

$candidatePath = Join-Path $Root '.github/workflows/release-candidate.yml'
$publishPath = Join-Path $Root '.github/workflows/publish-release.yml'
Assert-True 'candidate workflow exists' (Test-Path -LiteralPath $candidatePath -PathType Leaf)
Assert-True 'publish workflow exists' (Test-Path -LiteralPath $publishPath -PathType Leaf)

if (Test-Path -LiteralPath $candidatePath) {
  $candidate = Get-ContractText -Path $candidatePath
  Assert-True 'candidate runs on supported Windows host' ($candidate -match 'runs-on:\s*windows-2025')
  Assert-True 'candidate is manual or pull-request scoped' ($candidate -match 'workflow_dispatch' -and $candidate -match 'pull_request')
  Assert-True 'candidate runs full distribution validation' ($candidate -match 'Test-DistributionContracts\.ps1')
  Assert-True 'candidate builds installer exactly once' (([regex]::Matches($candidate, 'Build-Installer\.ps1')).Count -eq 1)
  Assert-True 'candidate runs isolated upgrade lifecycle' ($candidate -match 'Test-LifecycleUpgrade\.ps1')
  Assert-True 'candidate certifies exact built installer lifecycle' ($candidate -match 'Test-InstallerLifecycle\.ps1' -and $candidate -match 'ExpectedSha256')
  Assert-True 'candidate stages installer and SHA sidecar unchanged' ($candidate -match 'Copy-Item ./dist/\*\.exe' -and $candidate -match '\*\.sha256\.txt')
  Assert-True 'candidate uploads lifecycle evidence' ($candidate -match 'lifecycle-report\.json' -and $candidate -match 'lifecycle-upgrade-report\.json')
  Assert-True 'candidate uploads one flat publishable bundle' ($candidate -match 'build/candidate-bundle/\*\*')
  Assert-True 'candidate artifact retention is explicit' ($candidate -match 'retention-days:\s*\d+')
  Assert-True 'candidate third-party actions are commit pinned' ($candidate -notmatch '(?m)^\s*uses:\s*[^\s]+@(v\d+|main|master)\s*$')
}

if (Test-Path -LiteralPath $publishPath) {
  $publish = Get-ContractText -Path $publishPath
  Assert-True 'publish is manual only' ($publish -match 'workflow_dispatch' -and $publish -notmatch '(?m)^\s*(push|pull_request|release):')
  Assert-True 'publish downloads a candidate run artifact' ($publish -match 'gh run download' -and $publish -match 'candidate_run_id')
  Assert-True 'publish accepts candidates only from the default branch' ($publish -match 'github\.event\.repository\.default_branch' -and $publish -match 'head_branch')
  Assert-True 'publish never rebuilds installer bytes' ($publish -notmatch 'Build-Installer|dotnet\s+publish|ISCC|winget\s+install')
  Assert-True 'publish verifies lifecycle certification' ($publish -match 'certified' -and $publish -match 'lifecycle-report\.json')
  Assert-True 'publish verifies exact SHA before release' ($publish -match 'Get-FileHash' -and $publish -match 'SHA256')
  Assert-True 'publish handles prerelease explicitly' ($publish -match '\-\-prerelease')
  Assert-True 'publish cannot mark a prerelease latest' ($publish -match '\-\-latest=false')
  Assert-True 'publish release command includes SHA sidecar' ($publish -match 'gh release create' -and $publish -match 'sha256\.txt')
  Assert-True 'publish permissions are least scoped for release' ($publish -match 'contents:\s*write' -and $publish -match 'actions:\s*read')
}

$project = Get-ContractText -Path (Join-Path $Root 'pyproject.toml')
Assert-True 'mypy major version is bounded for reproducible validation' ($project -match '"mypy>=1\.11,<2"')

Write-Host ''
if ($script:Fail) {
  Write-Host "FAILURES ($script:Fail):" -ForegroundColor Red
  foreach ($failure in $script:Failures) { Write-Host "  - $failure" -ForegroundColor Red }
  Write-Host "RELEASE WORKFLOW CONTRACTS FAILED: $script:Pass passed, $script:Fail failed." -ForegroundColor Red
  exit 1
}
Write-Host "RELEASE WORKFLOW CONTRACTS PASSED: $script:Pass passed, 0 failed." -ForegroundColor Green
exit 0
