# AFK LocalAI Windows Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a professional, per-user, lifecycle-tested
`AFKLocalAISetup-0.2.0-rc1-x64.exe` with a native first-run and recovery shell.

**Architecture:** A self-contained .NET 8 Windows Forms shell presents setup
and daily-use UI while invoking the repo's PowerShell/Python provisioning engine
with hidden subprocesses. Inno Setup installs a manifest-selected payload under
the user's LocalAppData; versioned state and logs live outside program files. A
candidate workflow builds once and lifecycle-tests the exact artifact, while a
separate publish workflow verifies and publishes those bytes without rebuilding.

**Tech Stack:** C#/.NET 8 Windows Forms, PowerShell 5.1/7, Python 3.12, Inno
Setup 6, GitHub Actions on `windows-2025`.

**Spec:** `docs/superpowers/specs/2026-09-08-afk-localai-windows-distribution-design.md`

## Global Constraints

- User-facing product name is `AFK LocalAI`; internal `localai` names may remain.
- Target display version is `0.2.0-rc1`; Windows file version is `0.2.0.0`.
- Installer filename is `AFKLocalAISetup-0.2.0-rc1-x64.exe`.
- Stable AppId is `{8A8A2D4D-CE75-4A2D-A39B-56B4206F93D0}`.
- Default install directory is `{localappdata}\Programs\AFK LocalAI`.
- User state and logs are preserved in `{localappdata}\AFK LocalAI`.
- Normal app and provisioning launches must not display console windows.
- Model weights are downloaded after install and are never embedded.
- Preflight is read-only; mutating recovery actions are explicit and bounded.
- Readiness is always revalidated live before expensive provisioning resumes.
- Candidate and publish workflows must use the same installer bytes.
- No stable release is published during this implementation session.

---

### Task 1: Versioned product contract

**Files:**
- Create: `installer/version.json`
- Create: `tests/test_distribution_contract.py`
- Modify: `pyproject.toml`
- Modify: `src/localai/__init__.py`

**Interfaces:**
- Consumes: current Python package metadata and public Friend Beta version.
- Produces: canonical fields `product_name`, `display_version`, `file_version`,
  `channel`, `architecture`, `app_id`, `publisher`, `repository`, `support_url`.

- [ ] **Step 1: Write the failing distribution contract tests**

  Add tests that load `installer/version.json` and require the exact global
  values, require PEP 440 package version `0.2.0rc1`, require display version
  `0.2.0-rc1`, and reject unrelated product/release workflow names.

- [ ] **Step 2: Run the focused test and verify RED**

  Run: `python -m pytest tests/test_distribution_contract.py -q`

  Expected: failure because `installer/version.json` does not exist and package
  metadata is still `0.1.1`.

- [ ] **Step 3: Add canonical metadata and align Python metadata**

  Create the JSON contract and set both Python version sources to `0.2.0rc1`.
  Change the package description from private-workbench wording to public AFK
  LocalAI distribution wording.

- [ ] **Step 4: Run focused and scaffold tests**

  Run: `python -m pytest tests/test_distribution_contract.py tests/test_python_scaffold.py -q`

  Expected: all pass.

- [ ] **Step 5: Commit**

  `git commit -m "chore: establish AFK LocalAI release identity"`

### Task 2: Early Windows environment preflight

**Files:**
- Create: `installer/preflight.ps1`
- Create: `installer/Get-Preflight.ps1`
- Create: `tests/Test-InstallerPreflight.ps1`
- Modify: `installer/installer-common.ps1`
- Modify: `installer/Install-LocalAI.ps1`
- Modify: `tests/Invoke-Checks.ps1`

**Interfaces:**
- Consumes: fixture or live evidence objects with `Platform`, `Firmware`,
  `WindowsVirtualization`, `Wsl`, and `Docker` properties.
- Produces: `Invoke-EnvironmentPreflight -Evidence <object>` result with
  `Overall`, `Reason`, `Code`, `Action`, `RebootRequired`, component statuses,
  and `ClassifierVersion`; `Get-Preflight.ps1 -Json` emits one JSON object.

- [ ] **Step 1: Port only the fixture suite from PR #4**

  Bring in `tests/Test-InstallerPreflight.ps1` first, preserving the two
  adversarial cases from the owner review: healthy Hyper-V Docker with WSL
  disabled is READY, and unavailable feature evidence with no hypervisor is
  UNKNOWN.

- [ ] **Step 2: Run the preflight suite and verify RED**

  Run: `pwsh -NoProfile -File tests/Test-InstallerPreflight.ps1`

  Expected: failure because `installer/preflight.ps1` does not exist.

- [ ] **Step 3: Port and rebase the classifier/state seam**

  Port the final PR #4 classifier, state changes, orchestrator Phase 0, and gate
  registration using focused patches. Preserve current `master` CI/public-audit
  improvements. State schema version 2 uses a caller-supplied data root and
  quarantines corrupt state instead of asking for manual deletion.

- [ ] **Step 4: Add the shell-facing JSON entry point**

  `Get-Preflight.ps1 -Json -DataRoot <path>` dot-sources the common and
  preflight functions, obtains live evidence, classifies it, persists a bounded
  checkpoint, and emits JSON without ANSI/control output.

- [ ] **Step 5: Run preflight and operational gates**

  Run: `pwsh -NoProfile -File tests/Test-InstallerPreflight.ps1`

  Run: `pwsh -NoProfile -File tests/Invoke-Checks.ps1`

  Expected: preflight matrix and repository gate pass.

- [ ] **Step 6: Run a real read-only host probe**

  Run: `powershell -NoProfile -ExecutionPolicy Bypass -File installer/Get-Preflight.ps1 -Json -DataRoot build/preflight-live`

  Expected: valid JSON with a stable reason code; no feature, service, context,
  registry, or package mutation.

- [ ] **Step 7: Commit**

  `git commit -m "feat(installer): add early resumable environment preflight"`

### Task 3: Recoverable provisioning actions and event protocol

**Files:**
- Create: `installer/recovery.ps1`
- Create: `installer/Invoke-Recovery.ps1`
- Create: `tests/Test-InstallerRecovery.ps1`
- Modify: `installer/Install-LocalAI.ps1`
- Modify: `installer/installer-common.ps1`
- Modify: `tests/Invoke-Checks.ps1`

**Interfaces:**
- Consumes: a preflight reason code and explicit action id.
- Produces: `Get-PreflightRecovery -Code <code>` with `ActionId`, `Label`,
  `RequiresElevation`, `Automatic`, and `Explanation`; `Invoke-Recovery.ps1`
  exits `0` success, `10` reboot/relaunch required, `20` user-guidance only,
  or `1` failure and emits `AFK-EVENT:<json>` progress records.

- [ ] **Step 1: Write failing recovery mapping/state tests**

  Cover every preflight code, require no automatic BIOS/BCD/context/container
  mode mutation, verify exact allowlisted commands for WSL/Docker actions,
  require atomic state writes, and require live-preflight invalidation after a
  recovery attempt.

- [ ] **Step 2: Run recovery tests and verify RED**

  Run: `pwsh -NoProfile -File tests/Test-InstallerRecovery.ps1`

  Expected: failure because recovery functions do not exist.

- [ ] **Step 3: Implement pure recovery mapping then bounded executor**

  Implement mappings first. Executor supports only `install-wsl`, `update-wsl`,
  `install-docker`, `update-docker`, `start-docker`, `restart-windows`, and
  `retry`. Elevation runs one checked script/action and does not elevate the app.

- [ ] **Step 4: Add structured provisioning events**

  Add `Write-InstallerEvent` and emit phase start/success/failure/checkpoint
  events while retaining readable console output for maintainer use. Add
  `-DataRoot`, `-EventStream`, and `-LegacyInstallRoot` parameters.

- [ ] **Step 5: Run recovery, preflight, and operational suites**

  Expected: all pass without performing live mutations in tests.

- [ ] **Step 6: Commit**

  `git commit -m "feat(installer): add bounded recovery and progress events"`

### Task 4: Native application core and no-console process contract

**Files:**
- Create: `src/AFKLocalAI.App/AFKLocalAI.App.csproj`
- Create: `src/AFKLocalAI.App/ProductInfo.cs`
- Create: `src/AFKLocalAI.App/AppPaths.cs`
- Create: `src/AFKLocalAI.App/ProcessSpec.cs`
- Create: `src/AFKLocalAI.App/HiddenProcessRunner.cs`
- Create: `src/AFKLocalAI.App/ProvisioningModels.cs`
- Create: `src/AFKLocalAI.App/ProvisioningStateStore.cs`
- Create: `src/AFKLocalAI.App/ProvisioningController.cs`
- Create: `tests/AFKLocalAI.App.Tests/AFKLocalAI.App.Tests.csproj`
- Create: `tests/AFKLocalAI.App.Tests/Program.cs`

**Interfaces:**
- `ProductInfo.Load(path)` validates `version.json`.
- `AppPaths.ForCurrentUser(baseOverride)` returns program/data/state/log paths.
- `ProcessSpec.Hidden(file, args, cwd)` always returns redirected,
  `UseShellExecute=false`, `CreateNoWindow=true`, hidden-window settings.
- `ProvisioningStateStore.LoadOrCreate()` quarantines corrupt JSON and
  `SaveAtomic(state)` uses temp-plus-replace.
- `ProvisioningController` builds preflight, recovery, provision, start, stop,
  health, diagnostics, and dashboard command specs.

- [ ] **Step 1: Create the package-free console test harness first**

  Tests require exact paths, metadata validation, corrupt-state quarantine,
  atomic roundtrip, event parsing, recovery-button mapping, and no-console
  process flags. The harness returns nonzero on any assertion failure.

- [ ] **Step 2: Run tests and verify RED**

  Run: `dotnet run --project tests/AFKLocalAI.App.Tests -c Release`

  Expected: compilation fails because production types do not exist.

- [ ] **Step 3: Implement minimal core types**

  Target `net8.0-windows`, `win-x64`, nullable/implicit usings enabled, no NuGet
  packages. Keep process construction separate from execution so tests inspect
  flags without starting commands.

- [ ] **Step 4: Run core harness and build**

  Run: `dotnet run --project tests/AFKLocalAI.App.Tests -c Release`

  Run: `dotnet build src/AFKLocalAI.App/AFKLocalAI.App.csproj -c Release`

  Expected: all assertions and build pass without warnings.

- [ ] **Step 5: Commit**

  `git commit -m "feat(app): add native provisioning core"`

### Task 5: Professional first-run and daily-use shell

**Files:**
- Create: `src/AFKLocalAI.App/Program.cs`
- Create: `src/AFKLocalAI.App/MainForm.cs`
- Create: `src/AFKLocalAI.App/Theme.cs`
- Create: `src/AFKLocalAI.App/AppIcon.cs`
- Create: `src/AFKLocalAI.App/app.manifest`
- Modify: `tests/AFKLocalAI.App.Tests/Program.cs`

**Interfaces:**
- Default launch opens `MainForm` in setup or home mode from live state.
- `--self-test --data-root <path>` runs metadata, path, state, preflight-command,
  and hidden-process checks without UI or system mutation.
- `--diagnostics`, `--data-folder`, and `--about` open focused shell modes.

- [ ] **Step 1: Add failing shell contract tests**

  Require `--self-test` exit 0 and a JSON summary, single-instance mutex naming,
  application icon availability, accessible labels, setup/home mode rules, and
  every external console operation going through `HiddenProcessRunner`.

- [ ] **Step 2: Run harness and verify RED**

  Expected: failures for missing entry point/form contracts.

- [ ] **Step 3: Implement UI and command-line modes**

  Build an accessible, DPI-aware two-mode shell. Setup shows component cards,
  intent selection, progress, primary recovery/setup action, Retry, Diagnostics,
  and collapsible details. Home shows Open Chat, Start, Stop, Health, Repair,
  Diagnostics, Data Folder, About, and Support. Buttons remain responsive while
  async hidden subprocesses stream progress.

- [ ] **Step 4: Verify shell without provisioning**

  Run the core harness, `dotnet publish` self-contained, then run the published
  `AFKLocalAI.exe --self-test` against a disposable data root.

- [ ] **Step 5: Commit**

  `git commit -m "feat(app): add AFK LocalAI setup and launcher UI"`

### Task 6: Deterministic payload and Inno installer

**Files:**
- Create: `installer/payload-manifest.txt`
- Create: `installer/AFKLocalAI.iss`
- Create: `scripts/New-ReleasePayload.ps1`
- Create: `scripts/Build-Installer.ps1`
- Create: `scripts/Test-DistributionContracts.ps1`
- Create: `tests/Test-InstallerContracts.ps1`
- Modify: `.gitignore`
- Modify: `tests/Invoke-Checks.ps1`

**Interfaces:**
- `New-ReleasePayload.ps1 -SourceRoot -StagingRoot -ShellPublishRoot -Commit`
  copies only manifest entries and writes `payload-manifest.json` containing
  sorted relative paths and uppercase SHA-256 values.
- `Build-Installer.ps1 -Configuration Release [-SkipTests]` emits exact EXE and
  sidecar under `dist/`.
- Inno accepts `/DSourceRoot`, `/DAppVersion`, `/DFileVersion`, `/DChannel`, and
  `/DSourceCommit` and writes the canonical filename.

- [ ] **Step 1: Write failing installer/payload contract tests**

  Require the stable AppId, lowest privilege, 64-bit mode, install/data paths,
  Start Menu entries, optional desktop task, uninstall metadata, hidden cleanup,
  version/channel naming, no model files, and manifest exclusions.

- [ ] **Step 2: Run tests and verify RED**

  Run: `pwsh -NoProfile -File tests/Test-InstallerContracts.ps1`

  Expected: failure because installer definition and scripts do not exist.

- [ ] **Step 3: Implement manifest staging and contract gate**

  Reject absolute/traversal paths, missing files, duplicates, untracked secret
  files, and any staged file not in the manifest. Normalize timestamps for the
  staging payload and emit a deterministic sorted hash manifest.

- [ ] **Step 4: Implement Inno definition and build script**

  Configure per-user install, stable AppId, shortcuts, postinstall launch,
  version metadata, upgrade-in-place, and best-effort hidden stop on uninstall.
  Locate Inno in standard install paths or fail with one maintainer instruction.

- [ ] **Step 5: Run contracts and build installer**

  Install pinned Inno Setup if absent, then run
  `pwsh -NoProfile -File scripts/Build-Installer.ps1 -Configuration Release`.

  Expected: canonical EXE, sidecar, and payload manifest.

- [ ] **Step 6: Commit**

  `git commit -m "feat(distribution): build per-user Inno installer"`

### Task 7: Exact-artifact lifecycle qualification

**Files:**
- Create: `scripts/Test-InstallerLifecycle.ps1`
- Create: `scripts/Test-LifecycleUpgrade.ps1`
- Create: `tests/Test-LifecycleHarness.ps1`
- Modify: `scripts/Build-Installer.ps1`
- Modify: `installer/AFKLocalAI.iss`

**Interfaces:**
- `Test-InstallerLifecycle.ps1 -InstallerPath -ExpectedSha256 -ReportPath
  [-DisposableRoot]` installs the supplied bytes, runs installed self-test,
  validates shortcuts/uninstall entry, uninstalls, and writes JSON evidence.
- `Test-LifecycleUpgrade.ps1` builds an isolated test-AppId predecessor,
  creates a user-state sentinel, upgrades to a test-AppId candidate, checks the
  sentinel and version, then uninstalls while preserving state.

- [ ] **Step 1: Write failing harness self-tests**

  Simulate installer/uninstaller exit codes and filesystem/registry outcomes.
  Require fail-closed behavior on digest mismatch, missing uninstall entry,
  version mismatch, leftover program files, or missing preserved state.

- [ ] **Step 2: Run harness tests and verify RED**

  Expected: failure because lifecycle functions do not exist.

- [ ] **Step 3: Implement lifecycle harness**

  Use explicit resolved paths, a disposable data override, process timeouts,
  and cleanup only for paths/registry entries created by the test. Capture
  installer and uninstaller logs in the report directory.

- [ ] **Step 4: Test upgrade preservation with isolated AppId**

  Build predecessor and candidate smoke installers under a test AppId. Verify
  program version replacement, settings/state sentinel preservation, and clean
  uninstall without touching the production AppId.

- [ ] **Step 5: Test exact release-candidate bytes on the real host**

  Confirm production AppId is absent, install the canonical EXE silently for
  current user, run installed `--self-test`, validate uninstall metadata and
  shortcuts, uninstall, and verify installed files are removed while disposable
  data is retained.

- [ ] **Step 6: Commit**

  `git commit -m "test(distribution): qualify install upgrade and uninstall"`

### Task 8: Candidate and no-rebuild publish workflows

**Files:**
- Create: `.github/workflows/windows-installer-candidate.yml`
- Create: `.github/workflows/windows-installer-publish.yml`
- Create: `scripts/Verify-CertifiedArtifact.ps1`
- Create: `tests/test_release_workflow_behavior.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Candidate artifact contains installer, sidecar, payload manifest, and lifecycle
  report named `AFKLocalAI-<version>-x64-candidate`.
- Publish inputs: `candidate_run_id`, `version`, `expected_sha256`, `channel`.
- `Verify-CertifiedArtifact.ps1` checks all coordinates and the lifecycle
  report's certified digest before release creation.

- [ ] **Step 1: Write failing workflow tests**

  Require pinned Actions SHAs, minimal permissions, candidate exact-byte
  lifecycle invocation, private artifact upload, publish download by run ID,
  no build command in publish, digest/lifecycle validation, and correct
  prerelease/stable mapping.

- [ ] **Step 2: Run test and verify RED**

  Expected: failure because workflows do not exist.

- [ ] **Step 3: Implement candidate workflow**

  On manual dispatch and release-candidate branches, set up .NET 8, Python 3.12,
  pinned Inno Setup, build once, lifecycle-test the exact EXE, and upload evidence.

- [ ] **Step 4: Implement publish workflow**

  Manual only. Download candidate artifact from the named run, verify all
  evidence, enforce version/channel rules, create tag/release at the recorded
  source commit, and upload EXE plus checksum. Do not build or test substitute
  bytes in this workflow.

- [ ] **Step 5: Run workflow and full repository gates**

  Run release tests, all Python tests, ruff, mypy under Python 3.12, operational
  checks, .NET tests/build, installer contracts, public audit, and `git diff --check`.

- [ ] **Step 6: Commit**

  `git commit -m "ci(release): publish only certified installer bytes"`

### Task 9: User-facing documentation and release handoff

**Files:**
- Modify: `README.md`
- Modify: `installer/README.md`
- Modify: `SUPPORT.md`
- Create: `docs/releases/0.2.0-rc1.md`
- Create: `docs/distribution.md`
- Modify: `docs/README.md`

**Interfaces:**
- User docs begin with download/double-click/launch and contain no required
  PowerShell, Git, PATH, Docker CLI, or developer-tool step.
- Maintainer docs record build, candidate qualification, exact-byte publish,
  external clean-machine qualification, and accidental-release cleanup.

- [ ] **Step 1: Add documentation assertions to distribution tests**

  Require canonical filename, Windows 11 scope, local-first network truth,
  unsigned-beta warning without security-disable advice, state preservation,
  logs, support, and explicit remaining qualification.

- [ ] **Step 2: Run focused test and verify RED**

  Expected: failures against current `.cmd`/PowerShell-first copy.

- [ ] **Step 3: Rewrite user and maintainer distribution docs**

  Keep advanced source instructions secondary. Explain prerequisite recovery in
  user language and publish the exact candidate limitations honestly.

- [ ] **Step 4: Run public audit and complete validation**

  Run every gate listed in Task 8 plus installer lifecycle tests against the
  final artifact. Record commands, counts, paths, hashes, and machine-dependent
  omissions for the final handoff.

- [ ] **Step 5: Commit**

  `git commit -m "docs: ship AFK LocalAI Friend Beta installer guidance"`

### Task 10: Independent review, push, and pull request

**Files:**
- Review all changes relative to `origin/master`.

**Interfaces:**
- Produces: coherent commit series, pushed branch, and a PR with release evidence
  and an explicit Friend Beta readiness verdict.

- [ ] **Step 1: Run adversarial review probes**

  Check malformed state, contradictory preflight evidence, remote Docker
  endpoints, path traversal in payload manifests, version/channel mismatch,
  digest substitution, silent-process flags, upgrade sentinel loss, and
  uninstall overreach.

- [ ] **Step 2: Run final verification from a clean worktree state**

  Re-run all automated gates and the exact installer lifecycle. Confirm no
  secrets, private paths, debug leftovers, source archives, or unrelated product
  files are tracked.

- [ ] **Step 3: Review commit/worktree identity**

  Run `git status --short --branch`, `git rev-parse --show-toplevel`,
  `git rev-parse HEAD`, `git branch --show-current`, `git merge-base HEAD
  origin/master`, and `git worktree list --porcelain` in the intended worktree.

- [ ] **Step 4: Push and open/update PR**

  Push `codex/afk-localai-friend-beta-rc`, open a PR to `master`, and include
  installer path/SHA, lifecycle results, known external qualification, and the
  accidental Adaptive Media release cleanup note. Do not publish a release.
