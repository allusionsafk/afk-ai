# Hardware-Aware Utility V1 Design

AFK should show this PC, recommend a model with inspectable reasons, distinguish downloaded and configured models, and expose optimizer evidence without requiring users to understand Ollama or memory math. The initial recommendation uses locally installed Ollama models, while any remote catalogue enrichment stays bounded and optional. Downloads and benchmarks require explicit actions.

## Boundaries

- Python owns hardware, model inventory, fit, recommendation, and optimizer policy. The native shell receives versioned JSON through the verified app-owned Python bridge.
- `/api/tags` supplies installed names, digests, and exact artifact bytes; `/api/show` is bounded enrichment for shortlisted models. `/api/ps` supplies loaded state and observed residency only. Neither a requested context nor a fit estimate becomes a measurement.
- A fit verdict first checks full resident weights and context KV against RAM and VRAM. MoE active parameters influence performance preference only after that check. Unknown or malformed architecture metadata lowers confidence.
- A model recommendation is a staged decision: eligibility, memory fit, context fit, available measurements, then capability and product policy. Each reason is returned as evidence.
- Optimizer V1 remains runtime-neutral. Its cache key and measured context contract remain authoritative. The product invokes cached-only analysis during browsing and runs a fresh benchmark only on an explicit, cancellable action.

## Product flow

The first-run and Models & Fit view begins with a fast local inventory and hardware summary. It marks the best eligible installed model, shows alternatives, and opens details for size, quantization, fit context, evidence source, and limitations. When the fresh inventory is empty, it presents the existing installer's hardware-tier model choice as a setup plan, clearly labelled as uninstalled and unmeasured; the explicit Setup action confirms hardware before downloading. Selecting an installed model is explicit. A future/optional remote recommendation must present an explicit install action and an exact bounded download size when known.

Optimization shows the configured model, current user setting, AFK recommended context when supported by valid measurements, measured rates and effective context, cache provenance, and a separate Apply action. A user override is stored separately from AFK's recommendation. Failed or cancelled measurement leaves settings untouched.

## Safety and performance

All runtime and remote input is length, shape, and numeric-bound validated. Subprocesses use argument arrays; localhost responses are untrusted. API reads have timeouts and response-size caps. Mutable state is atomic and keyed by hardware, model digest, runtime identity, context, and policy version. No automatic multi-gigabyte pull or launch-time benchmark occurs. The UI remains responsive and never promotes stale evidence to fresh.

## Verification

Regression tests cover MoE/dense residency, exact size, sharded size, malformed and huge metadata, KV derivation/fallback, changed model digest/runtime, measurement versus estimate, override versus recommendation, cancellation, and partial runtime failure. Run focused checks, independent boundary review, then the canonical full repository gate and exact-SHA local build/deployment qualification.

## Component review

Utility V1 reviews each component for a faster or simpler replacement at its actual scale:

| Component | Candidate considered | V1 decision and evidence |
| --- | --- | --- |
| Ollama inventory adapter | A new HTTP client dependency or a separate service | Keep bounded standard-library loopback calls. The quick path makes three calls, returns a capped 128-model report, and observed 22 local models in about one second on the qualification PC. Optional `/api/show` enrichment is capped at eight finalists. |
| Native model list | Virtualized or web-rendered list | Keep WinForms `ListView` inside the existing shell. The contract caps inventory at 128 models, and native keyboard and screen-reader controls require no new runtime. Revisit virtualization if measured list rendering becomes slow at that cap. |
| Native JSON bridge | New IPC framework | Keep the verified owned-interpreter process and `System.Text.Json`. The native parser has a size/depth/schema cap and subprocesses use argument arrays and cancellation. |
| Optimizer evidence | New cache/database | Keep the bounded atomic cache, with a deferred commit for explicit fresh measurements. A partial or cancelled ladder cannot publish a fresh recommendation or partial cache. |
| Inference runtime | Reopen llama.cpp lineage | Keep Ollama for production. A replacement needs a new measured product win and separate ownership/integrity review. |

These are current decisions, not permanent bans on replacement. Compare observable startup, response time, memory, correctness, dependency and maintenance cost before changing them.
