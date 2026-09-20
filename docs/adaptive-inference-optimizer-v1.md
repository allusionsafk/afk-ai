# Adaptive inference optimizer V1

The optimizer profiles local hardware and one **already installed** Ollama
model, measures bounded generation runs at safe configured context sizes, and
recommends a context with separate estimated and measured evidence. It uses
only the local Ollama API and writes no telemetry or prompt text to the cache.

## Run

```powershell
$env:PYTHONPATH = 'src'
py -3.12 -m localai.optimizer_cli qwen2.5:14b --mode balanced --max-context 8192
```

Use an exact model tag from `ollama list`. `--cached-only` reports existing
measurements without generating. `--no-cache` generates again. Use
`--override-context 4096` to select a different safely estimated and measured
context. The CLI refuses an override outside those bounds. The command returns
JSON for later UI/API consumption and exits nonzero when no profile can be
recommended.

## Evidence contract

- `hardware` records CPU, total/available RAM, all NVIDIA GPUs visible to
  `nvidia-smi`, and Windows/platform information. The fingerprint hashes only
  stable hardware fields. Hostnames and serial numbers are excluded.
- `model` comes from Ollama `/api/tags` and `/api/show`: exact tag, digest,
  family, parameter size, quantization, artifact size, maximum context, and
  architecture KV fields when present. Missing facts remain null.
- `runtime` records Ollama's API version and explicit generation options.
  Client environment settings tighten cache identity but are **not** asserted
  to be server-effective settings. Backend remains unknown because the local
  API does not reliably name it. `/api/ps` residency is recorded separately.
- `estimates` use architecture KV dimensions when available, otherwise the
  existing scout parameter buckets. The estimate assumes f16 KV and reserves
  physical RAM headroom. It also reports whether weights plus KV appear to fit
  the largest single GPU with scout's VRAM reserve; GPU fit is an estimate and
  CPU offload is allowed when system RAM is safe. Unknown model context prevents
  a benchmark.
- `measurements` are a separate warm-up followed by three runs with one fixed
  prompt and 48 generated tokens each. Wall latency and first token timing use
  a monotonic clock; prompt and decode throughput use Ollama's token counts and
  durations. A median limits the effect of one slow run. All three runs and
  `/api/ps` effective context verification must succeed. VRAM residency is a
  post-run observation, not a peak memory measurement.
- `recommended_context`, `reasons`, `alternatives`, `confidence`, and
  `selected_context` provide the policy result. Interactive favors response
  speed, balanced allows some speed cost for more context, and long-context
  chooses the largest safe measured context. A failed run stops higher tests.

The fixed prompt is short. A successful 16K **configured** context does not
prove throughput with a 16K-token prompt. The result includes actual prompt
tokens so future UI can state this limit plainly.

Ollama does not expose all server launch settings or a reliable backend name
through these local endpoints. A server setting changed outside the CLI's
visible environment may require `--no-cache` to force a new measurement. V1
therefore labels cache-derived evidence and does not claim portability across
machines or runtime backends.

## Cache

The default cache is `%LOCALAPPDATA%\AFK AI\optimizer\measurements-v1.json`.
Entries have a schema version and a deterministic key over hardware fingerprint,
model digest, runtime version/backend, request options, context, prompt hash, and
benchmark protocol version. Missing digest/version disables reuse. Entries
expire after 30 days; only successful profiles are saved; at most 128 entries
remain. Corrupt or stale entries are ignored. Writes use a temporary file and
atomic replacement. The cache is separate from conversation history.
