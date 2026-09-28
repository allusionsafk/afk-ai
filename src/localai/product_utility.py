"""Pure, product-facing model fit and recommendation policy."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard

from localai.model_scout import VRAM_OVERHEAD_GB, kv_gb_per_1k
from localai.optimizer import GIB, HardwareProfile, ModelProfile
from localai.product_config import valid_model_tag

MAX_BYTES = 2**53 - 1
MAX_MODELS = 128
MAX_TREE_ROWS = 4096
GPU_RESERVE_BYTES = int(VRAM_OVERHEAD_GB * GIB)
RAM_RESERVE_BYTES = 5 * GIB
_QUANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
_SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]{1,256}\Z")
_SHARD = re.compile(r"(.+)-(\d{5})-of-(\d{5})\.gguf\Z", re.I)


@dataclass(frozen=True)
class ArtefactSize:
    bytes: int
    files: tuple[str, ...]


def artifact_size_from_files(
    files: Iterable[tuple[str, object]], quantization: str
) -> ArtefactSize | None:
    """Resolve one quant's complete GGUF set; ambiguity is never exact evidence."""
    if not _QUANT.fullmatch(quantization):
        return None
    selected: list[tuple[str, int]] = []
    marker = re.compile(rf"(?:^|[-/]){re.escape(quantization)}(?:-|\.gguf\Z)", re.I)
    ud_variant = re.compile(
        rf"(?:^|[-/])UD-{re.escape(quantization)}(?:-|\.gguf\Z)", re.I
    )
    for index, (path, size) in enumerate(files):
        if index >= MAX_TREE_ROWS:
            return None
        if not isinstance(path, str) or not marker.search(path):
            continue
        if not quantization.upper().startswith("UD-") and ud_variant.search(path):
            continue
        if (
            not _SAFE_PATH.fullmatch(path)
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not path.lower().endswith(".gguf")
            or not _valid_bytes(size)
        ):
            return None
        selected.append((path, size))
    if not selected:
        return None
    if len({path for path, _ in selected}) != len(selected):
        return None
    if len(selected) == 1 and _SHARD.fullmatch(selected[0][0]) is None:
        return ArtefactSize(selected[0][1], (selected[0][0],))
    shards = [_SHARD.fullmatch(path) for path, _ in selected]
    if any(match is None for match in shards):
        return None
    complete = [match for match in shards if match is not None]
    prefixes = {match.group(1) for match in complete}
    totals = {int(match.group(3)) for match in complete}
    if len(prefixes) != 1 or len(totals) != 1:
        return None
    expected = totals.pop()
    indices = {int(match.group(2)) for match in complete}
    if not 1 <= expected <= 128 or indices != set(range(1, expected + 1)):
        return None
    total = sum(size for _, size in selected)
    if total > MAX_BYTES:
        return None
    return ArtefactSize(total, tuple(path for path, _ in sorted(selected)))


def _valid_bytes(value: object) -> TypeGuard[int]:
    return type(value) is int and 0 < value <= MAX_BYTES


def _kv(model: ModelProfile, context: int) -> tuple[int | None, str]:
    per_token = model.kv_bytes_per_token
    if _valid_bytes(per_token) and per_token <= GIB:
        demand = per_token * context
        return (
            (demand, "architecture estimate")
            if demand <= MAX_BYTES
            else (None, "unknown")
        )
    params = model.parameter_count
    if type(params) is int and 0 < params <= 10**13:
        bucket = kv_gb_per_1k(params / 1e9)
        estimate = math.ceil(bucket * context / 1024 * GIB)
        return estimate, "parameter estimate"
    return None, "unknown"


def _purpose(model: ModelProfile) -> str:
    label = model.identifier.lower()
    family = (model.family or "").lower()
    if "embed" in label or "embed" in family:
        return "embedding"
    if label.startswith(("image-", "voice-", "web-", "terminal-", "valclip-")) or (
        "coder" in label or "-vl" in label
    ):
        return "specialized"
    return "general"


def _fit_row(
    hardware: HardwareProfile,
    model: ModelProfile,
    configured_model: str | None,
    loaded_models: Mapping[str, str] | None,
    context: int,
) -> dict[str, object]:
    weights = model.artifact_bytes if _valid_bytes(model.artifact_bytes) else None
    kv, kv_source = _kv(model, context)
    gpu_totals = [
        gpu.dedicated_vram_bytes
        for gpu in hardware.gpus
        if _valid_bytes(gpu.dedicated_vram_bytes)
    ]
    gpu_budget = max(gpu_totals, default=0) - GPU_RESERVE_BYTES
    gpu_budget = max(0, gpu_budget)
    ram_total = hardware.ram_total_bytes
    ram_budget = (
        max(0, ram_total - RAM_RESERVE_BYTES) if _valid_bytes(ram_total) else None
    )
    demand = weights + kv if weights is not None and kv is not None else None
    maximum = model.max_context
    if type(maximum) is int and 0 < maximum < context:
        verdict, reason = "Does not fit", "Requested context exceeds the model maximum."
    elif demand is None or demand > MAX_BYTES:
        verdict = "Unknown"
        reason = "Model size or context cache requirement is unknown."
    elif gpu_budget > 0 and demand <= gpu_budget:
        verdict, reason = "Fits", "Weights and estimated context cache fit usable VRAM."
    elif ram_budget is not None and demand <= ram_budget:
        verdict = "RAM-assisted"
        reason = (
            "Weights and estimated context cache exceed usable VRAM; "
            "RAM may assist. Speed is unmeasured."
        )
    elif ram_budget is not None:
        verdict = "Does not fit"
        reason = "Estimated memory exceeds conservative RAM headroom."
    else:
        verdict, reason = "Unknown", "System RAM capacity is unknown."
    digest = model.digest
    loaded_digest = (
        loaded_models.get(model.identifier) if loaded_models is not None else None
    )
    loaded: bool | None = (
        None
        if loaded_models is None
        else (
            loaded_digest == digest
            if isinstance(digest, str) and digest and isinstance(loaded_digest, str)
            else False
            if loaded_digest is None
            else None
        )
    )
    return {
        "model": model.identifier,
        "digest": digest,
        "source": "ollama local inventory",
        "family": model.family,
        "purpose": _purpose(model),
        "parameters": model.parameter_count,
        "quantization": model.quantization,
        "weights_bytes": weights,
        "weights_source": (
            "ollama exact model size" if weights is not None else "unknown"
        ),
        "context": context,
        "kv_bytes": kv,
        "kv_source": kv_source,
        "vram_required_bytes": demand,
        "vram_budget_bytes": gpu_budget or None,
        "ram_budget_bytes": ram_budget,
        "verdict": verdict,
        "reason": reason,
        "confidence": (
            "bounded estimate" if verdict != "Unknown" else "insufficient evidence"
        ),
        "installed": True,
        "configured": model.identifier == configured_model,
        "loaded": loaded,
        "healthy": None,
        "measurement": None,
    }


def build_report(
    hardware: HardwareProfile,
    installed_models: Sequence[ModelProfile],
    configured_model: str | None,
    loaded_models: Mapping[str, str] | None,
    *,
    context: int,
) -> dict[str, object]:
    if not 1024 <= context <= 1_048_576:
        raise ValueError("context is outside the supported report range")
    counts: dict[str, int] = {}
    for model in installed_models[:MAX_MODELS]:
        counts[model.identifier] = counts.get(model.identifier, 0) + 1
    rows = [
        _fit_row(hardware, model, configured_model, loaded_models, context)
        for model in installed_models[:MAX_MODELS]
        if valid_model_tag(model.identifier) and counts[model.identifier] == 1
    ]
    eligible = [
        row for row in rows if row["verdict"] == "Fits" and row["purpose"] == "general"
    ]
    chosen = next((row for row in eligible if row["configured"]), None)
    basis = "configured model fits in VRAM"
    if chosen is None and eligible:
        chosen = max(
            eligible,
            key=lambda row: (
                row["parameters"] if type(row["parameters"]) is int else 0,
                row["weights_bytes"] if type(row["weights_bytes"]) is int else 0,
                str(row["model"]),
            ),
        )
        basis = "largest installed general model fitting VRAM at this context"
    recommendation: dict[str, object] | None = (
        {
            "model": chosen["model"],
            "basis": basis,
            "reason": chosen["reason"],
            "context": context,
            "source": "rule-based estimate",
        }
        if chosen is not None
        else None
    )
    return {
        "schema_version": 1,
        "hardware": {
            "cpu": hardware.cpu,
            "logical_cores": hardware.logical_cores,
            "ram_total_bytes": hardware.ram_total_bytes,
            "ram_available_bytes": hardware.ram_available_bytes,
            "gpus": [
                {
                    "name": gpu.name,
                    "vram_total_bytes": gpu.dedicated_vram_bytes,
                    "vram_free_bytes": gpu.free_vram_bytes,
                }
                for gpu in hardware.gpus
            ],
            "platform": hardware.platform,
        },
        "context": context,
        "models": rows,
        "truncated": len(installed_models) > MAX_MODELS,
        "configured_model": configured_model,
        "recommendation": recommendation,
    }
