# AFK LocalAI for Windows

> Public Friend Beta `0.1.7rc1` · current source candidate `0.2.0-rc1`

AFK AI is a local-first AI workspace for Windows 11. It combines Ollama, Open
WebUI, SearXNG, local voice, hardware-aware model selection, and a guided Windows
setup path.

[Download](https://localai-windows-starter-site.allusionsafk.workers.dev/) |
[Support](SUPPORT.md) | [Security](SECURITY.md) |
[Contributing](CONTRIBUTING.md)

## Release authority

The public Friend Beta is pinned separately from development. The project
website remains the download authority for `0.1.7rc1`; branches, pull requests,
and the `0.2.0-rc1` source candidate can contain work that is not included in
the current download.

The `0.2.0-rc1` source candidate defines a conventional Windows installer named
`AFKLocalAISetup-0.2.0-rc1-x64.exe`. It is not a published candidate until exact
installer bytes pass lifecycle qualification and are attached to the matching
prerelease with their SHA-256 file. See the
[candidate notes](docs/releases/0.2.0-rc1.md) and
[install, upgrade, and uninstall contract](docs/install-upgrade-uninstall.md).

## At a glance

| | |
|---|---|
| Public status | Friend Beta `0.1.7rc1` |
| Source candidate | `0.2.0-rc1` (not yet the public download) |
| Primary target | Windows 11 with an NVIDIA GPU |
| CPU-only path | Smaller models with slower generation |
| Chat | Open WebUI at `http://localhost:3000` |
| Model runtime | Ollama for Windows |
| Optional search | SearXNG at `http://localhost:8080` |
| Licence | MIT |

## Install the public Friend Beta

Download AFK AI from the
[project website](https://localai-windows-starter-site.allusionsafk.workers.dev/).
The website serves pinned Friend Beta bytes after verifying their SHA-256. It
does not use GitHub `releases/latest` as the AFK AI version authority.

The source file remains named `Install Local AI.cmd` for compatibility. The
website serves the same pinned bytes as `Install AFK AI.cmd`. Run the downloaded
installer and follow the prompts. Windows may warn about the unsigned Friend
Beta script; the file can be inspected before it is run.

If Smart App Control blocks the script, do not disable Smart App Control for the
beta. Use the inspectable source/bootstrap route instead.

## Guided `0.2.0-rc1` first run

The source candidate checks the machine before downloading models or changing
the local runtime. It classifies Windows, firmware virtualization, Windows
virtualization, WSL, Docker Desktop, GPU, memory, disk, and conflicting or
damaged prior AFK state.

When a prerequisite is missing, the native shell presents one bounded recovery
action or a precise manual instruction. Potentially disruptive firmware, boot,
container-mode, and Docker-context changes are never made silently. Setup saves
checkpoints under `%LOCALAPPDATA%\AFK LocalAI\State`; after a restart or partial
failure, **Resume setup** rechecks live state before continuing.

The candidate installs per user under `%LOCALAPPDATA%\Programs\AFK LocalAI` and
keeps mutable state under `%LOCALAPPDATA%\AFK LocalAI`, outside the replaceable
program directory. The supported user path does not require a repository clone,
developer tools, manual PowerShell, or PATH editing. App-owned Python remains a
required Product Utility milestone and is not yet complete.

## Local services

| Service | Endpoint | Purpose |
|---|---|---|
| Open WebUI | `127.0.0.1:3000` | Chat interface |
| SearXNG | `127.0.0.1:8080` | Optional web search |
| Control Center | `127.0.0.1:8765` | Health and diagnostics |
| Kokoro TTS | `127.0.0.1:8880` | Local voice |
| Ollama | host port `11434` | Native Windows model runtime |

Ollama runs on the Windows host so it can use the GPU directly. Docker-hosted
services reach it through the configured host boundary. The `0.2.0-rc1` source
candidate gives its Compose resources the isolated `afk-localai` identity and
pins Open WebUI by digest.

After installation, the first Open WebUI account created on that installation
becomes its local owner/admin account. It is stored in Open WebUI's local
database, not in an AFK AI cloud account.

## Privacy

AFK AI is local-first, not offline-only.

Local by design:

- model inference through local Ollama
- Open WebUI account and chat storage
- local UI, voice, search front end, and Control Center
- diagnostics intended to exclude chats, prompts, documents, credentials, and
  file contents

Internet access can still be used for setup and software downloads, model
downloads, updates, web search you enable, and optional online integrations you
choose. Remote access is separate and opt-in. The included Tailscale helper is
not enabled automatically.

See [SECURITY.md](SECURITY.md) for reporting and security details.

## Requirements

Current Friend Beta target:

- Windows 11
- hardware virtualization for the Docker path
- NVIDIA GPU recommended
- about 40 GB of free disk for a comfortable first install
- Docker Desktop
- Ollama for Windows
- PowerShell 7 for the full tooling path

The source tree contains Python-based engineering and control tools. The
finished product must use an AFK-owned Python runtime so users do not need to
install Python, select a version, modify PATH, or manage dependencies.

## Model fitting

Model Scout evaluates the detected machine before recommending a model and
context combination.

| Tier | VRAM | Typical target |
|---|---:|---|
| S | 16 GB+ | Larger local models |
| A | 12 GB | High-quality mid-size models |
| B | 8 GB | Balanced local models |
| C | 4 GB | Compact models |
| CPU | none | Small models with slow generation |

A successful load does not by itself mean a model is a good fit. Architecture,
quantization, context length, KV cache, runtime overhead, RAM, VRAM, and offload
all matter.

## Development

For a source checkout:

```powershell
pip install -e .
copy .env.example .env
localai start
localai status
```

Set a strong `SEARXNG_SECRET` in `.env` before using the search stack.

The internal package and command retain `localai` for continuity. AFK LocalAI
is not affiliated with or endorsed by mudler/LocalAI or localai.io.

Common commands:

| Command | Purpose |
|---|---|
| `localai vet [--json]` | Inspect hardware and capability tier |
| `localai start` | Start the local stack |
| `localai stop` | Stop the local stack |
| `localai status [--json]` | Report structured product readiness |
| `localai health` | Check services and runtime health |
| `localai dashboard` | Open the Control Center |
| `localai model-scout` | Recommend models for the machine |
| `localai warm` | Warm models |
| `localai perf` | Show performance information |
| `localai firewall` | Apply local network guardrails |
| `localai update` | Update supported runtime assets |
| `localai public-audit --strict` | Scan the public tree for machine-specific leaks |

Run `localai --help` for the complete command list.

## Upgrade and uninstall

The `0.2.0-rc1` installer uses a stable per-user application identity so newer
qualified builds can replace program files while preserving mutable state.
Uninstall stops only containers whose ownership is proven from the installed
Compose-file path. It does not stop Docker Desktop or Ollama globally, remove
models, prune Docker, delete volumes, or remove preserved user state.

## Documentation

| Document | Purpose |
|---|---|
| [Documentation index](docs/README.md) | Public docs and engineering records |
| [Support](SUPPORT.md) | Support scope and issue routing |
| [Security](SECURITY.md) | Private vulnerability reporting |
| [Contributing](CONTRIBUTING.md) | Contribution and test expectations |
| [Friend Beta 0.1.7 notes](docs/releases/0.1.7rc1.md) | Current public candidate |
| [0.2.0 source candidate notes](docs/releases/0.2.0-rc1.md) | Unpublished source-candidate scope |
| [Installer guide](installer/README.md) | Native distribution architecture |
| [WebBrain guide](docs/webbrain.md) | Browser and search integration |

Historical Adaptive Media release artefacts also exist in this repository.
They are not AFK AI versions. For public AFK AI downloads, use the website pin
and the named Friend Beta release record.

## Licence

MIT. See [LICENSE](LICENSE).

**ALLUSIONS**  
Independent software by Jidan.
