# AFK product baseline and utility implementation plan

**Goal:** Ship the accepted warm workbench on current master, reconcile owned
runtime resources, then expose real model-fit and optimizer evidence in the
canonical Windows product.

**Authority:** The user-provided AFK AI full product development resumption brief
(2026-09-27); PRODUCT.md and docs/design/product-shell-runtime.md.
**Architecture:** Retain Python engine, app-owned runtime, process/JSON bridge,
native .NET shell, and Ollama. Keep estimates, measurements, recommendations and
user overrides distinct. No experimental runtime import or new dependency.

## Baseline checkpoint
- [x] Verify repository identity, fetch origin, preserve existing checkouts.
- [x] Isolate from current master and cherry-pick only accepted UI commits.
- [ ] Establish current installed source, launchers, runtime and Docker ownership.
- [ ] Add concise durable AGENTS.md. Run shell regressions and full repository gate.
- [ ] Review changes and bounded truthfulness probes; open PR, pass CI, integrate.
- [ ] Build exact integrated SHA, stage and validate payload/runtime integrity.
- [ ] Retire only proven stale AFK containers; preserve all volumes and models.
- [ ] Promote recoverably, launch normal shortcut, qualify Ready, write receipt.
- [ ] Remove superseded runnable deployments and stale user-facing launchers.

## Hardware-aware utility checkpoint
- [ ] Reassess historical Scout algorithms against current engine and optimizer.
- [ ] Add bounded first-run recommendation using real hardware/model evidence.
- [ ] Add Models & Fit and Optimization via existing verified process/JSON bridge.
- [ ] Preserve recommendation independently from an explicit user override.
- [ ] Cover metadata failure, MoE residency, cache identity and malformed inputs.
- [ ] Review, gate, integrate, deploy, launch, qualify and prune as above.

## Review focus
- A liveness or in-progress observation must never manufacture Ready.
- An ambiguous Compose owner must block destructive remediation.
- A missing exact weight/KV input must reduce confidence rather than invent data.
- Cached evidence must bind hardware, runtime, model and context identities.
- The normal shortcut must resolve to the receipt-verified deployed executable.

## Execution decisions
The user explicitly requests sustained implementation with one owner of shared
lifecycle/deployment state. Execute inline with read-only parallel reviews.
Accepted visual design is fixed. No public release, release-tag or website change.
