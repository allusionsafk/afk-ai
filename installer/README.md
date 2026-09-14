# AFK LocalAI Windows distribution

The supported user entry point is `AFKLocalAISetup-<version>-x64.exe`. The
installer deploys a self-contained `AFKLocalAI.exe` WinForms shell plus the
bounded provisioning payload. It does not embed model weights.

## Installed architecture

| Component | Role |
|---|---|
| `AFKLocalAISetup-<version>-x64.exe` | Per-user Inno Setup package |
| `AFKLocalAI.exe` | Native setup, recovery, launch, diagnostics, and About shell |
| `Get-Preflight.ps1` | Read-only Windows environment probe using inbox Windows PowerShell 5.1 |
| `Invoke-Recovery.ps1` | Allowlisted, resumable prerequisite recovery actions |
| `Install-LocalAI.ps1` | Phase-based runtime and model provisioning engine |
| `installer-common.ps1` | Atomic state and shared installer primitives |

The shell starts all console-based helpers with `UseShellExecute=false`,
`CreateNoWindow=true`, and a hidden window style. Normal install, first run,
launch, diagnostics, and uninstall do not expose PowerShell or CMD windows.

Program files are installed at `%LOCALAPPDATA%\Programs\AFK LocalAI`. Mutable
state is stored separately at `%LOCALAPPDATA%\AFK LocalAI` so an in-place
upgrade or uninstall cannot accidentally erase it.

## Preflight and recovery

Every setup entry and resume begins with a live probe. The classifier produces
one reason code for Windows support, virtualization, WSL, Docker, GPU/runtime
capability, reboot state, or broken prior state. The UI maps that code to one
bounded action and plain-language recovery copy.

Recovery may install an approved missing prerequisite, open the appropriate
Windows settings surface, request a restart, or retry the probe. It does not
silently edit firmware, BCD/hypervisor policy, Docker context, or container
mode. Checkpoints are hints only and never replace live revalidation.

Structured progress is emitted as `AFK-EVENT:` JSON lines. State writes are
atomic; corrupt state is quarantined rather than overwritten. Logs and
diagnostics remain in the user data directory.

## Deterministic payload

`payload-manifest.txt` is the explicit allowlist of tracked source files. The
release builder rejects traversal, generated output, private state, model
weights, secrets, and untracked entries. It adds the published native EXE and
runtime `version.json`, normalizes timestamps, sorts file records, and writes
`payload-manifest.json` with a SHA-256 for each payload file and the source
commit.

The compiler toolchain is pinned in `toolchain.json`. `Build-Installer.ps1`
refuses a dirty source tree for certifiable builds, verifies the Inno Setup
version, creates the canonical EXE, and writes its `.sha256.txt` sidecar.

## Validation and release

```powershell
pwsh -File scripts\Test-DistributionContracts.ps1
pwsh -File scripts\Build-Installer.ps1
```

The release-candidate workflow then tests:

1. an isolated predecessor install and in-place candidate upgrade
2. version and uninstall registration
3. Start Menu shortcut creation
4. installed `AFKLocalAI.exe --self-test`
5. settings/state preservation
6. silent uninstall and program-file removal
7. the SHA-256 of the exact production installer before it runs

The candidate artifact contains the tested EXE, SHA-256, payload manifest, and
lifecycle reports. The manual publish workflow downloads those bytes, verifies
their provenance and digest, and creates a prerelease or stable release without
running a compiler. A stable release must never be published before those exact
installer bytes pass the production lifecycle.

For the user-visible lifecycle, see
[`docs/install-upgrade-uninstall.md`](../docs/install-upgrade-uninstall.md).
