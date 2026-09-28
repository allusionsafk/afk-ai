"""Bounded local Ollama adapter for the installed product's Utility view."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from localai.installer_vet import classify_tier, load_tiers
from localai.optimizer import HardwareProfile, ModelProfile
from localai.optimizer_ollama import (
    _architecture,
    _number,
    _parameter_count,
    benchmark_ollama,
    profile_hardware,
    profile_ollama,
)
from localai.product_config import valid_model_tag
from localai.product_utility import MAX_BYTES, MAX_MODELS, build_report

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ENRICHED_MODELS = 8
BASE = "http://127.0.0.1:11434"
PATHS = frozenset({"/api/version", "/api/tags", "/api/ps", "/api/show"})
_DIGEST = re.compile(r"[a-fA-F0-9]{64}\Z")
__all__ = [
    "benchmark_ollama",
    "collect_local_report",
    "profile_hardware",
    "profile_ollama",
]


def _json(path: str, body: dict[str, object] | None = None) -> dict[str, Any]:
    if path not in PATHS:
        raise ValueError("Unsupported Ollama API path")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urlopen(request, timeout=3) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError, TimeoutError) as error:
        raise RuntimeError(
            f"Ollama {path} is unavailable ({type(error).__name__})"
        ) from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"Ollama {path} response is too large")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise RuntimeError(f"Ollama {path} returned invalid JSON") from error
    if not isinstance(result, dict):
        raise RuntimeError(f"Ollama {path} returned invalid JSON object")
    return result


def _text(value: object, limit: int) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= limit else None


def _digest(value: object) -> str | None:
    return (
        value.lower() if isinstance(value, str) and _DIGEST.fullmatch(value) else None
    )


def _model_from_tag(row: object) -> ModelProfile | None:
    if not isinstance(row, dict):
        return None
    name = row.get("name")
    if not isinstance(name, str) or not valid_model_tag(name):
        return None
    details = row.get("details")
    details = details if isinstance(details, dict) else {}
    size = _number(row.get("size"))
    return ModelProfile(
        identifier=name,
        digest=_digest(row.get("digest")),
        family=_text(details.get("family"), 64),
        parameter_count=_parameter_count(details.get("parameter_size")),
        quantization=_text(details.get("quantization_level"), 32),
        artifact_bytes=size if size is not None and size <= MAX_BYTES else None,
        max_context=None,
        kv_bytes_per_token=None,
        architecture={},
    )


def _enrich(
    models: list[ModelProfile],
    configured_model: str | None,
    fetch: Callable[[str, dict[str, object] | None], Mapping[str, object]],
) -> tuple[list[ModelProfile], int]:
    from dataclasses import replace

    ranked = sorted(
        models,
        key=lambda model: (
            model.identifier == configured_model,
            model.parameter_count or 0,
            model.artifact_bytes or 0,
            model.identifier,
        ),
        reverse=True,
    )[:MAX_ENRICHED_MODELS]
    chosen = {model.identifier for model in ranked}
    enriched: list[ModelProfile] = []
    successes = 0
    for model in models:
        if model.identifier not in chosen:
            enriched.append(model)
            continue
        try:
            shown = fetch("/api/show", {"model": model.identifier})
        except (RuntimeError, OSError, ValueError):
            enriched.append(model)
            continue
        info = shown.get("model_info")
        if not isinstance(info, dict):
            enriched.append(model)
            continue
        maximum, kv, fields = _architecture(info)
        enriched.append(
            replace(
                model, max_context=maximum, kv_bytes_per_token=kv, architecture=fields
            )
        )
        successes += 1
    return enriched, successes


def collect_local_report(
    configured_model: str | None,
    *,
    context: int = 8192,
    enrich: bool = False,
    fetch: Callable[[str, dict[str, object] | None], Mapping[str, object]] = _json,
    hardware_probe: Callable[[], HardwareProfile] = profile_hardware,
) -> dict[str, object]:
    hardware = hardware_probe()
    errors: list[str] = []
    try:
        version_response = fetch("/api/version", None)
        version = _text(version_response.get("version"), 64)
    except (RuntimeError, OSError, ValueError):
        version = None
        errors.append("Ollama version unavailable")
    try:
        inventory = fetch("/api/tags", None)
        raw_models = inventory.get("models")
        if not isinstance(raw_models, list):
            raise ValueError("invalid model inventory")
        models = [
            parsed
            for row in raw_models[: MAX_MODELS + 1]
            if (parsed := _model_from_tag(row)) is not None
        ]
        inventory_state = "Fresh"
    except (RuntimeError, OSError, ValueError):
        models = []
        inventory_state = "Unknown"
        errors.append("Ollama model inventory unavailable")
    try:
        loaded_response = fetch("/api/ps", None)
        loaded_rows = loaded_response.get("models")
        if not isinstance(loaded_rows, list):
            raise ValueError("invalid loaded model list")
        loaded_map: dict[str, str] = {}
        for row in loaded_rows[:MAX_MODELS]:
            if not isinstance(row, dict):
                raise ValueError("invalid loaded model")
            name, digest = row.get("name"), _digest(row.get("digest"))
            if not isinstance(name, str) or not valid_model_tag(name) or digest is None:
                raise ValueError("invalid loaded identity")
            loaded_map[name] = digest
        loaded: dict[str, str] | None = loaded_map
        loaded_state = "Fresh"
    except (RuntimeError, OSError, ValueError):
        loaded = None
        loaded_state = "Unknown"
        errors.append("Ollama loaded-model state unavailable")
    enriched = 0
    if enrich and models:
        models, enriched = _enrich(models, configured_model, fetch)
    report = build_report(hardware, models, configured_model, loaded, context=context)
    try:
        tiers = load_tiers()
        vram = max(
            (
                gpu.dedicated_vram_bytes
                for gpu in hardware.gpus
                if type(gpu.dedicated_vram_bytes) is int
                and 0 < gpu.dedicated_vram_bytes <= MAX_BYTES
            ),
            default=0,
        )
        # Match the installer's get_vram_gb one-decimal contract at tier edges.
        tier = classify_tier(round(vram / (1024**3), 1), tiers=tiers)
        pick = tier["pick"]
        source, planned_context = pick["source"], pick["ctx"]
        if (
            not isinstance(source, str)
            or not valid_model_tag(source)
            or type(planned_context) is not int
            or not 1024 <= planned_context <= 32768
        ):
            raise ValueError("invalid setup tier choice")
        report["setup_plan"] = {
            "model": source,
            "context": planned_context,
            "tier": str(tier["id"])[:16],
            "source": "installer tier policy",
            "basis": "selected from detected NVIDIA VRAM; setup confirms hardware",
        }
    except (OSError, KeyError, TypeError, ValueError):
        report["setup_plan"] = None
    report["observed_at_utc"] = datetime.now(UTC).isoformat()
    report["runtime"] = {
        "name": "ollama",
        "version": version,
        "inventory_state": inventory_state,
        "loaded_state": loaded_state,
        "enriched_models": enriched,
        "errors": errors,
    }
    return report
