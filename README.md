# AFK LocalAI for Windows

> Public prerelease `0.2.0-rc1` · Windows 11 x64

AFK AI is a local-first AI workspace for Windows 11. It combines Ollama, Open
WebUI, SearXNG, local voice, hardware-aware model selection, and a guided Windows
setup path.

[Download](https://localai-windows-starter-site.allusionsafk.workers.dev/) |
[Support](SUPPORT.md) | [Security](SECURITY.md) |
[Contributing](CONTRIBUTING.md)

## Release authority

The public download is pinned separately from development. The published
[v0.2.0-rc1 prerelease](https://github.com/allusionsafk/afk-ai/releases/tag/v0.2.0-rc1)
contains `AFKLocalAISetup-0.2.0-rc1-x64.exe`, its SHA-256 sidecar, payload
manifest, and lifecycle reports. The installer is 58,367,121 bytes. Branches and
pull requests can contain later work that is absent from those bytes. See the
[candidate notes](docs/releases/0.2.0-rc1.md) and
[install, upgrade, and uninstall contract](docs/install-upgrade-uninstall.md).

## At a glance

| | |
|---|---|
| Public status | Prerelease `0.2.0-rc1` |
| Source development | `master` and feature branches can be ahead of the published EXE |
| Primary target | Windows 11 with an NVIDIA GPU |
| CPU-only path | Smaller models with slower generation |
| Chat | Open WebUI at `http://localhost:3000` |
| Model runtime | Ollama for Windows |
| Optional search | SearXNG at `http://localhost:8080` |
| Licence | MIT |

## Install the public Beta

Download AFK AI from the
[project website](https://localai-windows-starter-site.allusionsafk.workers.dev/).
The website serves pinned Beta bytes after verifying their SHA-256. It does not
use GitHub `releases/latest` as the AFK AI version authority.

Run the downloaded `AFKLocalAISetup-0.2.0-rc1-x64.exe` and follow the prompts.
The old CMD bootstrap remains in the source tree for engineering compatibility;
it is no longer the pinned public download. Code signing remains outstanding.
If Windows blocks the installer, keep security features enabled and report the
exact warning through [Support](SUPPORT.md).

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
developer tools, manual PowerShell, or PATH editing. The source candidate carries
its own pinned, verified CPython runtime and never installs Python or packages
into Python already on the PC. Home reports what AFK AI can prove right now, and
Open Chat checks that chat really answers before opening it.

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

Current Beta target:

- Windows 11
- hardware virtualization for the Docker path
- NVIDIA GPU recommended
- about 40 GB of free disk for a comfortable first install
- Docker Desktop
- Ollama for Windows
- PowerShell 7 only for the engineering tooling and optional firewall hardening

The source tree contains Python-based engineering and control tools. The
installed product uses its own Python runtime, so users do not need to install
Python, select a version, modify PATH, or manage dependencies.

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

The source Prepare path checks the selected GGUF file or complete shard set
using repository byte metadata before downloading. For supported conventional
attention architectures, it derives a conservative f16 KV estimate at the
requested context and parallel count. Missing or ambiguous metadata retains
parameter estimates. These checks can reject a download that exceeds the
memory or disk budget; they do not measure runtime allocation or certify speed.
Scout category ranking still uses estimates, and repository `main` can change
between the metadata check and Ollama's download.

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
| [Beta 0.1.7 notes](docs/releases/0.1.7rc1.md) | Historical CMD candidate |
| [0.2.0 prerelease notes](docs/releases/0.2.0-rc1.md) | Published EXE identity and qualification limits |
| [Installer guide](installer/README.md) | Native distribution architecture |
| [WebBrain guide](docs/webbrain.md) | Browser and search integration |

Historical DemiMedia/Adaptive Media release artefacts also exist in this
repository. They are not AFK AI versions. For public AFK AI downloads, use the
website pin and the named Beta release record.

## Licence

MIT. See [LICENSE](LICENSE).

**ALLUSIONS**  
Independent software by Jidan.
