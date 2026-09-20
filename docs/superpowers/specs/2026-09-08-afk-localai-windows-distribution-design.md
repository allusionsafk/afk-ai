# AFK LocalAI Windows Distribution Design

**Status:** Approved for autonomous Friend Beta release-candidate implementation
**Version target:** `0.2.0-rc1`
**Public repository:** `allusionsafk/afk-ai`

## Outcome

The normal distribution is one per-user Windows installer:

`AFKLocalAISetup-0.2.0-rc1-x64.exe`

It installs a native AFK LocalAI application shell and the small, reviewed
application payload. It does not bundle model weights. The installed shell owns
first-run checks, prerequisite recovery, provisioning progress, diagnostics,
launching, and support links. Normal use never opens a PowerShell or Command
Prompt window.

## Ground Truth and Problems Being Replaced

The public `master` branch at `2339c9ac7f1401b98d56c7602881201fffcbffac`
has a tested Python control layer and a PowerShell Friend Bootstrapper, but its
public distribution is still a downloaded `.cmd` that fetches a mutable
bootstrap script. The current installer:

- installs a repository checkout into `%USERPROFILE%\localai`;
- requires visible console interaction and exposes script/developer concepts;
- has no Windows uninstall entry, stable application identity, or installed
  Start Menu application;
- has no release workflow that builds an AFK LocalAI executable installer;
- has a version split between Friend Beta `0.1.7rc1` and Python package `0.1.1`;
- records unversioned state next to installer source and asks users to delete a
  corrupt state file manually;
- checks Docker too late on `master` and does not distinguish the important
  virtualization, WSL, context, engine, and restart states;
- has no clean install/upgrade/uninstall lifecycle test;
- has no AFK LocalAI release artifact or checksum attached to the current beta.

The repository also currently contains unrelated Adaptive Media branches, tags,
and Releases. `Adaptive Media 0.3.2` is marked Latest in the AFK LocalAI
repository. This is a public release-hygiene defect. Existing releases are not
deleted by this implementation; the final handoff will identify the exact
cleanup required.

Useful existing work is retained selectively:

- PR #4's fixture-tested preflight classifier is the base for early checks,
  after rebasing and rerunning its adversarial cases.
- PR #13's immutable-hop regression test informs removal of the `.cmd` download
  path from the normal flow.
- Adaptive Media's useful release pattern is reused: build a private candidate,
  test that exact artifact, and publish by downloading rather than rebuilding
  it. Adaptive Media is not a runtime or source dependency.

## Product Identity and Layout

User-facing product name is **AFK LocalAI**. Internal Python imports and legacy
script filenames may remain `localai` where changing them adds risk without
improving the installed experience.

- Installer: `AFKLocalAISetup-<display-version>-x64.exe`
- Application: `AFKLocalAI.exe`
- Stable Inno Setup AppId: `{8A8A2D4D-CE75-4A2D-A39B-56B4206F93D0}`
- Publisher: `AFK`
- Default program directory: `{localappdata}\Programs\AFK LocalAI`
- User data: `{localappdata}\AFK LocalAI`
- State: `{localappdata}\AFK LocalAI\State`
- Logs: `{localappdata}\AFK LocalAI\Logs`
- Diagnostics: `{localappdata}\AFK LocalAI\Diagnostics`
- Start Menu folder: `AFK LocalAI`

`installer/version.json` is the release metadata source. It contains the display
version, four-part Windows file version, release channel, product name, AppId,
and source repository. The build fails if Python metadata, application assembly
metadata, Inno definitions, output filename, or release workflow inputs disagree.

## Installed Architecture

### Native shell

`AFKLocalAI.exe` is a self-contained `win-x64` .NET 8 Windows Forms application.
It has no dependency on an already installed .NET runtime and uses only Microsoft
framework libraries. The shell is intentionally small and divided into:

- immutable product/version metadata;
- path and state services;
- a no-console process runner with streaming output and cancellation;
- provisioning state and event models;
- preflight/recovery command mapping;
- the Windows Forms presentation layer.

The default window has two modes:

1. **Set up this PC** for an unprovisioned or interrupted installation. It shows
   PC checks, intent choices, the current stage, progress, one next action, and a
   collapsible details/log view.
2. **AFK LocalAI home** after provisioning. It offers Open Chat, Start, Stop,
   Check Health, Repair Setup, Diagnostics, Data Folder, About, and Support.

External web pages and Windows GUI applications may open normally. Every console
child uses `UseShellExecute=false`, redirected output, `CreateNoWindow=true`, and
`WindowStyle=Hidden`.

### Reviewed payload

The installer stages a manifest-selected payload containing the PowerShell
installer/recovery scripts, Python package, compose configuration, model recipes,
static dashboard assets, licence, security/support docs, and version metadata.
Git metadata, tests, build output, caches, logs, secrets, observer state, and
machine-specific files are excluded.

The installed payload is immutable application material. Mutable state lives in
the user-data paths. Docker volumes and Ollama's model store remain managed by
their runtimes and are not embedded in the installer.

### Provisioning engine

PowerShell remains the system-integration engine because the public repo already
has substantial reviewed behavior there. The shell invokes it invisibly and
renders structured events as application progress.

The read-only preflight script remains Windows PowerShell 5.1 compatible so the
first useful screen does not depend on installing PowerShell 7. PowerShell 7 is
provisioned only when the later orchestrator needs it. The existing Python
control package remains the runtime controller after Python is installed.

## First-Run and Recovery State Machine

Every launch begins with live read-only evidence. Saved state is an audit and
resume checkpoint, never proof that a prerequisite is still healthy.

1. Load `provisioning-state.json` schema version 2.
2. If JSON is corrupt, atomically rename it to
   `provisioning-state.corrupt-<UTC timestamp>.json`, start a clean state, and
   tell the user where the quarantined file is.
3. Detect an earlier script installation at `%USERPROFILE%\localai`. Report it
   as a legacy installation, preserve it, and import only explicitly supported
   settings such as an existing `.env`; never delete or overwrite it silently.
4. Probe and classify:
   - supported 64-bit Windows 11 build;
   - firmware virtualization evidence;
   - Windows hypervisor/optional-feature/restart state;
   - WSL command, version, and health where the selected Docker backend needs it;
   - Docker Desktop installation, local endpoint, Linux engine, version, startup,
     and health;
   - GPU name/VRAM, RAM, CPU, and free disk;
   - the supported runtime and tier/model path.
5. Render all component results, but choose one overall blocker by documented
   precedence. Unknown or contradictory evidence fails closed before model pulls.
6. Offer exactly one safe next action:
   - firmware disabled: BIOS/UEFI guidance only;
   - Windows/WSL missing: bounded elevated `wsl --install --no-distribution`;
   - WSL outdated: bounded elevated `wsl --update`;
   - restart required: save checkpoint and offer Restart now/later;
   - Docker absent: `winget install Docker.DockerDesktop`;
   - Docker stopped: launch Docker Desktop and poll readiness;
   - Docker outdated: `winget upgrade Docker.DockerDesktop`;
   - remote Docker context or Windows-container engine: precise manual recovery,
     with no automatic mutation of an advanced user's Docker configuration;
   - unsupported or unknown: stop with a reason code and support action.
7. Re-run live preflight after every recovery action or application restart.
8. Once ready, provision PowerShell 7, Python 3.12, the Python package, Ollama,
   the hardware-fit model, compose services, WebUI seed, network guardrails, and
   health checks. Structured phase events and the full transcript are logged.
9. Mark `usable=true` only after the end-to-end health gate passes. Show Open
   Chat as the primary action.

Supported recovery actions are idempotent. Each phase records start, completion,
failure code, attempt count, and UTC time through atomic replace. A failed phase
is retryable; completed mutating phases are skipped only after their required
postcondition is revalidated.

## Installer Behavior

Inno Setup builds a 64-bit per-user installer with `PrivilegesRequired=lowest`
and a stable AppId. It creates:

- an AFK LocalAI Start Menu application shortcut;
- Start Menu shortcuts for Diagnostics, Data Folder, Support, and Uninstall;
- an optional Desktop shortcut, unchecked by default;
- a standard Apps & Features uninstall entry with display version, publisher,
  icon, update URL, and support URL;
- a post-install Launch AFK LocalAI checkbox.

Upgrade installs in place under the same AppId. It replaces only manifest-owned
program files and preserves user data, Docker volumes, models, settings, and
provisioning checkpoints. Downgrades are refused unless a maintainer-only test
flag is used.

Normal uninstall removes shortcuts and manifest-owned program files. It stops
AFK LocalAI-owned compose services on a best-effort hidden path. It deliberately
preserves user data and downloaded models by default and tells the user where
they remain; destructive data/model removal is not silently coupled to uninstall.

## Diagnostics and Support

Logs are UTF-8, timestamped, bounded, and stored outside the application
directory. Normal failures show:

- a plain-language headline;
- stable reason code;
- one next action;
- Retry and Open Diagnostics actions;
- the precise log path.

The diagnostics command writes a support bundle containing product version,
Windows build, classifier statuses, hardware summary, installed prerequisite
versions, Docker endpoint kind, and bounded log tails. It excludes environment
variable values, Docker endpoint addresses, usernames, home paths, tokens,
secrets, chat content, model prompts, and full third-party logs.

About shows AFK LocalAI, display version, release channel, source commit when
available, licence, source URL, Support URL, and the not-affiliated-with-mudler
notice.

## Build and Release Discipline

`scripts/Build-Installer.ps1`:

1. validates a clean source tree and release metadata;
2. runs the Python, PowerShell, .NET, installer-contract, and public-audit gates;
3. publishes the self-contained native shell;
4. creates a clean staging directory from `installer/payload-manifest.txt`;
5. records SHA-256 for every staged file plus source commit in
   `payload-manifest.json`;
6. compiles Inno Setup with a pinned major/minor compiler expectation;
7. emits the exact installer and its `.sha256.txt` sidecar;
8. runs shell self-tests and installer lifecycle tests against the emitted EXE.

The candidate workflow builds once on `windows-2025`, installs and uninstalls
the exact output in a disposable runner account, and uploads the EXE, checksum,
payload manifest, and lifecycle report as a private workflow artifact.

The publish workflow never rebuilds. It accepts a candidate run ID and expected
SHA-256, downloads that artifact, verifies version, source commit, filename, and
digest, then creates or updates `v<version>`. Release channel is explicit:

- `rc`/`beta` versions must publish as prereleases;
- stable versions must not publish as prereleases;
- a stable publish is refused unless the lifecycle report certifies the exact
  installer digest.

No stable release is part of this implementation session. The target is a
Friend Beta prerelease candidate plus the evidence required to decide whether
to publish it.

## Verification Matrix

Automated fixture and contract tests cover:

- all preflight classifications, evidence conflicts, backend-specific WSL rules,
  recovery mappings, live revalidation, state migration/quarantine, and privacy;
- version consistency and deterministic payload selection/hashing;
- shell command construction and the no-visible-console invariant;
- installed product identity, paths, shortcuts, uninstall metadata, upgrade
  preservation flags, and release-channel handling;
- clean install, installed executable self-test, state sentinel creation,
  in-place upgrade with sentinel preservation, uninstall, program-file removal,
  and deliberate user-state preservation;
- exact installer digest equality from candidate build through publish input.

Real-host qualification in this session runs the read-only preflight, builds the
installer, installs it to disposable per-user state, runs shell/diagnostic smoke
tests, and uninstalls it. Full clean-machine Docker/WSL enablement, mandatory
restart/resume, first model download, first Open WebUI signup, chat, and GPU
inference remain external-machine qualification unless the current host can be
used without disturbing existing workloads.

## Non-Goals

- Recreating the private engineering workbench's installer or orchestration
  layer. That workbench is a separate, unpublished repository; this
  distribution wraps the scripts and compose definitions it ships here rather
  than reimplementing that workbench's tooling.
- Shipping Adaptive Media or ValClip code or runtime dependencies.
- Bundling large model weights.
- Automatically editing firmware, BCD, remote Docker contexts, or Windows/Linux
  container mode behind the user's back.
- Publishing a stable release before exact-byte clean lifecycle certification.
