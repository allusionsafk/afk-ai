# Clean-machine first-run qualification

The first-run path is qualified two ways. Everything reproducible without
breaking a working PC runs in the automated gate; the rest needs a genuinely
clean Windows machine and is listed here so it can be run deliberately rather
than assumed.

**Nothing in the "not yet qualified" section below has been exercised end to
end.** Do not describe the Friend Beta as clean-machine qualified until it has.

## What the automated gate already proves

`tests/Test-FirstRunTransitions.ps1` drives the whole chain — machine evidence →
diagnosis → bounded recovery → checkpoint → resume — as a matrix. Every state the
product claims to distinguish has a row asserting its overall verdict, reason
code, restart requirement, recovery action, elevation requirement, and persisted
checkpoint:

| State | Reason code | Recovery | Elevation | Restart |
| --- | --- | --- | --- | --- |
| ready | `PREFLIGHT-READY` | `retry` | no | no |
| firmware virtualization disabled | `PREFLIGHT-FIRMWARE-VIRT-DISABLED` | guidance only | no | no |
| Windows feature missing | `PREFLIGHT-WINDOWS-FEATURE-MISSING` | `install-wsl` | yes | no |
| restart owed | `PREFLIGHT-WINDOWS-REBOOT-REQUIRED` | `restart-windows` | yes | yes |
| hypervisor configured, not active | `PREFLIGHT-HYPERVISOR-NOT-RUNNING` | `restart-windows` | yes | no |
| WSL not installed | `PREFLIGHT-WSL-NOT-INSTALLED` | `install-wsl` | yes | no |
| WSL outdated | `PREFLIGHT-WSL-UPDATE-REQUIRED` | `update-wsl` | yes | no |
| WSL unhealthy | `PREFLIGHT-WSL-UNHEALTHY` | `update-wsl` | yes | no |
| Docker not installed | `PREFLIGHT-DOCKER-NOT-INSTALLED` | `install-docker` | no | no |
| Docker installed, not running | `PREFLIGHT-DOCKER-NOT-RUNNING` | `start-docker` | no | no |
| Docker still starting | `PREFLIGHT-DOCKER-STARTING` | `retry` | no | no |
| Docker on a remote context | `PREFLIGHT-DOCKER-REMOTE-CONTEXT` | guidance only | no | no |
| Docker on the wrong engine | `PREFLIGHT-DOCKER-LINUX-ENGINE-REQUIRED` | guidance only | no | no |
| evidence insufficient beneath the stack | `PREFLIGHT-UNKNOWN` | guidance only | no | no |
| contradictory virtualization evidence | `PREFLIGHT-UNKNOWN` | guidance only | no | no |
| Docker genuinely unobservable | `PREFLIGHT-UNKNOWN` | guidance only | no | no |
| conclusive blocker beneath an unobservable layer | the *lower* layer's code | that layer's recovery | per layer | per layer |

It also asserts that guidance-only actions carry no machine command, that
anything outside the allowlist is rejected, that the shell's C# recovery table
and the installer's PowerShell table agree code by code, and that checkpoints
resume correctly: completed phases survive a relaunch, repeating one does not
duplicate it, a pending restart round-trips with its reason, and live-machine
phases are never trusted from persisted state.

## Qualified on a real machine

- Environment preflight under Windows PowerShell 5.1 against live WSL and Docker,
  returning `PREFLIGHT-READY` with `docker_endpoint_kind=local-npipe`.
- Argument passing through `Invoke-AiProcess` on Windows PowerShell 5.1.
- Docker running → healthy classification.

## Not yet qualified — needs a clean Windows machine or VM

Run in order. Record the reason code and the on-screen headline at each step.

**Machine preconditions**

1. Fresh Windows 11 (64-bit, build ≥ 22000), fully updated.
2. Virtualization enabled in firmware.
3. No WSL installed (`wsl --version` must fail).
4. No Docker Desktop installed.
5. No `%LOCALAPPDATA%\AFK LocalAI` and no AFK uninstall entry.
6. Take a VM snapshot here — several steps below are one-way.

**First run**

7. Install the candidate `AFKLocalAISetup-*.exe` and launch it.
8. Expect a WSL blocker (`PREFLIGHT-WSL-NOT-INSTALLED`), headline "One step is
   needed", button "Install WSL components". Confirm it asks for Windows
   approval.
9. Run that recovery. Confirm the checkpoint records it and that a restart is
   requested if Windows asks for one.

**Restart boundary**

10. Restart Windows from the offered action.
11. Relaunch AFK LocalAI. Confirm it re-probes rather than trusting the previous
    result, and that it resumes at the first incomplete phase rather than from
    zero.
12. Confirm no completed expensive step (model pull, pip install) is repeated.

**Docker**

13. Expect `PREFLIGHT-DOCKER-NOT-INSTALLED` → "Install Docker Desktop".
14. Run it, then expect `PREFLIGHT-DOCKER-NOT-RUNNING` → "Start Docker Desktop",
    then `PREFLIGHT-DOCKER-STARTING` → "Check again" while it boots.
15. Confirm it converges to `PREFLIGHT-READY` without manual intervention beyond
    the offered buttons.

**Provisioning**

16. Continue setup. Confirm first model download runs once.
17. Complete the Open WebUI first signup.
18. Confirm GPU inference works if the machine has a supported GPU.

**Idempotence**

19. Press "Check again" repeatedly at several stages; confirm no duplicate
    installs, containers, or model pulls, and no contradictory checkpoint.
20. Relaunch the app mid-setup; confirm it resumes rather than restarting.

**Lifecycle**

21. Uninstall. Confirm the program directory, `HKCU` uninstall entry and Start
    Menu folder are removed, and that per-user state under
    `%LOCALAPPDATA%\AFK LocalAI` is preserved.
22. Confirm no shared Docker/Ollama state was touched: other compose projects
    still running, Docker Desktop still running, no models removed.

**States that still need a purpose-built machine**

- firmware virtualization disabled (requires a VM with nested virtualization off,
  or a host BIOS change — do not disable it on a working development machine);
- Windows virtualization feature missing;
- hypervisor configured but not active;
- WSL present but unhealthy;
- Docker pinned to a remote context or the Windows engine.

These are covered by fixtures in the transition matrix. Live coverage would need
a dedicated VM per state.
