# Product shell and app-owned runtime

This record describes how the native AFK AI shell decides what to show, how it
reaches the engine, what Python it runs, and who owns what on the PC. It is a
contract for the `0.2.0` source line, backed by the tests named below. It does
not claim clean-machine qualification; see
[clean-machine-first-run-qualification.md](clean-machine-first-run-qualification.md).

## Defects this replaced

Each was reproduced on a real installation before it was changed.

| Defect | Evidence | Invariant |
|---|---|---|
| Home said "Your local AI is ready" from a persisted `usable` flag | `provisioning-state.json` had `usable: true` while Docker, Ollama and Open WebUI were all down | Ready only from engine evidence |
| Open Chat opened `127.0.0.1:3000` unconditionally | `MainForm.RenderHome` routed with no status check | Open Chat is a gate |
| Setup installed Python machine-wide and ran `py -3.12 -m pip install -e .[windows]` | The PC's Python 3.12 held `__editable__.localai-0.2.0rc1.pth` pointing at the program folder; Python 3.14 resolved `localai` to an engineering checkout | No ambient Python, no global pip state |
| Engine commands ran through `py.exe` | Whatever interpreter the launcher picked, with its site-packages and `PYTHON*` settings | The engine runs only on its own interpreter |
| Unpinned dependencies resolved from PyPI at install time | `fastapi>=0.115`, `uvicorn>=0.30`, ... | Deterministic runtime |
| Configuration written into the program folder | `.env` secret, rewritten `docker-compose.yml`, `logs/`, `src/localai.egg-info` in `%LOCALAPPDATA%\Programs\AFK LocalAI` | Program files are replaceable; user state is not in them |
| Setup launched `pwsh.exe` | Not present on clean Windows 11; the click handler had no guard | Setup runs on inbox Windows PowerShell |
| Shell Start was the engineering workbench start | Tailscale, model warm-up, MCP/git-hook health checks that could fail Start, and it opened the browser itself | Product start never routes to chat |
| Diagnostics were the preflight JSON only | Could not tell a broken backend from a missing model | Diagnostics distinguish failure classes |
| Launcher fetched `bootstrap.ps1` from `master` | `Install Local AI.cmd` | Nothing outside the verified chain executes |
| Windows PowerShell children inherited PowerShell 7's `PSModulePath` | pwsh -> cmd -> powershell.exe could not resolve `Get-FileHash` | Children resolve their own cmdlets |

## Boundary

```text
AFKLocalAI.exe (WinForms)
   | process + JSON, structured argument lists, hidden windows
   v
runtime\python\python.exe -I -B installer\afk-payload.py
      --program-root <program> --data-root <data> <command>
   | verified loader: owned interpreter, isolated, pinned version,
   | this installation's own package
   v
localai.product_cli  (standard library only)
   v
Docker (proven AFK-owned containers) / Ollama / Open WebUI
```

The existing process-and-JSON contract was kept. A persistent loopback service
was not introduced: the shell needs a handful of commands, each observation is a
short-lived process (measured ~285 ms warm for a liveness probe with Docker
down; see below), and a resident HTTP service would add a new local attack
surface and lifecycle to manage without a demonstrated need.

Prerequisite classification and recovery stay in PowerShell
(`Get-Preflight.ps1`, `Invoke-Recovery.ps1`) because they are Windows
remediation. Product work moved into the engine.

### Commands (`installer/afk-payload.py`)

| Command | Output | Used by |
|---|---|---|
| `status --json [--liveness]` | one readiness schema-2 line | Home, Open Chat gate, setup self-test |
| `start` | `AFK-EVENT:` progress, final `AFK-STATUS:` line | Home, setup |
| `stop` | report; exit 0 even when nothing is provable | Home, uninstaller |
| `diagnostics --output <Diagnostics\*.json>` | privacy-bounded bundle | Diagnostics |
| `runtime-info` | interpreter and installation integrity | setup, lifecycle qualification |
| `configure [--model]`, `pull-model`, `seed-webui`, `aliases` | setup steps | `Install-LocalAI.ps1` |

Exit codes: 0 success, 1 not usable or step failed, 2 usage, 3 refused
(layout, interpreter or ownership unproven). A refused `status --json` still
prints a parseable `Failed / RUNTIME_UNVERIFIED` status.

## Readiness contract (schema 2)

`src/localai/readiness.py` is the single readiness authority.

READY requires, in order: Docker reachable; this installation's Open WebUI
container running; its backend answering `/api/config` (not just `/health`);
Ollama answering; the configured model present; the chat backend container
itself reaching Ollama and seeing that model (exec by proven container id); and
a tiny generation producing tokens (thinking-only output counts).

| Field | Meaning |
|---|---|
| `mode` | `qualify` (everything above) or `liveness` (no exec, no generation) |
| `state` | `Ready`, `Live`, `Starting`, `Stopped`, `Degraded`, `Failed`, `NotInstalled`, `Unknown` |
| `reason` | stable reason code |
| `next_action` | one of `open_chat`, `start`, `repair`, `check`, `wait`, `diagnostics` |
| `chat.ready`, `chat.url` | the URL is only present when chat is ready |
| `chat.onboarding_required` | Open WebUI still needs its first account (a separate fact) |
| `qualification.inference` | `passed`, `failed`, `not_run` |

A liveness probe that finds everything healthy reports `Live`, never `Ready`.
A Docker timeout or any observer exception reports `Unknown`. Containers that
claim the product's Compose project name without being this installation's are
counted (never named) and report `FOREIGN_PROJECT_COLLISION`; Start refuses.

## Shell state (`HomeState.cs`)

The shell does not re-derive readiness. `HomeReducer` decides only how long
engine evidence stays valid:

1. `Ready` is shown only from a `qualify` status with inference `passed` and a
   loopback chat URL. Anything else claiming `Ready` is shown as `Unknown`.
2. A `Live` liveness status keeps an existing qualification only for the same
   model, within `QualificationLifetime` (10 minutes), with no regression in
   between. It never creates one.
3. Any regressed, failed, or unobservable status drops the qualification
   immediately.

`ChatGate` routes to chat only from `Ready`, and before opening the browser it
re-observes: a liveness probe while the qualification is fresh, a full
qualification otherwise. The URL must be `http://127.0.0.1:3000/` or
`http://localhost:3000/` with no path, query, fragment or user info.

`HomePresenter` maps states to one primary action from a fixed allowlist; an
unknown `next_action` falls back to diagnostics, never to chat.

### Refresh policy (measured, bounded)

| Situation | Probe |
|---|---|
| Home opens | full qualification; if `Stopped` with `start`, AFK AI starts itself once per launch (never after the person pressed Stop) |
| `Starting` / `Checking` | liveness every 5 s |
| `Ready` | liveness every 30 s |
| other states | liveness every 60 s |
| window re-activated | liveness if the last observation is older than 15 s |
| `Live` without qualification | automatic full qualification, at most once per 5 minutes |
| minimized, hidden, busy | no probing |

A full qualification can load the model; the 5-minute automatic backoff stops a
failing model from being reloaded every cycle. People pressing a button are
never throttled.

## App-owned Python runtime

| Property | Value |
|---|---|
| Distribution | CPython 3.14.7 Windows embeddable package (amd64) from python.org |
| Pin | `installer/python-runtime.json`: URL, size, SHA-256 `D297E5FF…1F15` (matches python.org's release API), PSF Authenticode subject, `._pth` entries |
| Location | `%LOCALAPPDATA%\Programs\AFK LocalAI\runtime\python`, byte-identical to the verified archive |
| Identity | `runtime\afk-runtime.json` beside it |
| Third-party packages | none |
| Size | 23.5 MB installed |

Why the embeddable distribution: its `python314._pth` puts the interpreter in
isolated mode, measured on the real binary: `sys.flags.isolated`, `no_site` and
`ignore_environment` are all set and `sys.path` is only its own archive and
folder; a hostile `PYTHONPATH`, `PYTHONHOME` and `PYTHONSTARTUP` were ignored.
It needs no installer, registry keys or PATH changes, and includes `sqlite3`,
`ssl` and `ctypes`.

Why 3.14: python.org publishes no Windows embeddable package for 3.12 after
3.12.10 (checked against the FTP index), and under PEP 719's schedule 3.13's
binary bugfix releases end around October 2026, so neither can keep receiving
binary security updates as a bundled runtime. The engine suite passes on 3.12 (the CI
minimum) and on 3.14 with `DeprecationWarning` treated as an error.

Why no packages: every module reachable from `product_cli` is standard library
only (enforced by `test_the_product_runtime_needs_no_third_party_packages`).
`typer`, `fastapi`, `uvicorn` and `pywebview` serve the engineering CLI and
Control Center, which the installed product does not run. The runtime is
therefore locked by construction; nothing is resolved from an index.

### Trust chain

1. Build (`scripts/Get-PythonRuntime.ps1`): pinned size and SHA-256; PSF
   Authenticode on `python.exe`, `pythonw.exe`, `python314.dll`; exact `._pth`
   entries; the extracted interpreter must prove isolation at runtime.
2. Payload (`scripts/New-ReleasePayload.ps1`): runtime staged before hashing, so
   every runtime file is in `payload-manifest.json`; links, `site-packages` and
   package directories are refused.
3. Before executing (`InstallationIntegrity.cs`): every file under `runtime`,
   `src/localai` and `installer` must match the installed manifest, and nothing
   extra may be present there. A failure shows "AFK AI needs repair".
4. At start (`afk-payload.py`): owned interpreter directory, isolated flags,
   pinned version, and the imported `localai` resolved from this program root.

The manifest sits beside the files it describes in a per-user folder. This
detects corruption, partial updates and mismatched installs. It is not a
defence against malware already running as the user.

### Measured costs

| Operation | Warm | Cold |
|---|---|---|
| Shell self-test including full integrity hashing | ~165 ms | ~2.2 s |
| Engine liveness status (Docker down) | ~285 ms | ~440 ms |
| Engine `runtime-info` (Python-side full hash) | ~350 ms | |

The shell verifies integrity once per launch on a background task.

## Ownership categories

| Category | Examples | Repair | Update | Uninstall |
|---|---|---|---|---|
| Product runtime | `AFKLocalAI.exe`, `runtime\`, `src\`, `installer\`, compose template, Modelfiles | re-verified; reinstall replaces | replaced wholesale (`[InstallDelete]` for `runtime`, `src`, `installer`), only in a folder AFK owns (new/empty, or this AppId's registered folder holding its files); any other non-empty folder is refused before changes | removed |
| AFK-owned state | `%LOCALAPPDATA%\AFK LocalAI\State`, `Config\runtime.env` (chosen model, generated service secret), `Logs`, `Diagnostics` | kept | kept | kept |
| User data | AFK's Docker volumes (`afk-localai_open-webui`: accounts, chats, documents; `afk-localai_searxng-data`) | kept | kept | kept |
| External shared dependency | Windows features, WSL, Docker Desktop, Ollama and its model store, the user's `OLLAMA_*` variables | not changed by repair | not changed | not removed or stopped globally |
| Unrelated | other Compose projects, containers, volumes, images, models, Python installations, engineering checkouts | never touched | never touched | never touched |

Repair (`Install-LocalAI.ps1 -Repair`) re-runs every product phase — runtime
verification, model configuration, model presence, service start and
qualification, Open WebUI defaults — while keeping the hardware tier, intent
and model choice. It only brings containers up; it never removes containers or
volumes.

Legacy state from earlier setups is handled explicitly:

- the program-folder `.env` secret is migrated into `Config\runtime.env`, and
  the old file is removed only after the migrated value reads back;
- `logs\` and `src\localai.egg-info` in the program folder are removed on
  update and uninstall;
- a global `pip install -e` registration of `localai` is **detected and
  reported** by diagnostics (pointing at this installation, the legacy
  `%USERPROFILE%\localai` folder, or elsewhere). It is not removed
  automatically: it lives in the user's own Python.

## Diagnostics

`src/localai/diagnostics.py` builds the bundle from allowlisted structured facts.
It never runs inference. It contains product version, installation integrity,
runtime identity, setup phases and preflight checkpoint, hardware summary,
configured model, presence (not value) of the service secret, Docker
reachability and context kind, AFK-owned containers with image references,
a count of foreign same-project containers, Ollama version and configured-model
presence, a liveness status, legacy registration findings, recent lifecycle
event codes, and a support `classification`: `prerequisite_failure`,
`runtime_failure`, `backend_failure`, `model_failure`, `chat_onboarding`,
`foreign_resource_collision`, `corrupt_installation`, `healthy`, or `unknown`.

It excludes chat content, prompts, generated text, documents, file contents,
credentials, tokens, browser data, and names of other projects, containers or
models. Install locations are described (`standard`/`custom`, spaces,
non-ASCII, length), not copied. A final pass redacts profile paths, the
generated secret and token-shaped strings. The privacy boundary is tested with
canaries planted in every input, and the test was mutation-checked.

When the engine cannot run, the shell writes a smaller fallback report
(version, which program files failed verification, Home state).

## Trust boundaries reviewed

- Shell -> engine: structured `ArgumentList`, no shell interpolation; hidden
  windows; `-I -B`.
- Engine -> Docker: containers addressed by proven id for `stop` and `exec`;
  `compose up` pinned to this installation's file and env file, after a
  foreign-collision check.
- Readiness probes: loopback URLs only; generated text is never copied.
- Chat routing: loopback URL allowlist in the shell.
- Model names: validated before reaching the env file, Ollama, or `docker exec`
  arguments.
- Diagnostics output path: absolute, `.json`, inside the diagnostics folder,
  validated before anything is observed.
- Launcher: bootstrap fetched from an immutable commit and verified before
  execution; release order is tag, pin `bootstrap.ps1`, pin the launcher.

Setup sets `OLLAMA_HOST=0.0.0.0:11434` so the chat container can reach Ollama
through `host.docker.internal`. That makes the firewall rule part of the
security invariant: the secure phase runs `ai-firewall.ps1 -Apply` on the same
inbox Windows PowerShell 5.1 as setup (administrator approval required), then
reads `LocalAI-Block-Physical-Ports` back and requires it to be enabled,
inbound, Block, TCP, covering 3000/8888/11434/8080/8880/8188, for any remote
address and program, scoped to every physical adapter and to no virtual one.
If that cannot be verified, setup fails with the exposure stated; it never
reports the install as secured on the tool's exit code alone. Whether
`host.docker.internal` reaches a loopback-bound Ollama still needs live
verification before the bind can be narrowed.

## Tests

| Invariant | Evidence |
|---|---|
| Ready only from qualified evidence; stale Ready dropped; liveness keeps but never creates | `tests/AFKLocalAI.App.Tests` status-contract block; `tests/test_readiness_behavior.py` |
| Open Chat gate and URL allowlist | native tests `ChatGate` block |
| Owned interpreter, isolation, version pin, hostile `PYTHONPATH` | `tests/test_afk_payload_entry_behavior.py` |
| No ambient Python or pip in setup; owned engine by path | `tests/Test-InstallerContracts.ps1`, `tests/Test-InstallerPreflight.ps1` |
| Runtime pin and tampered-archive refusal | `tests/Test-InstallerContracts.ps1` owned runtime block |
| Configuration outside the program folder; legacy secret migration | `tests/test_product_engine_behavior.py` |
| Corrupt, missing and stale files detected | Python and native integrity tests; live tamper probe |
| Start never opens a browser; refuses foreign collisions | `tests/test_product_engine_behavior.py` |
| Diagnostics privacy | `tests/test_diagnostics_privacy_behavior.py` |
| Upgrade removes stale runtime files; an existing non-AFK folder is refused and left intact; uninstall leaves nothing in the program folder; state preserved | `scripts/Test-LifecycleUpgrade.ps1` (certified locally on real installer bytes); `tests/Test-InstallerContracts.ps1` |
| Ollama exposure only with a verified block rule; no unverified loopback claims | `tests/Test-InstallerFirewall.ps1` (runs on Windows PowerShell 5.1) |
| Launcher verifies before executing | `tests/test_launcher_integrity_behavior.py` (runs the launcher's own verification against tampered bytes) |
