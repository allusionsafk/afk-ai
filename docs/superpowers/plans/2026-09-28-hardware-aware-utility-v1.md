# Hardware-Aware Utility V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show truthful hardware-aware model fit and recommendations, and expose measured optimization in the installed AFK Windows product.

**Architecture:** Python owns bounded Ollama inventory, fit policy, recommendation and Optimizer V1 evidence. A versioned JSON command crosses the verified app-owned Python bridge into native WinForms screens. Downloads and benchmarks start only from explicit user actions.

**Tech Stack:** Python 3.12+, standard library, pytest, .NET WinForms, PowerShell only for the existing setup path.

**Spec:** `docs/superpowers/specs/2026-09-28-hardware-aware-utility-v1-design.md`

## Global Constraints

- Preserve the installed product's isolated Python interpreter, Ollama runtime, warm paper/ink UI, and `AGENTS.md` canonical gate.
- Full weights and KV count for both dense and MoE; active expert parameters may influence speed only.
- Keep estimated, measured, cached, recommended and user-selected contexts as different fields.
- No automatic download or benchmark during app open; no new third-party dependency.
- Bound every runtime response and operation; validate model names and all numeric metadata.

## Review Focus

1. Alias/tag changes with an unchanged digest must not reuse incompatible optimizer evidence.
2. Missing, fractional, negative, huge or hybrid architecture metadata must not be labelled exact KV.
3. A model disappearing between inventory and selection must fail without changing configuration.
4. Cancellation or a partial benchmark must not create an AFK recommendation or apply a setting.
5. A localhost service with malformed or oversized JSON must not hang or crash the native UI.

---

### Task 1: Fit truth and optimizer identity

**Files:** `src/localai/model_scout.py`, `src/localai/optimizer.py`, `src/localai/optimizer_ollama.py`, `tests/test_model_scout_behavior.py`, `tests/test_optimizer.py`

**Interfaces:** Preserve public Scout and Optimizer V1 signatures. `measurement_key(HardwareProfile, ModelProfile, RuntimeProfile, int) -> str | None` must include every fit/measurement-affecting model field.

- [ ] Write failing regressions for MoE VRAM spill, exact size priority, and identity changes with stable digest.
- [ ] Run focused tests and confirm the new cases fail for the intended reason.
- [ ] Repair Scout fit ordering and strengthen optimizer key/metadata validation without changing evidence semantics.
- [ ] Run focused Python tests, Ruff and mypy; commit.

### Task 2: Pure Utility fit and recommendation

**Files:** create `src/localai/product_utility.py`, `tests/test_product_utility.py`; update the payload manifest.

**Interfaces:** `build_report(hardware, installed_models, configured_model, loaded_models, *, context) -> dict[str, object]`. Inputs are validated model facts; output schema 1 distinguishes exact artifact bytes, estimated KV, fit, installed/configured/loaded and recommendation reasons.

- [ ] Write regressions for dense, MoE, context-sensitive fit, unknown architecture, malformed/huge metadata, duplicate digests and deterministic selection.
- [ ] Run focused tests to prove failure.
- [ ] Implement explicit staged fit and selection, with no arbitrary decimal score and bounded candidate count.
- [ ] Run focused tests, Ruff and mypy; commit.

### Task 3: Bounded Ollama adapter and product command

**Files:** create `src/localai/product_utility_ollama.py`; update `src/localai/product_cli.py`, `installer/afk-payload.py`, manifest; create `tests/test_product_utility_ollama.py`.

**Interfaces:** `utility-report --json` emits one schema-1 JSON document. `utility-optimize --model TAG [--measure]` uses the product-owned cache and emits distinct recommendation/measurement/override fields. Neither command downloads or changes configuration.

- [ ] Write fake HTTP/runtime boundary tests for response caps, timeouts, invalid model names, missing model, partial API failure, and no benchmark by default.
- [ ] Run tests to prove failure.
- [ ] Implement short bounded inventory, finalist enrichment, and verified product command routing.
- [ ] Run focused tests, Ruff, mypy and payload/manifest contracts; commit.

### Task 4: Explicit use, install and context actions

**Files:** update `product_cli.py`, `product_config.py`, `product_runtime.py`, native controller; add tests.

**Interfaces:** `utility-use --model TAG` only accepts a currently installed model; `utility-apply-context --model TAG --context N` only accepts a safe measured/recommended or explicit user override context and records recommendation separately from the chosen setting. Setup's existing pull remains the explicit first-run download.

- [ ] Write failure tests for vanished model, invalid tag/context, failed creation and unchanged configuration on failure.
- [ ] Run focused tests to prove failure.
- [ ] Implement atomic selection and apply flow using existing argument-array and owned-container paths.
- [ ] Run focused tests and type/lint gates; commit.

### Task 5: Native Models & Fit and Optimization

**Files:** update `src/AFKLocalAI.App/MainForm.cs`, `ProvisioningController.cs`; create focused native presenter/contract files and tests in `tests/AFKLocalAI.App.Tests/Program.cs`.

**Interfaces:** async versioned JSON parse from the owned bridge. Show This PC, best installed choice, alternatives, fit details, and separate optimizer recommendation, measurements, cache source and user setting. Explicit actions are cancellable.

- [ ] Write native JSON and presenter regressions for unknown/malformed fields, estimate versus measurement, and override display.
- [ ] Run native tests to prove failure.
- [ ] Add screens and actions, keyboard/screen-reader names, loading/error states and responsive layout.
- [ ] Run native tests and manual 100/150/200% DPI and keyboard review; commit.

### Task 6: Final review, integration and local deployment

**Files:** release receipt and build outputs outside source commits.

- [ ] Run focused adversarial probes on changed trust boundaries and independent review; repair findings.
- [ ] Run all canonical `AGENTS.md` gates on the exact branch SHA.
- [ ] Recheck worktree identity, commit, integrate through reviewed PR when network available, and verify exact clean master SHA.
- [ ] Build the exact source, stage and validate payload/runtime, preserve rollback, promote canonical installation.
- [ ] Launch the normal shortcut, verify genuine Ready and inference, update build receipt and prune only superseded runnable deployment.
- [ ] Report source, quality, model/fit, optimization, security, performance, deployment and limitations with exact evidence.
