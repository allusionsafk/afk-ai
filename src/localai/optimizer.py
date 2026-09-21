"""Runtime-neutral inference evidence, cache, and conservative context policy."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Literal

from localai.model_scout import VRAM_OVERHEAD_GB, estimate_kv_gb

CONTRACT_VERSION = 1
PROTOCOL_VERSION = 3
PROMPT = (
    "Describe how a bicycle brake converts a rider's hand force into stopping "
    "force. Explain the cable, lever, friction, and heat in plain language."
)
PROMPT_HASH = hashlib.sha256(PROMPT.encode()).hexdigest()
NUM_PREDICT = 48
WARMUPS = 1
RUNS = 3
MAX_CACHE_ENTRIES = 128
CACHE_AGE_DAYS = 30
GIB = 1024**3


@dataclass(frozen=True)
class GPU:
    name: str
    dedicated_vram_bytes: int | None
    free_vram_bytes: int | None


@dataclass(frozen=True)
class HardwareProfile:
    cpu: str | None
    logical_cores: int | None
    ram_total_bytes: int | None
    ram_available_bytes: int | None
    gpus: tuple[GPU, ...]
    platform: str

    @property
    def fingerprint(self) -> str:
        # Available memory is dynamic. No host name, serial number, or user ID.
        stable = {
            "cpu": self.cpu,
            "logical_cores": self.logical_cores,
            "ram_total_bytes": self.ram_total_bytes,
            "gpus": [(g.name, g.dedicated_vram_bytes) for g in self.gpus],
            "platform": self.platform,
        }
        return _hash(stable)


@dataclass(frozen=True)
class ModelProfile:
    identifier: str
    digest: str | None
    family: str | None
    parameter_count: int | None
    quantization: str | None
    artifact_bytes: int | None
    max_context: int | None
    kv_bytes_per_token: int | None
    architecture: dict[str, int]


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    version: str | None
    backend: str | None
    options: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class Run:
    success: bool
    total_seconds: float
    first_token_seconds: float | None
    prompt_tokens: int | None
    prompt_tokens_per_second: float | None
    decode_tokens: int | None
    decode_tokens_per_second: float | None
    error: str | None = None


@dataclass(frozen=True)
class Measurement:
    context: int
    successful: bool
    runs: tuple[Run, ...]
    median_total_seconds: float | None
    median_first_token_seconds: float | None
    median_prompt_tokens_per_second: float | None
    median_decode_tokens_per_second: float | None
    measured_at: str
    error: str | None
    source: Literal["fresh", "cache"] = "fresh"
    effective_context: int | None = None
    resident_vram_bytes: int | None = None
    resident_total_bytes: int | None = None
    startup_seconds: float | None = None
    resident_ram_bytes: int | None = None
    warmup_seconds: float | None = None


@dataclass(frozen=True)
class ContextEstimate:
    context: int
    demand_bytes: int | None
    budget_bytes: int | None
    kv_bytes: int | None
    kv_method: str
    safe: bool
    reason: str
    gpu_budget_bytes: int | None = None
    gpu_fit_estimate: bool | None = None


@dataclass(frozen=True)
class Recommendation:
    schema_version: int
    hardware: HardwareProfile
    model: ModelProfile
    runtime: RuntimeProfile
    mode: str
    recommended_context: int | None
    selected_context: int | None
    override_context: int | None
    confidence: str
    reasons: tuple[str, ...]
    alternatives: tuple[dict[str, Any], ...]
    estimates: tuple[ContextEstimate, ...]
    measurements: tuple[Measurement, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def measurement_key(
    hardware: HardwareProfile, model: ModelProfile, runtime: RuntimeProfile, ctx: int
) -> str | None:
    if not model.digest or not runtime.version:
        return None
    return _hash(
        {
            "contract": CONTRACT_VERSION,
            "protocol": PROTOCOL_VERSION,
            "hardware": hardware.fingerprint,
            "model_digest": model.digest,
            "runtime_name": runtime.name,
            "runtime_version": runtime.version,
            "runtime_backend": runtime.backend,
            "runtime_options": runtime.options,
            "context": ctx,
            "prompt_hash": PROMPT_HASH,
            "num_predict": NUM_PREDICT,
            "warmups": WARMUPS,
            "runs": RUNS,
        }
    )


def summarize(ctx: int, runs: tuple[Run, ...]) -> Measurement:
    # A tiny sample is useful only when every recorded run succeeds. A single
    # anomalous slow run is visible in runs but does not dominate the median.
    good = [r for r in runs if r.success and r.decode_tokens and r.decode_tokens > 0]
    ok = len(runs) == RUNS and len(good) == RUNS

    def middle(values: list[float | None]) -> float | None:
        actual = [v for v in values if v is not None and math.isfinite(v)]
        return median(actual) if len(actual) == RUNS else None

    return Measurement(
        context=ctx,
        successful=ok,
        runs=runs,
        median_total_seconds=middle([r.total_seconds for r in good]) if ok else None,
        median_first_token_seconds=(
            middle([r.first_token_seconds for r in good]) if ok else None
        ),
        median_prompt_tokens_per_second=(
            middle([r.prompt_tokens_per_second for r in good]) if ok else None
        ),
        median_decode_tokens_per_second=(
            middle([r.decode_tokens_per_second for r in good]) if ok else None
        ),
        measured_at=datetime.now(UTC).isoformat(),
        error=next(
            (r.error or "invalid benchmark run" for r in runs if not r.success),
            "incomplete benchmark",
        )
        if not ok
        else None,
    )


def context_ladder(model: ModelProfile, cap: int | None = None) -> tuple[int, ...]:
    if model.max_context is None:
        # Without a declared maximum even the floor may be impossible.
        return ()
    ceiling = min(x for x in (model.max_context, cap, 32768) if x is not None)
    if ceiling < 1024:
        return ()
    start = 12 if ceiling >= 4096 else 10
    values = [2**p for p in range(start, 16) if 2**p <= ceiling]
    return tuple(values)


def estimate_context(
    hardware: HardwareProfile, model: ModelProfile, context: int
) -> ContextEstimate:
    if model.max_context is not None and context > model.max_context:
        return ContextEstimate(
            context, None, None, None, "none", False, "model maximum"
        )
    if (
        not model.artifact_bytes
        or not hardware.ram_total_bytes
        or hardware.ram_available_bytes is None
    ):
        return ContextEstimate(
            context, None, None, None, "none", False, "missing size or available RAM"
        )
    if model.kv_bytes_per_token:
        kv = model.kv_bytes_per_token * context
        method = "architecture metadata"
    elif model.parameter_count:
        kv = int(
            estimate_kv_gb(
                model.parameter_count / 1e9, ctx=context, parallel=1, kv_factor=1.0
            )
            * GIB
        )
        method = "scout parameter bucket"
    else:
        # Unknown architecture/parameters: a bounded floor, not a fabricated KV.
        if context > 4096:
            return ContextEstimate(
                context, None, None, None, "unknown", False, "unknown KV size"
            )
        kv = 2 * GIB
        method = "conservative unknown-KV reserve"
    # Reserve 5 GiB for Windows and other applications, following scout's
    # existing RAM headroom. Dynamic available RAM can only tighten the limit.
    total_budget = max(0, hardware.ram_total_bytes - 5 * GIB)
    available_budget = max(0, hardware.ram_available_bytes - 2 * GIB)
    budget = min(total_budget, available_budget)
    demand = model.artifact_bytes + kv
    safe = demand <= budget
    reason = (
        "estimated memory headroom" if safe else "estimated memory exceeds headroom"
    )
    gpu_budget = max(
        (
            max(0, gpu.dedicated_vram_bytes - int(VRAM_OVERHEAD_GB * GIB))
            for gpu in hardware.gpus
            if gpu.dedicated_vram_bytes is not None
        ),
        default=None,
    )
    gpu_fit = demand <= gpu_budget if gpu_budget is not None else None
    return ContextEstimate(
        context, demand, budget, kv, method, safe, reason, gpu_budget, gpu_fit
    )


def _valid_run(run: Run) -> bool:
    return (
        run.success is True
        and isinstance(run.total_seconds, (int, float))
        and math.isfinite(run.total_seconds)
        and run.total_seconds > 0
        and isinstance(run.first_token_seconds, (int, float))
        and math.isfinite(run.first_token_seconds)
        and 0 < run.first_token_seconds <= run.total_seconds
        and isinstance(run.prompt_tokens, int)
        and run.prompt_tokens > 0
        and isinstance(run.decode_tokens, int)
        and run.decode_tokens > 0
        and isinstance(run.decode_tokens_per_second, (int, float))
        and math.isfinite(run.decode_tokens_per_second)
        and run.decode_tokens_per_second > 0
        and (
            run.prompt_tokens_per_second is None
            or (
                isinstance(run.prompt_tokens_per_second, (int, float))
                and math.isfinite(run.prompt_tokens_per_second)
                and run.prompt_tokens_per_second > 0
            )
        )
        and run.error is None
    )


def validate_measurement(measurement: Measurement, context: int) -> Measurement:
    """A fresh adapter result must prove the requested context and run contract."""
    if measurement.successful is not True:
        return measurement
    if (
        measurement.context != context
        or measurement.effective_context != context
        or len(measurement.runs) != RUNS
        or not all(_valid_run(run) for run in measurement.runs)
    ):
        return replace(
            measurement,
            successful=False,
            error="benchmark evidence or effective context mismatch",
        )
    verified = summarize(context, measurement.runs)
    return replace(
        verified,
        measured_at=measurement.measured_at,
        effective_context=context,
        resident_vram_bytes=measurement.resident_vram_bytes,
        resident_total_bytes=measurement.resident_total_bytes,
        startup_seconds=measurement.startup_seconds,
        resident_ram_bytes=measurement.resident_ram_bytes,
        warmup_seconds=measurement.warmup_seconds,
    )


class MeasurementCache:
    """Local, bounded, versioned JSON cache. Corrupt entries are ignored."""

    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict[str, Any]:
        try:
            if self.path.stat().st_size > 4 * 1024 * 1024:
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("schema") != CONTRACT_VERSION:
            return {}
        entries = data.get("entries")
        return entries if isinstance(entries, dict) else {}

    def get(
        self, key: str | None, *, now: datetime | None = None
    ) -> Measurement | None:
        if key is None:
            return None
        raw = self._read().get(key)
        if not isinstance(raw, dict):
            return None
        try:
            at = datetime.fromisoformat(raw["measured_at"])
            if at.tzinfo is None:
                return None
            age = (now or datetime.now(UTC)) - at
            if age > timedelta(days=CACHE_AGE_DAYS) or age < -timedelta(minutes=5):
                return None
            runs = tuple(Run(**item) for item in raw["runs"])
            entry = Measurement(**{**raw, "runs": runs, "source": "cache"})
            if (
                entry.successful is not True
                or len(runs) != RUNS
                or entry.effective_context != entry.context
                or not all(_valid_run(r) for r in runs)
            ):
                return None
            verified = summarize(entry.context, runs)
            if not verified.successful:
                return None
            return replace(
                verified,
                measured_at=entry.measured_at,
                source="cache",
                effective_context=entry.effective_context,
                resident_vram_bytes=entry.resident_vram_bytes,
                resident_total_bytes=entry.resident_total_bytes,
                startup_seconds=entry.startup_seconds,
                resident_ram_bytes=entry.resident_ram_bytes,
                warmup_seconds=entry.warmup_seconds,
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def put(self, key: str | None, measurement: Measurement) -> None:
        if (
            key is None
            or not measurement.successful
            or measurement.effective_context != measurement.context
        ):
            return
        entries = self._read()
        entries[key] = asdict(measurement)
        entries = dict(
            sorted(
                entries.items(),
                key=lambda item: (
                    str(item[1].get("measured_at", ""))
                    if isinstance(item[1], dict)
                    else ""
                ),
                reverse=True,
            )[:MAX_CACHE_ENTRIES]
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as output:
                temporary = output.name
                json.dump({"schema": CONTRACT_VERSION, "entries": entries}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def recommend(
    hardware: HardwareProfile,
    model: ModelProfile,
    runtime: RuntimeProfile,
    estimates: tuple[ContextEstimate, ...],
    measurements: tuple[Measurement, ...],
    *,
    mode: str = "balanced",
    override_context: int | None = None,
) -> Recommendation:
    if mode not in {"interactive", "balanced", "long-context"}:
        raise ValueError("unknown operating mode")
    eligible = {e.context: e for e in estimates if e.safe}
    successful = {
        m.context: m for m in measurements if m.successful and m.context in eligible
    }
    ordered = sorted(successful)
    reasons: list[str] = []
    chosen: int | None = None
    if ordered:
        baseline = successful[ordered[0]]
        if mode == "long-context":
            chosen = ordered[-1]
        else:
            responsive: list[int] = []
            for ctx in ordered:
                run = successful[ctx]
                speed = run.median_decode_tokens_per_second
                base_speed = baseline.median_decode_tokens_per_second
                first = run.median_first_token_seconds
                base_first = baseline.median_first_token_seconds
                speed_limit = 0.85 if mode == "interactive" else 0.70
                first_limit = 1.25 if mode == "interactive" else 1.75
                if (
                    speed is not None
                    and base_speed is not None
                    and speed >= base_speed * speed_limit
                    and (
                        first is None
                        or base_first is None
                        or first <= base_first * first_limit
                    )
                ):
                    responsive.append(ctx)
            chosen = max(responsive) if responsive else ordered[0]
        prompt_tokens = successful[chosen].runs[0].prompt_tokens
        reasons.append(
            f"three runs completed at configured context {chosen}; "
            f"prompt was {prompt_tokens or 'unknown'} tokens"
        )
        reasons.append(f"{eligible[chosen].reason}; KV: {eligible[chosen].kv_method}")
        if eligible[chosen].gpu_fit_estimate is False:
            reasons.append(
                "VRAM estimate suggests possible CPU offload; "
                "this is not a hard safety gate"
            )
        observed = successful[chosen]
        if (
            observed.resident_vram_bytes is not None
            and observed.resident_total_bytes is not None
            and observed.resident_vram_bytes == observed.resident_total_bytes
        ):
            reasons.append("Ollama reported full GPU residency after the measured run")
        if successful[chosen].source == "cache":
            reasons.append("measurement reloaded from a matching local cache entry")
    else:
        reasons.append("no safely estimated context has a successful measurement")
    if override_context is not None and override_context not in successful:
        raise ValueError(
            "override must be a safely estimated, successfully measured context"
        )
    selected = override_context if override_context is not None else chosen
    if override_context is not None:
        reasons.append(f"user selected measured override {override_context}")
    alternatives = tuple(
        {
            "context": e.context,
            "status": "measured" if e.context in successful else "rejected",
            "reason": (
                "eligible, mode preference"
                if e.context in successful
                else e.reason
                if not e.safe
                else "benchmark failed or unavailable"
            ),
        }
        for e in estimates
        if e.context != chosen
    )
    return Recommendation(
        schema_version=CONTRACT_VERSION,
        hardware=hardware,
        model=model,
        runtime=runtime,
        mode=mode,
        recommended_context=chosen,
        selected_context=selected,
        override_context=override_context,
        confidence="bounded short-prompt measurement"
        if chosen is not None
        else "insufficient evidence",
        reasons=tuple(reasons),
        alternatives=alternatives,
        estimates=estimates,
        measurements=measurements,
    )


def optimize(
    hardware: HardwareProfile,
    model: ModelProfile,
    runtime: RuntimeProfile,
    benchmark: Callable[[str, int], Measurement],
    cache: MeasurementCache,
    *,
    mode: str = "balanced",
    cap: int | None = None,
    override_context: int | None = None,
    use_cache: bool = True,
    measure: bool = True,
) -> Recommendation:
    if mode not in {"interactive", "balanced", "long-context"}:
        raise ValueError("unknown operating mode")
    contexts = context_ladder(model, cap)
    estimates = tuple(estimate_context(hardware, model, ctx) for ctx in contexts)
    measurements: list[Measurement] = []
    for estimate in estimates:
        if not estimate.safe:
            continue
        key = measurement_key(hardware, model, runtime, estimate.context)
        cached = cache.get(key) if use_cache else None
        if cached is not None and cached.context == estimate.context:
            measurements.append(cached)
            continue
        if not measure:
            continue
        result = validate_measurement(
            benchmark(model.identifier, estimate.context), estimate.context
        )
        measurements.append(result)
        if result.successful:
            cache.put(key, result)
        else:
            # A real failure overrides optimistic estimates for higher contexts.
            break
    return recommend(
        hardware,
        model,
        runtime,
        estimates,
        tuple(measurements),
        mode=mode,
        override_context=override_context,
    )
