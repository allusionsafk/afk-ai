# llama.cpp Runtime Competition V1

## Decision

**C. DO NOT INTEGRATE YET.**

On this machine and model, llama.cpp did not provide enough latency,
throughput, memory, or operational benefit to justify a second production
runtime. The experimental adapter and evidence remain on the competition
branch. This is a decision for the measured product state, not a permanent
architectural verdict.

## Lineage and scope

- Optimizer V1 entered `master` at
  `a7252fdf413a147c3941c8bd3e5bd0d998bedd76`.
- This spike started from that exact commit on
  `codex/llamacpp-competition-v1`.
- It does not alter the installer, public release, runtime selection, model
  storage, routes, or UI.

## Runtime provenance

The runtime came from the official
[ggml-org/llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases).
The stable `v0.4.1` release pointer selected nightly build `b10964`.

- Reported binary version: `0.4.1-dev`, build `10964`
- Source commit: `b29c606e28a01b1bc8c1351026a0fa6e616bf6c4`
- Main artifact: `llama-b10964-bin-win-cuda-12.4-x64.zip`
- Main artifact SHA-256:
  `264f20d7ee3860aecca9ec12418357a9f3e80349a2b186f66c63859ded1a9593`
- CUDA runtime artifact: `cudart-llama-bin-win-cuda-12.4-x64.zip`
- CUDA runtime SHA-256:
  `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6`
- Local executable:
  `%USERPROFILE%\.codex\visualizations\2026\09\20\01a0c0de-f8ec-7402-945b-70acd886d5c9\llamacpp\runtime\llama-server.exe`

Both downloaded hashes matched the digests published by the upstream release.
The 646 MB of compressed runtime artifacts remained in the disposable local
evidence directory and were not added to AFK packaging.

## Model comparability

The comparison used the exact GGUF layer already referenced by the local
Ollama `qwen2.5:14b` manifest. The layer was opened read-only in Ollama's blob
store; it was not copied, converted, or modified.

- Architecture: Qwen 2
- Parameter class: 14.8 billion
- Quantization: `Q4_K_M`
- GGUF bytes: `8,988,110,688`
- Shared GGUF layer SHA-256:
  `2049f5674b1e92b4464e5729975c9689fcfbf0b0e4443ccf10b5339f370f9a54`
- Ollama manifest digest:
  `7cdf5a0187d5c58cc5d369b255592f7841d1c4696d45a8c8a9489440385b22f6`

This is exact weight and quantization parity. Runtime templates were also
matched: llama.cpp's chat endpoint produced the same 58 prompt tokens as
Ollama for the fixed benchmark text.

## Safety gate

The live optimizer profile allowed 4K and 8K. It rejected 16K because the
estimated 11.37 GiB demand exceeded both the 10.20 GiB available RAM budget and
the GPU headroom estimate. The spike did not bypass that result. Since 16K was
unsafe, 32K was not attempted.

This differs from the earlier Optimizer V1 run because available system memory
was lower during this spike. The 16K baseline remains valid for its earlier
machine state; this run does not claim a current 16K comparison.

## Method

- Hardware class: NVIDIA GPU with 12 GB VRAM (public evidence is sanitized)
- Fixed short prompt: 58 actual prompt tokens
- Output target: 48 tokens
- Contexts measured: 4K and 8K
- One warmup and three measured requests per configuration
- Median aggregation
- Monotonic client timing for TTFT and total latency
- Native runtime counters for prompt and decode throughput
- llama.cpp prompt caching disabled explicitly
- Runtime unloaded or stopped before the competing runtime started
- Dedicated llama.cpp loopback port, one slot, all GPU layers, Flash Attention,
  and Q8 K/V cache

The long check used a deterministic, non-private synthetic equipment log. A
matched pass marker changed at the beginning of each request so Ollama could
not reuse the warmup prefix. Each measured runtime therefore processed 5,873
actual prompt tokens.

## Short prompt results

| Context | Runtime | TTFT | Prompt tok/s | Decode tok/s | Total | Cold/model-ready observation | VRAM |
|---:|---|---:|---:|---:|---:|---:|---:|
| 4K | Ollama 0.34.0 | 0.062 s | 1,955.76 | 33.83 | 1.461 s | 5.17 s unloaded-to-warmup completion | 8.47 GiB |
| 4K | llama.cpp b10964 | 0.070 s | 1,097.57 | 33.07 | 1.491 s | 8.48 s model ready; 1.50 s warmup | 8.67 GiB |
| 8K | Ollama 0.34.0 | 0.059 s | 1,921.23 | 33.66 | 1.478 s | 5.17 s unloaded-to-warmup completion | 8.87 GiB |
| 8K | llama.cpp b10964 | 0.063 s | 1,052.12 | 33.18 | 1.488 s | 8.57 s model ready; 1.52 s warmup | 9.07 GiB |

For the short prompt, llama.cpp was 6% to 12% slower to first token, about 44%
to 45% lower in reported prompt throughput, and 1% to 2% lower in decode
throughput. Total latency was within 2%. Short-prompt prompt throughput is less
important than TTFT and total latency and can be sensitive to runtime prefix
reuse, so it is not treated as a standalone decision metric.

## Long prompt reality check

| Runtime | Prompt tokens | TTFT | Prompt tok/s | Decode tok/s | Total | VRAM |
|---|---:|---:|---:|---:|---:|---:|
| Ollama 0.34.0 | 5,873 | 3.425 s | 1,864.07 | 30.59 | 4.997 s | 8.87 GiB |
| llama.cpp b10964 | 5,873 | 3.369 s | 1,872.53 | 29.44 | 4.966 s | 9.07 GiB |

The long prompt result is effectively tied. llama.cpp was about 1.6% faster to
first token, 0.5% faster in prompt processing, and 0.6% faster end to end. It
decoded about 3.8% slower and used about 0.20 GiB more observed VRAM.

## Memory and lifecycle observations

- Ollama's `/api/ps` reported model residency of 8.47 GiB at 4K and 8.87 GiB
  at 8K.
- Windows WDDM did not expose reliable per-process VRAM for the standalone
  server. The adapter therefore recorded the before/after total GPU-memory
  delta while Ollama had no loaded model: 8.67 GiB at 4K and 9.07 GiB at 8K.
- llama.cpp process working set was 8.42 GiB for the short runs and 10.16 GiB
  after the long prompt. Ollama does not expose an equivalent process working
  set through its API, so those RAM values are observational rather than a
  direct comparison.
- The adapter owns the standalone process, validates `/health`, verifies the
  effective context and exact served model through `/props`, detects early
  exit and timeouts, and uses terminate with a bounded kill fallback.
- Every standalone server was stopped after its configuration. Ollama's model
  inventory was empty after the final run.

## Product interpretation

llama.cpp offers direct flag control, native timing fields, and a clean
app-owned process boundary. Those are useful experimental capabilities.

The measured performance did not produce a meaningful product advantage. The
long workload was tied, decode was slightly slower, VRAM was slightly higher,
and the cold model path was longer. Production support would add a second GPU
runtime, roughly 646 MB of compressed Windows/CUDA artifacts, signature and
update ownership, process supervision, backend compatibility testing, and a
model-store strategy. Reusing Ollama's private blob layout would create an
undesirable production dependency; owning GGUF files could duplicate about
9 GB per model.

## Implementation and evidence

- `experiments/llamacpp_competition/adapter.py` implements the isolated
  process/runtime adapter outside the production payload.
- `experiments/llamacpp_competition/runner.py` reproduces the bounded
  comparison.
- `optimizer.py` carries optional startup, warmup, RAM, and VRAM observations
  without changing recommendation policy.
- `optimizer_ollama.py` accepts custom deterministic prompts and can unload
  only the named benchmark model.
- Focused tests cover unavailable and invalid executables, missing models,
  startup failure, malformed timing, timeout, context mismatch, process exit,
  runtime and model identity changes, runtime cache separation, process flags,
  memory parser degradation, and prompt-variant bounds.
- Raw sanitized evidence is in
  `docs/evidence/llamacpp-competition-v1.json`.

## Next bounded milestone

Do not start production Runtime Competition V2 from this result. Reopen the
competition after a material upstream runtime change or a product requirement
that Ollama cannot satisfy. The next bounded step is to rerun this exact
contract from a fresh machine state with current pinned Ollama and llama.cpp
builds, including a safe 16K rung if the optimizer admits it.

If that recheck establishes product value, Runtime Competition V2 should cover
only: app-owned process supervision, a signed runtime update channel, an owned
GGUF storage/deduplication policy, runtime selection and fallback policy, and
diagnostic/UI exposure. Packaging and default-runtime changes require a
separate decision after that milestone.
