# AFK AI support

The current public AFK AI Beta is `0.1.7rc1`. The repository also
contains an unpublished `0.2.0-rc1` source candidate; always report the exact
version, tag, branch, or commit you tested.

Support is focused on reproducible Windows installation, runtime, chat, model,
and hardware problems.

[Download AFK AI](https://localai-windows-starter-site.allusionsafk.workers.dev/)

## Supported target

| | |
|---|---|
| OS | Windows 11 |
| Accelerated path | NVIDIA GPU |
| CPU-only path | Smaller models with slower generation |
| Container runtime | Docker Desktop |
| Model runtime | Ollama for Windows |
| Recommended free disk | About 40 GB for a comfortable first install |

The public Beta is pinned separately from development branches and pull
requests.

## Installation or setup

Use the **Installation / setup problem** issue form.

Before reporting a `0.2.0-rc1` source-candidate setup problem, launch AFK LocalAI
again. A partial setup should return to its saved checkpoint and recheck the live
machine. If Windows requested a restart, restart before choosing **Try again**.

The source candidate provides **Start Menu → AFK LocalAI → Diagnostics**. Review
the generated report before attaching it; it should contain only the bounded
environment evidence needed for support.

Include:

- AFK AI version, tag, or commit
- installer phase or last status shown
- exact error text
- whether Windows requested a restart
- whether Docker Desktop opens successfully
- Windows version
- GPU model when relevant
- a sanitised diagnostic report when available

Do not post credentials, `.env` contents, private documents, chats, prompts,
cookies, tokens, or unrelated machine information.

## Hardware or compatibility

Use the **Hardware / compatibility problem** form for issues involving:

- GPU or CPU support
- memory or disk pressure
- Windows version
- hardware virtualization
- WSL
- Docker compatibility

Include what AFK AI detected and what Windows, WSL, Docker, or Ollama reported.

## Product bugs

Use the **General bug** form for a reproducible problem after setup, including:

- chat or Open WebUI
- start and stop behaviour
- service health or structured readiness
- model selection or inference
- Control Center behaviour
- search or local voice
- behaviour that disagrees with the documentation

## Security and privacy

Do not publish sensitive security details in a normal issue. Use the private
reporting path in [SECURITY.md](SECURITY.md).

## Writing a useful report

Keep the report focused on one reproducible problem. Include the evidence needed
to reproduce it and remove unrelated machine details before posting.
