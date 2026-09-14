# Install, upgrade, and uninstall AFK LocalAI

## Clean install

Run the versioned x64 installer normally. It installs without machine-wide
administrator privileges to:

```text
%LOCALAPPDATA%\Programs\AFK LocalAI
```

The installer creates an Installed apps entry and an AFK LocalAI Start Menu
group. The Desktop shortcut is optional and off by default.

On first launch, the native app runs environment preflight and guides
prerequisite provisioning. If setup is interrupted, reopen AFK LocalAI. It
loads the last checkpoint, probes Windows again, and resumes only work that is
still valid.

## User state

Mutable state is outside the program directory:

```text
%LOCALAPPDATA%\AFK LocalAI
├── State
├── Logs
└── Diagnostics
```

Runtime data created by Docker, Ollama, or Open WebUI remains governed by those
components and the provisioning configuration. AFK LocalAI does not claim that
uninstalling its shell erases third-party runtime or model data.

## Upgrade

Double-click the newer `AFKLocalAISetup-<version>-x64.exe`. The stable Inno
Setup application identity finds the existing per-user installation, keeps its
directory and shortcut choices, and replaces application files in place.

The upgrade does not delete `%LOCALAPPDATA%\AFK LocalAI`. When the new shell
opens, it revalidates the live environment before treating the workspace as
usable. Corrupt provisioning state is quarantined beside the original and a
recoverable setup state is created.

## Uninstall

Use either:

- Windows **Settings → Apps → Installed apps → AFK LocalAI → Uninstall**
- **Start Menu → AFK LocalAI → Uninstall AFK LocalAI**

Uninstall asks the shell to stop the managed local stack without showing a
terminal, then removes program files, shortcuts, and the uninstall entry. It
preserves `%LOCALAPPDATA%\AFK LocalAI` so a reinstall can recover settings and
diagnostics.

If you intentionally want to remove preserved AFK LocalAI state, first review
and back up anything you need, complete the normal uninstall, then delete that
specific data folder yourself. Model/runtime data belonging to Ollama, Docker,
or Open WebUI may be stored elsewhere and is not removed automatically.
