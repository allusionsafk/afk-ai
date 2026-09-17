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
  $script:Failures.Add($(if ($Detail) { "$Case$([Environment]::NewLine)        $Detail" } else { $Case }))
}

function Require-File {
  param([string]$Relative)
  $path = Join-Path $Root $Relative
  Assert-True "$Relative exists" (Test-Path -LiteralPath $path -PathType Leaf)
  return $path
}

$versionPath = Require-File 'installer/version.json'
$toolchainPath = Require-File 'installer/toolchain.json'
$issPath = Require-File 'installer/AFKLocalAI.iss'
$manifestPath = Require-File 'installer/payload-manifest.txt'
$stagePath = Require-File 'scripts/New-ReleasePayload.ps1'
$buildPath = Require-File 'scripts/Build-Installer.ps1'
$gatePath = Require-File 'scripts/Test-DistributionContracts.ps1'

if (Test-Path -LiteralPath $versionPath) {
  $version = Get-Content -LiteralPath $versionPath -Raw | ConvertFrom-Json
  Assert-True 'canonical installer filename' ($version.installer_name -eq 'AFKLocalAISetup-0.2.0-rc1-x64.exe')
}
if (Test-Path -LiteralPath $toolchainPath) {
  $toolchain = Get-Content -LiteralPath $toolchainPath -Raw | ConvertFrom-Json
  Assert-True 'Inno compiler version is pinned' ($toolchain.inno_setup.version -match '^6\.\d+\.\d+$')
  Assert-True 'Inno installer download digest is pinned' ($toolchain.inno_setup.installer_sha256 -match '^[0-9A-F]{64}$')
}

Write-Host '-- Inno installer contract' -ForegroundColor Cyan
if (Test-Path -LiteralPath $issPath) {
  $iss = Get-ContractText -Path $issPath
  $required = [ordered]@{
    'stable product AppId' = [regex]::Escape('#define TestAppId "{{8A8A2D4D-CE75-4A2D-A39B-56B4206F93D0}"')
    'per-user privileges' = '(?m)^PrivilegesRequired=lowest$'
    '64-bit architecture' = '(?m)^ArchitecturesAllowed=x64compatible$'
    '64-bit install mode' = '(?m)^ArchitecturesInstallIn64BitMode=x64compatible$'
    'per-user program directory' = [regex]::Escape('#define TestDefaultDir "{localappdata}\Programs\AFK LocalAI"')
    'upgrade keeps install directory' = '(?m)^UsePreviousAppDir=yes$'
    'standard uninstall entry' = '(?m)^Uninstallable=yes$'
    'publisher metadata' = '(?m)^AppPublisher=AFK$'
    'support URL metadata' = '(?m)^AppSupportURL='
    'update URL metadata' = '(?m)^AppUpdatesURL='
    'canonical output basename' = [regex]::Escape('OutputBaseFilename=AFKLocalAISetup-{#AppVersion}-x64')
    'numeric Windows product version' = '(?m)^VersionInfoProductVersion=\{#FileVersion\}$'
    'Start Menu app shortcut' = [regex]::Escape('{group}\AFK LocalAI')
    'Start Menu diagnostics shortcut' = '--diagnostics'
    'Start Menu data shortcut' = '--data-folder'
    'Start Menu uninstall shortcut' = [regex]::Escape('{uninstallexe}')
    'optional desktop shortcut' = 'Name: "desktopicon";.*Flags: unchecked'
    'post-install launch' = 'postinstall.*nowait.*skipifsilent'
    'hidden stop on uninstall' = '--stop.*runhidden'
    'uninstall stop runs once' = 'RunOnceId: "StopAFKLocalAI"'
  }
  foreach ($item in $required.GetEnumerator()) {
    Assert-True $item.Key ($iss -match $item.Value) "pattern missing: $($item.Value)"
  }
  Assert-True 'normal installer paths never launch a console host' ($iss -notmatch '(?i)Filename:\s*"\{(?:cmd|sys)\}\\(?:cmd|WindowsPowerShell)')
  Assert-True 'uninstall preserves per-user AFK LocalAI state' ($iss -notmatch '(?i)\[UninstallDelete\][\s\S]*AFK LocalAI')
  Assert-True 'installer embeds no model weights' ($iss -notmatch '(?i)\.gguf|\.safetensors|\.bin\b')
  Assert-True 'lifecycle test AppId can be isolated at compile time' ($iss -match '#ifndef TestAppId' -and $iss -match 'AppId=\{#TestAppId\}')
  Assert-True 'lifecycle test install directory can be isolated at compile time' ($iss -match '#ifndef TestDefaultDir' -and $iss -match 'DefaultDirName=\{#TestDefaultDir\}')
  Assert-True 'lifecycle test Start Menu group can be isolated at compile time' ($iss -match '#ifndef TestGroupName' -and $iss -match 'DefaultGroupName=\{#TestGroupName\}')
}

Write-Host '-- newline portability contract' -ForegroundColor Cyan
# Regression: these contracts used to depend on how the checkout materialised
# rather than on what the file says. .NET's multiline '$' matches immediately
# BEFORE the '\n', so on a CRLF checkout the '\r' is still inside the line and a
# pattern like '(?m)^ArchitecturesAllowed=x64compatible$' does not match. A local
# LF clone passed while GitHub's windows-latest runner - which checks out with
# core.autocrlf=true, and therefore CRLF - failed the identical tree.
if (Test-Path -LiteralPath $issPath) {
  $crlfProbe = Join-Path ([IO.Path]::GetTempPath()) ("afk-crlf-" + [guid]::NewGuid().ToString('n') + ".iss")
  try {
    # Re-materialise the real installer directive exactly as a CRLF checkout would.
    $crlfBody = ((Get-ContractText -Path $issPath) -replace "`n", "`r`n")
    [IO.File]::WriteAllText($crlfProbe, $crlfBody)
    Assert-True 'CRLF probe really is CRLF' ([IO.File]::ReadAllText($crlfProbe).Contains("`r`n"))

    $reread = Get-ContractText -Path $crlfProbe
    Assert-True 'contract reader strips CR from a CRLF checkout' (-not $reread.Contains("`r"))
    Assert-True 'contract reader preserves content' ($reread -eq (Get-ContractText -Path $issPath))
    foreach ($item in $required.GetEnumerator()) {
      Assert-True "survives a CRLF checkout: $($item.Key)" ($reread -match $item.Value) `
        "pattern missing under CRLF: $($item.Value)"
    }
  } finally {
    Remove-Item -LiteralPath $crlfProbe -Force -ErrorAction SilentlyContinue
  }
}

Write-Host '-- deterministic payload contract' -ForegroundColor Cyan
if (Test-Path -LiteralPath $manifestPath) {
  $entries = @(Get-Content -LiteralPath $manifestPath | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') })
  Assert-True 'payload manifest has no duplicates' (($entries | Sort-Object -Unique).Count -eq $entries.Count)
  Assert-True 'payload includes version metadata' ($entries -contains 'installer/version.json')
  Assert-True 'payload includes provisioning entry point' ($entries -contains 'installer/Install-LocalAI.ps1')
  Assert-True 'payload includes recovery entry point' ($entries -contains 'installer/Invoke-Recovery.ps1')
  Assert-True 'payload includes Python package' ($entries -contains 'src/localai/__init__.py')
  Assert-True 'payload includes support and licence' ($entries -contains 'SUPPORT.md' -and $entries -contains 'LICENSE')
  foreach ($entry in $entries) {
    Assert-True "manifest entry is relative: $entry" (-not [IO.Path]::IsPathRooted($entry) -and $entry -notmatch '(^|[\\/])\.\.([\\/]|$)')
    Assert-True "manifest source exists: $entry" (Test-Path -LiteralPath (Join-Path $Root $entry) -PathType Leaf)
    Assert-True "manifest excludes unsafe/generated content: $entry" ($entry -notmatch '(?i)(^|/)(\.git|build|dist|tests?|skill-observations|logs|backups|__pycache__|bin|obj)(/|$)|\.env$|\.gguf$|\.safetensors$|\.pyc$')
  }
}

foreach ($path in @($stagePath, $buildPath, $gatePath)) {
  if (Test-Path -LiteralPath $path) {
    $text = Get-ContractText -Path $path
    Assert-True "$(Split-Path -Leaf $path) uses strict errors" ($text -match '\$ErrorActionPreference\s*=\s*''Stop''')
  }
}
if (Test-Path -LiteralPath $stagePath) {
  $stage = Get-ContractText -Path $stagePath
  Assert-True 'payload builder rejects traversal' ($stage -match 'IsPathRooted' -and $stage -match '\.\.')
  Assert-True 'payload hashes every selected file' ($stage -match 'Get-FileHash' -and $stage -match 'SHA256')
  Assert-True 'payload manifest records source commit' ($stage -match 'source_commit')
  Assert-True 'payload records files in sorted order' ($stage -match 'Sort-Object')
  Assert-True 'payload stages runtime metadata beside the native executable' (
    $stage -match [regex]::Escape("Join-Path `$shell 'version.json'") -and
    $stage -match [regex]::Escape("Join-Path `$staging 'version.json'"))
}
if (Test-Path -LiteralPath $buildPath) {
  $build = Get-ContractText -Path $buildPath
  Assert-True 'build publishes self-contained x64 shell' ($build -match 'dotnet' -and $build -match 'self-contained' -and $build -match 'win-x64')
  Assert-True 'build emits SHA-256 sidecar' ($build -match 'Get-FileHash' -and $build -match 'sha256\.txt')
  Assert-True 'build derives output name from canonical metadata' ($build -match 'installer_name')
  Assert-True 'build verifies the pinned Inno compiler version' ($build -match 'toolchain\.json' -and $build -match 'GetVersionInfo')
}

Write-Host '-- runtime identity and image pinning' -ForegroundColor Cyan
# Regression from a real install. The shipped compose declared the project name
# "localai", which a private engineering workbench on the development machine
# also declares. Starting AFK therefore resolved to the SAME project: same
# container names, same network, and same volumes (localai_open-webui). AFK
# recreated the workbench's containers against its three-month-old Open WebUI
# database; the backend then returned HTTP 500 from /api/config while still
# serving the static frontend, which is Open WebUI's "Backend Required
# (frontend only)" screen - and AFK still reported "ready".
$composePath = Require-File 'docker-compose.yml'
if (Test-Path -LiteralPath $composePath) {
  $compose = Get-ContractText -Path $composePath
  $projectName = [regex]::Match($compose, '(?m)^name:\s*(?<name>\S+)\s*$')
  Assert-True 'compose declares an explicit project name' $projectName.Success
  if ($projectName.Success) {
    $value = $projectName.Groups['name'].Value
    Assert-True 'the project name is AFK-specific' ($value -ne 'localai') "name=$value"
    Assert-True 'the project name identifies this product' ($value -match '^afk') "name=$value"
  }
  # A certified installer cannot promise a working product if its core service
  # image can change underneath it between qualification and first run.
  Assert-True 'Open WebUI is pinned by immutable digest' (
    $compose -match 'open-webui@sha256:[0-9a-f]{64}')
  Assert-True 'Open WebUI is not tracking a mutable tag' (
    $compose -notmatch 'open-webui:main')
  Assert-True 'SearXNG is version pinned' ($compose -match 'searxng/searxng:[0-9]')
  Assert-True 'Kokoro is version pinned' ($compose -match 'kokoro-fastapi-cpu:v[0-9]')
}

$backupPath = Join-Path $Root 'src/localai/backup.py'
if (Test-Path -LiteralPath $backupPath) {
  $backup = Get-ContractText -Path $backupPath
  # Backing up or restoring the wrong project's volume is a data-loss bug.
  Assert-True 'the backup volume fallback is AFK-scoped' (
    $backup -match 'OPEN_WEBUI_VOLUME\s*=\s*"afk-localai_open-webui"')
}

Write-Host '-- uninstall ownership contract' -ForegroundColor Cyan
# Regression: uninstalling AFK LocalAI must only ever stop resources whose AFK
# ownership is proven. The shipped uninstaller used to run "py -m localai stop",
# which resolves through the ambient `localai` name - on a machine that also has
# the private engineering workbench installed editable that unloaded every
# loaded Ollama model, tore down the workbench's compose project, and
# force-closed Docker Desktop and Ollama machine-wide.
$controllerPath = Require-File 'src/AFKLocalAI.App/ProvisioningController.cs'
$ownershipPath = Require-File 'src/localai/afk_ownership.py'
$entryPointPath = Require-File 'installer/afk-payload.py'

if (Test-Path -LiteralPath $controllerPath) {
  $controller = Get-ContractText -Path $controllerPath
  # Every Python-facing command must go through the verified payload entry
  # point. Start and Health once routed through the ambient name too: quieter
  # than the destructive Stop, but just as wrong - the installed product would
  # operate, and report on, whichever checkout won Python resolution.
  Assert-True 'app never invokes the ambient localai package' (
    $controller -notmatch '"-m"\s*,\s*"localai"' -and
    $controller -notmatch '"localai"' -and
    $controller -notmatch '"-3\.12"')
  # Regression: the verified entry point was then launched with py.exe - the
  # right package on whatever interpreter the Python launcher chose, with that
  # interpreter's site-packages and PYTHON* settings. And setup ran pwsh.exe,
  # which a clean Windows 11 does not have.
  Assert-True 'app never starts a Python launcher, PATH Python or PowerShell 7' (
    $controller -notmatch '"py\.exe"' -and $controller -notmatch '"python\.exe"\s*,' -and
    $controller -notmatch '"pwsh\.exe"')
  Assert-True 'app runs AFK AI''s own interpreter by path' (
    $controller -match [regex]::Escape('Path.Combine(_paths.ProgramRoot, "runtime", "python", "python.exe")'))
  foreach ($command in @('Start', 'Stop')) {
    $member = [regex]::Match($controller, "(?s)public ProcessSpec $command\(\).*?;")
    Assert-True "app exposes a $command command" $member.Success
    if ($member.Success) {
      Assert-True "$command routes through the owned engine" ($member.Value -match 'Engine\(')
    }
  }
  foreach ($command in @('Status', 'Diagnostics')) {
    $member = [regex]::Match($controller, "(?s)public ProcessSpec $command\(.*?;\r?\n")
    Assert-True "app exposes a $command command" $member.Success
    if ($member.Success) {
      Assert-True "$command routes through the owned engine" ($member.Value -match 'Engine\(')
    }
  }
  Assert-True 'the engineering health report is not a product command' ($controller -notmatch 'public ProcessSpec Health\(')
  $engineMember = [regex]::Match($controller, '(?s)private ProcessSpec Engine\(.*?\n    \}')
  Assert-True 'app has one engine invocation helper' $engineMember.Success
  if ($engineMember.Success) {
    $engineText = $engineMember.Value
    Assert-True 'engine commands run the by-path entry point' ($engineText -match 'EngineEntryPoint')
    Assert-True 'engine commands run the owned interpreter' ($engineText -match 'RuntimeInterpreter')
    Assert-True 'engine commands pass an explicit program root' ($engineText -match [regex]::Escape('"--program-root"'))
    Assert-True 'engine commands pass an explicit data root' ($engineText -match [regex]::Escape('"--data-root"'))
    Assert-True 'engine commands are isolated from PYTHON* settings' ($engineText -match [regex]::Escape('"-I"'))
    # Importing the payload writes __pycache__ into the program directory.
    # Setup never installed those files, so its uninstaller never removes them,
    # and the installation survives the uninstall.
    Assert-True 'engine commands leave no bytecode behind' ($engineText -match [regex]::Escape('"-B"'))
  }
  Assert-True 'setup runs on the inbox Windows PowerShell' (
    $controller -match '(?s)public ProcessSpec Provision\(.*?WindowsPowerShell\(' -and
    $controller -match [regex]::Escape('"WindowsPowerShell", "v1.0", "powershell.exe"'))
}

if (Test-Path -LiteralPath $entryPointPath) {
  $entry = Get-ContractText -Path $entryPointPath
  Assert-True 'entry point resolves the payload package by path' (
    $entry -match 'sys\.path\.insert' -and $entry -match '"src"')
  Assert-True 'entry point discards an already-imported localai' (
    $entry -match 'del sys\.modules')
  Assert-True 'entry point proves which package answered the import' (
    $entry -match '__file__' -and $entry -match 'startswith')
  Assert-True 'entry point refuses rather than guessing' (
    $entry -match 'Refusing to act')
  Assert-True 'entry point writes no bytecode into the installation' (
    $entry -match 'sys\.dont_write_bytecode\s*=\s*True')
  foreach ($command in @('status', 'start', 'stop', 'diagnostics')) {
    Assert-True "entry point serves $command" ($entry -match "(?s)COMMANDS = \(.*`"$command`".*?\)")
  }
  Assert-True 'entry point does not serve the engineering health report' ($entry -notmatch '"health"')
  # An uninstall must never fail; any other command that did nothing must never
  # report success.
  Assert-True 'a refused uninstall still succeeds' ($entry -match '(?m)^STOP_REFUSAL_EXIT = 0$')
  Assert-True 'every other refused command reports failure' ($entry -match '(?m)^REFUSAL_EXIT = [1-9]$')
  Assert-True 'unknown commands fail closed' ($entry -match 'Usage:')
  # Before importing anything: the interpreter must be THIS installation's own,
  # isolated from site-packages and PYTHON* variables, at the pinned version.
  Assert-True 'entry point proves it runs on the owned interpreter' (
    $entry -match 'def verify_interpreter' -and $entry -match 'RUNTIME_DIR = \("runtime", "python"\)')
  Assert-True 'entry point requires an isolated interpreter' (
    $entry -match 'flags\.isolated' -and $entry -match 'flags\.no_site')
  Assert-True 'entry point checks the runtime version against its pin' ($entry -match 'afk-runtime\.json')
  $verifyCall = $entry.IndexOf('problem = verify_interpreter(program_root)')
  $loadCall = $entry.IndexOf('problem = load_payload(program_root)')
  Assert-True 'entry point verifies the interpreter before loading the payload' (
    $verifyCall -ge 0 -and $loadCall -gt $verifyCall) "verify=$verifyCall load=$loadCall"
}

Write-Host '-- owned Python runtime contract' -ForegroundColor Cyan
$runtimePinPath = Require-File 'installer/python-runtime.json'
$runtimeFetchPath = Require-File 'scripts/Get-PythonRuntime.ps1'
if (Test-Path -LiteralPath $runtimePinPath) {
  $pin = Get-Content -LiteralPath $runtimePinPath -Raw | ConvertFrom-Json
  Assert-True 'runtime pin names conventional CPython' ($pin.implementation -eq 'CPython' -and $pin.version -match '^3\.\d+\.\d+$')
  Assert-True 'runtime pin comes from python.org' ($pin.source_url -match '^https://www\.python\.org/ftp/python/[0-9.]+/python-[0-9.]+-embed-amd64\.zip$')
  Assert-True 'runtime pin version matches its archive' ($pin.source_url -match [regex]::Escape("/$($pin.version)/python-$($pin.version)-embed"))
  Assert-True 'runtime archive digest is pinned' ($pin.sha256 -match '^[0-9A-F]{64}$' -and [int64]$pin.size -gt 1MB)
  Assert-True 'runtime publisher signature is pinned' ($pin.authenticode_subject -match 'O=Python Software Foundation')
  Assert-True 'runtime ships no third-party packages' (@($pin.third_party_packages).Count -eq 0)
  Assert-True 'runtime search path is exactly its own archive and folder' (
    (@($pin.path_file_entries) -join '|') -eq "python$(($pin.version -split '\.')[0..1] -join '').zip|.")
}
if (Test-Path -LiteralPath $runtimeFetchPath) {
  $fetch = Get-ContractText -Path $runtimeFetchPath
  Assert-True 'runtime fetch verifies size and SHA-256' ($fetch -match 'Get-FileHash' -and $fetch -match 'SHA256' -and $fetch -match '\.size')
  Assert-True 'runtime fetch verifies the publisher signature' ($fetch -match 'Get-AuthenticodeSignature' -and $fetch -match 'authenticode_subject')
  Assert-True 'runtime fetch verifies the isolated search path' ($fetch -match 'path_file_entries')
  Assert-True 'runtime fetch proves isolation by running the interpreter' ($fetch -match 'sys\.flags\.isolated' -and $fetch -match 'no_site')
  Assert-True 'runtime fetch only writes under build' ($fetch -match 'outside the build directory')

  # Behaviour, not text: a tampered cached archive must be refused, offline, before
  # anything is extracted or staged.
  $probeRoot = Join-Path $Root ('build/contract-runtime-' + [guid]::NewGuid().ToString('n'))
  try {
    $probeCache = Join-Path $probeRoot 'cache'
    [void](New-Item -ItemType Directory -Path $probeCache -Force)
    $pinned = Get-Content -LiteralPath $runtimePinPath -Raw | ConvertFrom-Json
    $archiveName = [IO.Path]::GetFileName(([Uri]$pinned.source_url).AbsolutePath)
    [IO.File]::WriteAllBytes((Join-Path $probeCache $archiveName), [byte[]](1..64))
    $refused = $false
    try {
      & $runtimeFetchPath -StagingRoot (Join-Path $probeRoot 'runtime') -CacheRoot $probeCache -Offline | Out-Null
    } catch { $refused = $true }
    Assert-True 'a tampered runtime archive is refused offline' $refused
    Assert-True 'a refused runtime is never staged' (-not (Test-Path -LiteralPath (Join-Path $probeRoot 'runtime')))
  } finally {
    Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}
if (Test-Path -LiteralPath $stagePath) {
  $stage = Get-ContractText -Path $stagePath
  Assert-True 'payload requires the owned runtime' ($stage -match '\[Parameter\(Mandatory\)\]\[string\]\$RuntimeRoot')
  Assert-True 'payload refuses links inside the runtime' ($stage -match 'ReparsePoint')
  Assert-True 'payload refuses a runtime with installed packages' ($stage -match 'site-packages')
  Assert-True 'payload hashes the runtime into the manifest' (
    $stage.IndexOf("Join-Path `$staging 'runtime'") -ge 0 -and
    $stage.IndexOf("Join-Path `$staging 'runtime'") -lt $stage.IndexOf('Get-FileHash'))
}
if (Test-Path -LiteralPath $buildPath) {
  $build = Get-ContractText -Path $buildPath
  Assert-True 'build stages the verified runtime before the payload' (
    $build.IndexOf('Get-PythonRuntime.ps1') -ge 0 -and
    $build.IndexOf('Get-PythonRuntime.ps1') -lt $build.IndexOf('New-ReleasePayload.ps1'))
}
if (Test-Path -LiteralPath $issPath) {
  $issText = Get-ContractText -Path $issPath
  # Setup copies new files but never removes ones a newer version dropped; a
  # stale module would stay importable beside a new interpreter.
  foreach ($tree in @('runtime', 'src', 'installer')) {
    Assert-True "update replaces the $tree tree wholesale" (
      $issText -match "(?s)\[InstallDelete\].*Type: filesandordirs; Name: `"\{app\}\\$tree`"")
  }
  Assert-True 'uninstall removes legacy program-folder leftovers only inside {app}' (
    $issText -match '(?s)\[UninstallDelete\].*\{app\}\\logs' -and
    $issText -notmatch '(?s)\[UninstallDelete\].*(localappdata|userappdata|\{%)')
}
$orchestratorPath = Require-File 'installer/Install-LocalAI.ps1'
if (Test-Path -LiteralPath $orchestratorPath) {
  $orchestratorText = Get-ContractText -Path $orchestratorPath
  Assert-True 'setup runs on Windows PowerShell 5.1' ($orchestratorText -match '(?m)^#requires -Version 5\.1$')
  Assert-True 'setup never installs Python machine-wide' ($orchestratorText -notmatch 'Python\.Python')
  Assert-True 'setup never pip-installs into the PC''s Python' (
    $orchestratorText -notmatch '(?i)pip\s+install' -and $orchestratorText -notmatch '[''"]-m[''"]\s*,\s*[''"]pip[''"]')
  Assert-True 'setup never resolves the ambient localai package' ($orchestratorText -notmatch '[''"]-m[''"]\s*,\s*[''"]localai' -and $orchestratorText -notmatch 'Resolve-Python\b')
  Assert-True 'setup runs the owned engine by path' ($orchestratorText -match [regex]::Escape("runtime\python\python.exe") -and $orchestratorText -match 'afk-payload\.py')
}
if (Test-Path -LiteralPath $manifestPath) {
  $shipped = @(Get-Content -LiteralPath $manifestPath | ForEach-Object { $_.Trim() })
  foreach ($module in @(Get-ChildItem -LiteralPath (Join-Path $Root 'src/localai') -Filter *.py -File)) {
    $relative = "src/localai/$($module.Name)"
    & git -C $Root ls-files --error-unmatch -- $relative 1>$null 2>$null
    $tracked = $LASTEXITCODE -eq 0
    if ($tracked -or $module.Name -like 'product_*' -or $module.Name -in @('installation.py', 'diagnostics.py')) {
      Assert-True "payload ships product module $relative" ($shipped -contains $relative)
    }
  }
}

if (Test-Path -LiteralPath $ownershipPath) {
  $ownership = Get-ContractText -Path $ownershipPath
  # Strip the module docstring: it names the forbidden operations in order to
  # explain why they are forbidden.
  $ownershipBody = ($ownership -split '"""', 3)[-1]
  # Operation-shaped, not word-shaped: the module's closing report legitimately
  # mentions Docker Desktop and Ollama to say it left them alone.
  $forbidden = [ordered]@{
    'never force-closes processes' = 'taskkill'
    'never stops Docker Desktop' = 'Docker Desktop\.exe|com\.docker\.backend'
    'never invokes Ollama' = 'ollama\.exe|"ollama"|ollama_path|ollama\s+(stop|rm|ps)'
    'never queries loaded models' = 'api/ps|11434'
    'never prunes Docker' = 'prune'
    'never removes volumes' = 'volume rm|--volumes'
    'never removes containers' = '"down"'
  }
  foreach ($rule in $forbidden.GetEnumerator()) {
    Assert-True "ownership module $($rule.Key)" ($ownershipBody -notmatch $rule.Value) `
      "matched: $($rule.Value)"
  }
  Assert-True 'ownership is decided by the compose config file path' (
    $ownershipBody -match 'com\.docker\.compose\.project\.config_files')
  Assert-True 'ownership requires an absolute program root' (
    $ownershipBody -match 'is_absolute')
  # Compose resolves targets from the project name and the service names in the
  # file, ignoring the config_files label. Verified against a live daemon: a
  # compose stop scoped with one file stopped a container labelled as belonging
  # to a different file. Another checkout can declare the same project name, so
  # acting by project name would hand execution to a weaker key than the proof.
  # Act on the proven container ids instead.
  Assert-True 'ownership stops the proven containers by id' (
    $ownershipBody -match [regex]::Escape('"stop", *stack.container_ids'))
  Assert-True 'ownership never delegates to a project-scoped compose command' (
    $ownershipBody -notmatch [regex]::Escape('"--project-name"') -and
    $ownershipBody -notmatch [regex]::Escape('"compose"'))
  # Path alone is not ownership: the 0.1.7 release candidate left containers
  # with THIS installation's compose path under project "localai", and treating
  # them as owned made Stop refuse and Home read the current stack as stopped.
  Assert-True 'ownership requires the AFK project as well as the config path' (
    $ownershipBody -match [regex]::Escape('AFK_COMPOSE_PROJECT = "afk-localai"') -and
    $ownershipBody -match 'project == AFK_COMPOSE_PROJECT and _same_path' -and
    $ownershipBody -match 'if not is_owned_container\(config_files, project, compose_file\)')
  $readinessText = Get-ContractText -Path (Require-File 'src/localai/readiness.py')
  Assert-True 'readiness decides ownership with the same two-key rule as stop' (
    $readinessText -match 'if not is_owned_container\(config_files, row_project, compose_file\)' -and
    $readinessText -notmatch 'compose_project_name')
  $runtimeText = Get-ContractText -Path (Require-File 'src/localai/product_runtime.py')
  Assert-True 'compose up names the AFK project explicitly' (
    $runtimeText -match '"--project-name",\s*AFK_COMPOSE_PROJECT,' -and
    $runtimeText -match 'env=compose_env\(\)' -and $runtimeText -notmatch 'env=docker_env\(\)')
}

if (Test-Path -LiteralPath $manifestPath) {
  $ownershipEntries = @(Get-Content -LiteralPath $manifestPath | ForEach-Object { $_.Trim() })
  Assert-True 'payload ships the ownership module' (
    $ownershipEntries -contains 'src/localai/afk_ownership.py')
  Assert-True 'payload ships the entry point' (
    $ownershipEntries -contains 'installer/afk-payload.py')
  Assert-True 'payload ships the readiness module' (
    $ownershipEntries -contains 'src/localai/readiness.py')
}

Write-Host ''
if ($script:Fail) {
  Write-Host "FAILURES ($script:Fail):" -ForegroundColor Red
  foreach ($failure in $script:Failures) { Write-Host "  - $failure" -ForegroundColor Red }
  Write-Host "INSTALLER CONTRACTS FAILED: $script:Pass passed, $script:Fail failed." -ForegroundColor Red
  exit 1
}
Write-Host "INSTALLER CONTRACTS PASSED: $script:Pass passed, 0 failed." -ForegroundColor Green
exit 0
