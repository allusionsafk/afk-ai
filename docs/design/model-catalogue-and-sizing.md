# Model catalogue and resident-memory sizing

Status: **design and research only; no runtime change, no default change**
Scope: `src/localai/model_scout.py`, `src/localai/scout_categories.py`

This document proposes the smallest backward-compatible catalogue extension that
lets Model Scout decide from architecture rather than from a parameter count. It
does not change `installer/tiers.json`, any model default, any Modelfile, or any
category weight.

Every claim below is labelled:

| Label | Meaning |
|---|---|
| **[SOURCE]** | Verified against an authoritative external source, fetched 2026-08-29. |
| **[CODE]** | Verified by reading this repository at `dbd8107`, or by running it. |
| **[INFER]** | Reasoned conclusion. Not measured. |
| **[BENCH]** | Requires an AFK AI measurement before it can be trusted. |

---

## 1. What is actually wrong today

The framing "Scout maps VRAM → parameter count → model" understates what exists.
`scout_categories.py` already carries workload profiles with per-category target
context and scoring weights, and `category_fit` already folds KV cache into the
verdict. The real gaps are narrower and more specific.

### 1.1 Sizing is decoupled from quantization — the primary defect

**[CODE]** Weight size is computed as `total_params × WEIGHTS_GB_PER_B`, with
`WEIGHTS_GB_PER_B = 0.6`, in both `fit_candidate` and `category_fit`.

**[CODE]** The quant that will actually be pulled is chosen *elsewhere and
later*, by `best_quant(repo)`, which runs in `prepare_pick` — after scouting has
already produced its verdicts and rankings. The verdict therefore never reflects
the file that gets downloaded.

**[CODE]** `best_quant` ends with `return quants[0] if quants else None`. When a
repository publishes no tag in `QUANT_PREFERENCE`, it returns an **arbitrary**
quant — which may be `Q8_0` or `Q2_K`.

**[SOURCE]** Authoritative bits-per-weight, from the llama.cpp *Tensor Encoding
Schemes* wiki:

| Scheme | BPW | | Scheme | BPW |
|---|---|---|---|---|
| Q8_0 | 8 | | Q4_K | 4.5 |
| Q6_K | 6.5625 | | IQ4_XS | 4.25 |
| Q5_K | 5.5 | | Q4_0 | 4 |
| IQ4_NL | 4.5 | | Q3_K | 3.4375 |
| F16 / BF16 | 16 | | Q2_K | 2.5625 |

`0.6 bytes/param` is 4.8 bpw. Across the repository's own `QUANT_PREFERENCE`
list the base types span 3.4375–4.5 bpw, and the arbitrary fallback reaches 8.

**[CODE, measured 2026-08-29]** Against real Hugging Face file sizes, the
constant is nevertheless **well calibrated for `Q4_K_M` specifically** — the
higher-precision embedding and output tensors that K-quants retain make the
effective file larger than the base 4.5 bpw suggests:

| Repository | Params | Quant | Real GB | `0.6 × B` | Error |
|---|---|---|---|---|---|
| `unsloth/Qwen3-8B-GGUF` | 8.2 | Q4_K_M | 5.03 | 4.92 | −2 % |
| `unsloth/Qwen3-14B-GGUF` | 14.8 | Q4_K_M | 9.00 | 8.88 | −1 % |
| `unsloth/Qwen3-30B-A3B-GGUF` | 30.5 | Q4_K_M | 18.56 | 18.30 | −1 % |
| `unsloth/gpt-oss-20b-GGUF` | 20.9 | Q4_K_M | 11.62 | 12.54 | **+8 %** |

So the honest statement is **not** "the constant is wrong". It is: *the constant
is a good `Q4_K_M` approximation being applied to outcomes that are not
`Q4_K_M`*. A `Q3_K_M` pick is over-priced by ~25 %, a `Q8_0` fallback is
under-priced by ~40 % — and under-pricing is the dangerous direction, because it
reports that a model fits when it does not.

### 1.2 The real size is already fetched and thrown away

**[CODE, verified by running it]** `fetch_hf_tree(repo)` calls
`https://huggingface.co/api/models/{repo}/tree/main`. Each entry carries
`{path, size, oid, lfs, type, xetHash}`. `best_quant` iterates that exact tree
and reads only `path`, discarding `size`.

The exact resident weight size is therefore **already available at zero
additional network cost**. Estimating from a bits-per-weight constant when the
byte count is in hand is the wrong default.

### 1.3 KV cache is bucketed when exact metadata is obtainable

**[CODE]** `kv_gb_per_1k` selects from three hard-coded buckets keyed on total
parameters (`<4 B → 0.11`, `<14 B → 0.16`, `<32 B → 0.20`, else `0.26` GB per 1 k
tokens at f16). The comment correctly notes KV tracks *total*, not active.

**[SOURCE]** The canonical llama.cpp KV size is exact, not bucketed:

```
kv_bytes = n_layer × 2 × n_ctx × n_head_kv × head_dim × bytes_per_element
```

(`2` = one K and one V cache.)

**[SOURCE]** Ollama's `/api/show` returns a `model_info` object carrying the GGUF
architecture keys needed to evaluate that formula directly — `block_count`,
`attention.head_count`, `attention.head_count_kv`, `context_length`,
`embedding_length` — plus a `capabilities` array (documented values include
`completion` and `vision`).

**[INFER]** `head_dim` is `embedding_length / attention.head_count` when a GGUF
does not publish an explicit key/value length.

**[INFER]** `/api/show` describes a model that is **already pulled**, so it
cannot price a *candidate*. For candidates, the equivalent fields are in the
source repository's `config.json` (`num_hidden_layers`, `num_key_value_heads`,
`hidden_size`, `num_attention_heads`, and `head_dim` where published). Neither
source is guaranteed, so the buckets must remain as a fallback.

### 1.4 Active parameters were used as a substitute for residency

**[CODE]** Fixed in the branch `fix/moe-vram-fit`: both fit paths now apply an
identical VRAM test to MoE and dense, and a low-active MoE earns a cheaper
*spill* verdict rather than a false claim of fitting. Recorded here so the
catalogue schema does not reintroduce the confusion: **`active_params` is a
compute and offload property and must never reduce a memory estimate.**

### 1.5 Runtime and backend constraints are unmodelled

**[CODE]** Nothing records which Ollama or llama.cpp version a model needs. A
newly published architecture that the installed runtime cannot load is scored,
ranked, and can be recommended; the failure surfaces only at pull or load time.

**[SOURCE]** OpenAI's gpt-oss models ship **natively quantized in MXFP4** — MoE
linear-projection weights at 4.25 bits/parameter with the remaining tensors in
BF16, with the MoE weights accounting for 90 %+ of parameters. gpt-oss-20b is
documented to run within 16 GB. This is a released-native low-precision format,
categorically different from post-training quantization of a BF16 checkpoint,
and a `Q4_K_M`-labelled repackaging of it is not the same object as a `Q4_K_M`
quantization of a BF16 model.

### 1.6 There is no place to record a measurement

**[CODE]** `measure_speed` exists and produces a number, but no schema stores
what hardware, quant, context, or cache dtype produced it. A measurement that
cannot be attributed cannot be compared across runs, and cannot justify changing
a default.

---

## 2. Proposed schema extension

Additive and backward compatible. Every field is optional; absent fields fall
back to today's behaviour, so an entry written before this change stays valid.

The guiding rule: **include a field only if it changes a decision.** Anything
merely interesting is excluded.

### 2.1 Resident weight sizing

```python
@dataclass(frozen=True)
class WeightSizing:
    """How much must be resident, and how confident we are."""
    bytes_total: int | None      # exact file size when known
    quant: str | None            # "Q4_K_M", "IQ4_XS", "MXFP4", ...
    quant_provenance: str        # "measured-file" | "bpw-table" | "global-heuristic"
    source: str | None           # repo/tag the size came from
```

Resolution order, most trustworthy first:

1. **`measured-file`** — the GGUF byte size from the Hugging Face tree, already
   fetched by `fetch_hf_tree`. Decides: everything, exactly.
2. **`bpw-table`** — `params × bpw(quant) / 8`, using the llama.cpp table in
   §1.1, when the quant is known but the file is not listed. Decides: a
   `Q3_K_M`/`Q8_0` outcome is priced as itself instead of as `Q4_K_M`.
3. **`global-heuristic`** — today's `params × WEIGHTS_GB_PER_B`. Retained
   unchanged as the last resort so behaviour degrades to current behaviour
   rather than to an error.

**The quant must be resolved before the verdict, not after.** That is the
structural change; the table is secondary.

### 2.2 Architecture, for exact KV

```python
@dataclass(frozen=True)
class ArchitectureInfo:
    n_layer: int | None
    n_head_kv: int | None
    head_dim: int | None
    native_context: int | None
    is_moe: bool
    total_params_b: float | None    # residency
    active_params_b: float | None   # compute/offload ONLY - never reduces memory
```

`kv_bytes = n_layer × 2 × n_ctx × n_head_kv × head_dim × bytes(kv_dtype) ×
n_parallel`, falling back to `kv_gb_per_1k` buckets when any field is missing.

**[SOURCE]** Ollama's KV cache quantization is real and already exploited by
this repository: `OLLAMA_KV_CACHE_TYPE` accepts `f16` (default), `q8_0`
(~half the memory of f16) and `q4_0` (~one quarter), and **requires
`OLLAMA_FLASH_ATTENTION` to be enabled**. It is a global setting affecting all
models.

**[CODE]** `installer/installer-common.ps1` already sets both
`OLLAMA_KV_CACHE_TYPE=q8_0` and `OLLAMA_FLASH_ATTENTION=1`, so the precondition
holds on an AFK AI install, and `KV_DTYPE_FACTORS` already reads the variable.
No change needed here — recorded so it is not "fixed" by someone unaware.

**[SOURCE]** `OLLAMA_NUM_PARALLEL` defaults to 1, and Ollama documents that
required memory scales by `OLLAMA_NUM_PARALLEL × OLLAMA_CONTEXT_LENGTH`, which
is why `n_parallel` belongs in the formula.

### 2.3 Runtime requirement

```python
@dataclass(frozen=True)
class RuntimeRequirement:
    backend: str                  # "ollama" | "llama.cpp"
    min_version: str | None       # only when a concrete need is documented
    needs_native_low_precision: bool   # MXFP4/NVFP4-style released weights
```

**Do not invent a version floor.** Populate `min_version` only where a specific
architecture or format is documented to need it. An unpopulated requirement must
never block a candidate.

### 2.4 Benchmark evidence

```python
@dataclass(frozen=True)
class BenchmarkRecord:
    model_tag: str
    gpu: str                 # GPU name as reported by nvidia-smi
    vram_gb: float
    quant: str
    kv_cache_type: str
    requested_ctx: int
    allocated_ctx: int | None    # what the runtime actually gave
    ttft_ms: float | None
    prefill_tps: float | None
    decode_tps: float | None
    resident_vram_gb: float | None
    host_ram_gb: float | None
    gpu_offload_pct: float | None
    outcome: str             # "ok" | "oom" | "load-failed" | "timeout"
    measured_at: str
    source: str              # "afk-ai-local" | external citation
```

`requested_ctx` versus `allocated_ctx` is the field that matters most: a runtime
silently reducing context is the failure this schema exists to catch, and it is
invisible in a tokens-per-second number alone.

---

## 3. What this deliberately does not do

- No change to `installer/tiers.json`, model defaults, Modelfiles, compose
  defaults, curated seeds, or category weights.
- No replacement of Ollama, and no architecture for speculative future runtimes.
- No new model families added to the catalogue. This is a *shape* proposal; a
  populated catalogue is a separate, evidence-gated exercise.
- No large model pulls and no fleet benchmarking.

## 4. Implementation order

1. **Use the size already fetched.** Keep `size` in `best_quant`/`fetch_hf_tree`
   and thread `WeightSizing` into the fit functions. Behaviour-affecting, so it
   needs the benchmark check in step 4 before defaults move.
2. **Per-quant BPW table** as the second-tier fallback.
3. **Exact KV from architecture metadata** where available, buckets otherwise.
4. **Benchmark schema plus a small, defined experiment** (§5) before any default
   changes.

Steps 1–3 are independently reviewable and each preserves current behaviour when
metadata is absent.

## 5. What must be measured before any default moves

**[BENCH]** All of the following are unmeasured today:

- Whether the `active ≤ 6 B` MoE spill threshold — inherited, not derived —
  matches real decode throughput on the project's reference machine.
- The real cost of `q8_0` KV cache versus `f16` on that box: memory saved and
  any quality change on the grounded chat preset.
- Whether `VRAM_OVERHEAD_GB = 1.5` still holds at 32 k context with flash
  attention enabled.
- `requested_ctx` versus `allocated_ctx` for the current daily driver — whether
  the 32 k the installer requests is actually granted.

A defined minimum experiment: the current daily driver plus one MoE of similar
footprint, each at 8 k/16 k/32 k, each at `f16` and `q8_0` KV, recording the
§2.4 fields. That is 12 runs on models already present — no new large downloads.

## 6. Sources

Fetched 2026-08-29.

- llama.cpp, *Tensor Encoding Schemes* wiki — bits-per-weight per scheme:
  <https://github.com/ggml-org/llama.cpp/wiki/Tensor-Encoding-Schemes>
- llama.cpp KV cache dimensions discussion:
  <https://github.com/ggml-org/llama.cpp/discussions/7949>
- Ollama API reference, `/api/show` `model_info` and `capabilities`:
  <https://github.com/ollama/ollama/blob/main/docs/api.md>
- Ollama FAQ — `OLLAMA_KV_CACHE_TYPE`, `OLLAMA_FLASH_ATTENTION`,
  `OLLAMA_NUM_PARALLEL`, `OLLAMA_CONTEXT_LENGTH`:
  <https://docs.ollama.com/faq>
- OpenAI, *Introducing gpt-oss* — native MXFP4 weights:
  <https://openai.com/index/introducing-gpt-oss/>
- gpt-oss model card — MXFP4 at 4.25 bits/parameter on MoE weights, 90 %+ of
  parameters, 20b within 16 GB: <https://arxiv.org/pdf/2508.10925>
- Hugging Face tree API (`size` per file), verified by direct request against
  `unsloth/Qwen3-8B-GGUF`, `unsloth/Qwen3-14B-GGUF`,
  `unsloth/Qwen3-30B-A3B-GGUF`, `unsloth/gpt-oss-20b-GGUF`.
