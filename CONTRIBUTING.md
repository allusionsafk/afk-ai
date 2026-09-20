# Contributing to AFK AI

The Windows AFK AI Beta is still being qualified. Contributions should improve the supported path without broadening claims beyond what has been tested.

## Keep changes focused

Prefer one demonstrated problem and one coherent fix.

Avoid combining a focused change with unrelated refactors, repository-wide renames, dependency churn, architecture experiments, or visual cleanup.

**AFK AI** is the product name. Existing `localai` package, command, and repository names may remain where renaming would add migration risk without improving the product.

## Before changing behaviour

1. Reproduce the problem or establish the current baseline.
2. Add or identify a regression test when practical.
3. Make the smallest maintainable change that addresses the cause.
4. Run focused checks first.
5. Run the wider inexpensive checks for the area you changed.
6. Update documentation when behaviour changes.

Only describe a platform, recovery path, privacy property, installer path, or performance result as tested when it was actually observed.

## Security and privacy

Do not:

- expose local services to the LAN or internet by default
- add telemetry or upload diagnostics without explicit product review
- include chats, prompts, documents, credentials, tokens, cookies, `.env` values, or unrelated machine data in diagnostics
- recommend disabling Defender, Smart App Control, antivirus, UAC, or other Windows security controls as a blanket workaround
- permanently weaken PowerShell execution policy
- bypass pinned download or integrity checks for convenience

Report vulnerabilities through the private path in [SECURITY.md](SECURITY.md).

## Checks

Use the checks relevant to the files you changed.

The Python project uses:

```text
pytest
ruff
mypy
```

Installer and public-surface work also uses repository-specific checks under `tests/` and `ai-public-audit.ps1`.

Documentation-only and helper-only changes do not need model downloads, Docker setup, GPU workloads, or unrelated end-to-end testing.

## Pull requests

A pull request should state:

- the problem
- the change
- what remains unchanged
- checks run and their results
- known limitations or follow-up work

Call out any privacy, security, installation, networking, runtime ownership, or release impact.

Do not move release pins unless the pull request is a reviewed release operation.
