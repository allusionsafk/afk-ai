# AFK AI engineering contract

- This is `allusionsafk/afk-ai`, a Windows local AI product. Python owns AI,
  model intelligence, and orchestration; the native .NET shell owns Windows UX.
  PowerShell is limited to Windows remediation and build/release automation.
- The installed product uses its own verified Python runtime, never ambient
  Python or global package installation. Ollama is the production inference
  runtime. llama.cpp remains experimental until measured product evidence
  justifies a separately reviewed change.
- Present evidence honestly: Unknown is not Ready, Starting is not Ready,
  estimates are not measurements, requested context is not loaded context,
  and cached evidence is not fresh. Keep technical evidence behind details.
- Prove resource ownership using paths, runtime identity, and labels before
  changing processes or Docker resources. Names alone are insufficient.
  Preserve user data, all downloaded models, unrelated runtimes and projects.
  Never use global Docker cleanup or broad process termination.
- Keep APIs narrow, validate external and persisted inputs, use bounded
  timeouts/cancellation, and write mutable state atomically. Never log secrets.
  New dependencies need evidence of necessity, maintenance and compatibility.
- Verify changes with the smallest meaningful boundary regressions, then run
  `python -m ruff check --no-cache src tests`, `python -m mypy src`,
  `python -m pytest -q`, `python -m localai public-audit --strict`,
  `dotnet run --project tests/AFKLocalAI.App.Tests -c Release`, and
  `pwsh -File tests/Invoke-Checks.ps1`. Do not weaken gates to pass.
- A runnable milestone closes only after clean source and normal integration,
  an exact-SHA build staged separately and validated, recoverable promotion to
  one canonical installation, launch through the normal user shortcut, smoke
  and genuine Ready qualification, and an `AFK-AI-BUILD.txt` receipt linking
  source, payload, runtime, validation and deployment. After successful
  promotion remove superseded runnable deployments and stale launchers;
  retain meaningful Git history and user data.
- Local build/deployment is not public release authorization. Publishing
  releases, moving tags or changing public download pins requires explicit scope.
- Keep machine-wide Codex configuration and observer metadata out of product
  commits; preserve unrelated dirty worktrees.
