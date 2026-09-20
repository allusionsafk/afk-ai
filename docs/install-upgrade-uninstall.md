# Install, upgrade, and uninstall AFK AI

## Clean install

Run the versioned x64 installer normally. It installs without machine-wide
administrator privileges to:

```text
%LOCALAPPDATA%\Programs\AFK LocalAI
```

The installer creates an Installed apps entry and an AFK LocalAI Start Menu
group. The Desktop shortcut is optional and off by default.

The installation carries its own pinned Python runtime under `runtime\python`.
You do not need Python installed, and AFK AI never installs Python, changes
PATH, or installs packages into a Python you already have.

On first launch, the native app runs environment preflight and guides
prerequisite provisioning. Setup runs on the Windows PowerShell included with
Windows 11. If setup is interrupted, reopen AFK AI. It loads the last
checkpoint, probes Windows again, and resumes only work that is still valid.

## What belongs to whom

| Category | Where | Repair | Upgrade | Uninstall |
|---|---|---|---|---|
| AFK AI program and runtime | `%LOCALAPPDATA%\Programs\AFK LocalAI` | verified, reinstall replaces | replaced | removed |
| AFK AI settings and records | `%LOCALAPPDATA%\AFK LocalAI` (`State`, `Config`, `Logs`, `Diagnostics`) | kept | kept | kept |
| Your chats, accounts and documents | AFK AI's Docker volumes (`afk-localai_open-webui`, `afk-localai_searxng-data`) | kept | kept | kept |
| Shared tools | Docker Desktop, WSL, Ollama and its models | not changed | not changed | not removed or stopped |
| Everything else | other Docker projects, volumes, images, models, Python installations | never touched | never touched | never touched |

`Config\runtime.env` holds the chat model this PC was set up with and a
generated local service secret. Diagnostics report whether the secret exists,
never its value.

## Home and chat

Home shows what the engine can prove right now, not what setup once reported.
"Your local AI is ready" appears only after AFK AI's own check has asked the
chat backend, confirmed it can reach the model, and seen the model answer a
short test prompt. Open Chat checks again before opening the browser. If
anything stops working, Home changes to what happened and the next useful step.

When you open AFK AI and its services are stopped, it starts them once. It does
not do so again in that session after you press Stop.

## Repair

**Setup & repair → Repair AFK AI** (or **Repair AFK AI** on Home) re-runs every
product setup step: it re-checks the PC, verifies AFK AI's runtime, confirms the
chat model is present (downloading it if missing), recreates AFK AI's own
services, and re-applies chat defaults. It keeps your hardware tier and model
choice, and it never removes chats, accounts or Docker volumes.

If AFK AI's program files are damaged or incomplete, Home says so and asks you
to run the installer again; your settings and chats are kept.

## Upgrade

Double-click the newer `AFKLocalAISetup-<version>-x64.exe`. The stable Inno
Setup application identity finds the existing per-user installation, keeps its
directory and shortcut choices, and replaces application files in place. The
`runtime`, `src` and `installer` folders are replaced wholesale, so a file an
older version shipped cannot linger beside the new one.

That cleanup only ever happens in a folder AFK LocalAI owns: an empty or new
folder, or the folder this AFK LocalAI installation is registered in (and which
still holds its files). If you choose an existing folder that already contains
other files, Setup refuses it before changing anything and asks for an empty or
new folder instead. The folder's name is never taken as proof.

The upgrade does not delete `%LOCALAPPDATA%\AFK LocalAI`. When the new shell
opens, it verifies its own files and asks the engine for live status before
showing anything as ready. Corrupt provisioning state is quarantined beside the
original and a recoverable setup state is created. An older setup's
program-folder `.env` secret is moved into `Config\runtime.env` on first use.

## Uninstall

Use either:

- Windows **Settings → Apps → Installed apps → AFK LocalAI → Uninstall**
- **Start Menu → AFK LocalAI → Uninstall AFK LocalAI**

Uninstall asks AFK AI's own engine to stop only the containers whose ownership
it can prove, without showing a terminal, then removes program files, the
bundled runtime, shortcuts, and the uninstall entry. If the runtime is already
missing, nothing is stopped and the uninstall still completes. It does not stop
Docker Desktop or Ollama, remove models, prune Docker, or delete volumes. It
preserves `%LOCALAPPDATA%\AFK LocalAI` so a reinstall can recover settings and
diagnostics.

If you intentionally want to remove preserved AFK LocalAI state, first review
and back up anything you need, complete the normal uninstall, then delete that
specific data folder yourself. Your chats live in the Docker volume
`afk-localai_open-webui`; removing it deletes them permanently. Model data
belonging to Ollama is stored with Ollama and is not removed automatically.

Earlier setups registered `localai` into your own Python with
`pip install -e`. AFK AI no longer does this. Diagnostics report such a
registration if one exists; AFK AI does not remove it from your Python for you.
