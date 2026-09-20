# AFK AI Windows distribution

The supported user entry point is `AFKLocalAISetup-<version>-x64.exe`. The
installer deploys a self-contained `AFKLocalAI.exe` WinForms shell, AFK AI's own
pinned Python runtime, and the bounded provisioning payload. It does not embed
model weights.

## Installed architecture

| Component | Role |
|---|---|
| `AFKLocalAISetup-<version>-x64.exe` | Per-user Inno Setup package |
| `AFKLocalAI.exe` | Native setup, recovery, Home status, Open Chat gate, diagnostics, and About shell |
| `runtime\python\` | AFK AI's own pinned CPython (embeddable distribution, no packages) |
| `installer\afk-payload.py` | Verified engine entry point: owned interpreter, isolation, version pin, own package |
| `src\localai\product_cli.py` | Engine commands: status, start, stop, diagnostics, setup steps |
| `Get-Preflight.ps1` | Read-only Windows environment probe using inbox Windows PowerShell 5.1 |
| `Invoke-Recovery.ps1` | Allowlisted, resumable prerequisite recovery actions |
| `Install-LocalAI.ps1` | Phase-based setup orchestrator on Windows PowerShell 5.1; product steps run on the owned engine |
| `installer-common.ps1` | Atomic state and shared installer primitives |

See [product shell and app-owned runtime](../docs/design/product-shell-runtime.md)
for the status contract, the runtime trust chain and ownership categories.

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
weights, secrets, and untracked entries. It adds the published native EXE,
runtime `version.json` and the verified Python runtime, normalizes timestamps,
sorts file records, and writes `payload-manifest.json` with a SHA-256 for each
payload file and the source commit.

The compiler toolchain is pinned in `toolchain.json`. `Build-Installer.ps1`
refuses a dirty source tree for certifiable builds, verifies the Inno Setup
version, creates the canonical EXE, and writes its `.sha256.txt` sidecar.

The Python runtime is pinned in `python-runtime.json`. `Get-PythonRuntime.ps1`
accepts the archive only at the pinned size and SHA-256, with a valid Python
Software Foundation signature on the interpreter binaries and the exact
isolated `._pth` search path, and only after the extracted interpreter proves it
starts isolated. It is staged before the payload manifest is computed, so every
runtime file is hashed. To move to a new CPython release, update every field of
the pin together and re-run the full gate and lifecycle qualification.

## Downloadable launcher

`Install Local AI.cmd` (served by the website as `Install AFK AI.cmd`) runs the
repository's own `bootstrap.ps1` when it sits in a checkout. Downloaded alone,
it fetches `bootstrap.ps1` from an immutable commit and verifies its SHA-256
before Windows PowerShell may run it. Release pin order is: tag the release,
pin `bootstrap.ps1` to that tag's commit and zip digest, pin the launcher to the
commit and digest of that `bootstrap.ps1`, then repin the website to the
launcher bytes.

## Validation and release

```powershell
pwsh -File scripts\Test-DistributionContracts.ps1
pwsh -File scripts\Build-Installer.ps1
```

The release-candidate workflow then tests:

1. an isolated predecessor install and in-place candidate upgrade
2. version and uninstall registration
3. Start Menu shortcut creation
4. installed `AFKLocalAI.exe --self-test`, including installation integrity
5. the installed engine on the installed interpreter proving isolation, the
   pinned version and an intact manifest
6. an upgrade removing files the older version shipped and the candidate does not
7. settings/state preservation
8. silent uninstall and complete program-folder removal
9. the SHA-256 of the exact production installer before it runs

The candidate artifact contains the tested EXE, SHA-256, payload manifest, and
lifecycle reports. The manual publish workflow downloads those bytes, verifies
their provenance and digest, and creates a prerelease or stable release without
running a compiler. A stable release must never be published before those exact
installer bytes pass the production lifecycle.

For the user-visible lifecycle, see
[`docs/install-upgrade-uninstall.md`](../docs/install-upgrade-uninstall.md).
