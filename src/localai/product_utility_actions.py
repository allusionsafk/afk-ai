"""Explicit, evidence checked changes to the installed chat model and context."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from typing import Any

from localai.optimizer import (
    HardwareProfile,
    MeasurementCache,
    ModelProfile,
    RuntimeProfile,
    context_ladder,
    optimize,
)
from localai.optimizer_ollama import profile_hardware, profile_ollama
from localai.product_config import (
    ProductLayout,
    UtilitySelection,
    configured_model,
    configured_source_model,
    ensure_runtime_config,
    read_utility_selection,
    valid_model_tag,
)
from localai.product_runtime import create_context_tag, owned_exec
from localai.product_utility_ollama import _json

_DIGEST = re.compile(r"[a-fA-F0-9]{64}\Z")
MAX_INVENTORY_ROWS = 128


def _inventory(
    fetch: Callable[[str, dict[str, object] | None], Mapping[str, Any]],
) -> dict[str, str]:
    response = fetch("/api/tags", None)
    rows = response.get("models")
    if not isinstance(rows, list) or len(rows) > MAX_INVENTORY_ROWS:
        raise RuntimeError("Ollama model inventory is unavailable or too large")
    found: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        tag, digest = row.get("name"), row.get("digest")
        if not isinstance(tag, str) or not valid_model_tag(tag):
            continue
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            continue
        if tag in found:
            raise RuntimeError("Ollama inventory contains duplicate model names")
        found[tag] = digest.lower()
    return found


def _installed_digest(
    tag: str, fetch: Callable[[str, dict[str, object] | None], Mapping[str, Any]]
) -> str:
    if not valid_model_tag(tag):
        raise ValueError("Invalid model name")
    digest = _inventory(fetch).get(tag)
    if digest is None:
        raise ValueError(f"Model is not installed: {tag}")
    return digest


def seed_owned_chat(layout: ProductLayout, model: str, context: int | None) -> bool:
    from localai.webui_seed import collect_webui_seed_report

    code, _lines = collect_webui_seed_report(
        model=model if context is not None else None,
        num_ctx=context or 4096,
        default_model=model,
        exec_fn=owned_exec(layout),
    )
    return code == 0


def use_installed_model(
    layout: ProductLayout,
    model: str,
    *,
    fetch: Callable[[str, dict[str, object] | None], Mapping[str, Any]] = _json,
    seed: Callable[[str, int | None], bool] | None = None,
) -> dict[str, str | int | None]:
    digest = _installed_digest(model, fetch)
    if not (seed or (lambda tag, ctx: seed_owned_chat(layout, tag, ctx)))(model, None):
        raise RuntimeError("Could not update the AFK-owned chat service")
    if _installed_digest(model, fetch) != digest:
        raise RuntimeError("Model identity changed during selection")
    ensure_runtime_config(
        layout,
        model=model,
        utility=UtilitySelection(model, digest, None, None, None),
    )
    return {"configured_model": model, **read_utility_selection(layout)}


def _context_tag(source: str, digest: str, context: int) -> str:
    identity = hashlib.sha256(f"{source}\0{digest}\0{context}".encode()).hexdigest()[
        :20
    ]
    return f"afk-context/{identity}:{context}"


def apply_context(
    layout: ProductLayout,
    source: str,
    context: int,
    *,
    override: bool,
    fetch: Callable[[str, dict[str, object] | None], Mapping[str, Any]] = _json,
    profile: Callable[[str], tuple[ModelProfile, RuntimeProfile]] = profile_ollama,
    hardware: Callable[[], HardwareProfile] = profile_hardware,
    create: Callable[[str, str, int], tuple[bool, str]] = create_context_tag,
    seed: Callable[[str, int | None], bool] | None = None,
) -> dict[str, str | int | None]:
    if (
        type(context) is not int
        or not 1024 <= context <= 32768
        or context & (context - 1)
    ):
        raise ValueError("Context must be a supported power of two between 1K and 32K")
    if not valid_model_tag(source) or configured_source_model(layout) != source:
        raise ValueError("Select this installed model before applying its context")
    original_digest = _installed_digest(source, fetch)
    model, runtime = profile(source)
    if model.identifier != source or model.digest != original_digest:
        raise RuntimeError("Model identity changed during profiling")
    if context not in context_ladder(model, 32768):
        raise ValueError("Context is outside this model's supported range")
    recommendation = optimize(
        hardware(),
        model,
        runtime,
        lambda *_args: (_ for _ in ()).throw(RuntimeError("Unexpected benchmark")),
        MeasurementCache(layout.state_root / "optimizer-measurements-v1.json"),
        cap=32768,
        measure=False,
    )
    estimated = next(
        (item for item in recommendation.estimates if item.context == context), None
    )
    if estimated is None or not estimated.safe:
        raise ValueError("Context exceeds conservative memory headroom")
    if not override and recommendation.recommended_context != context:
        raise ValueError(
            "Only an evidenced AFK recommendation can be applied without override"
        )
    tag = _context_tag(source, original_digest, context)
    existing = _inventory(fetch).get(tag)
    selection = read_utility_selection(layout)
    reuse = (
        existing is not None
        and configured_model(layout) == tag
        and selection["source_model"] == source
        and selection["source_digest"] == original_digest
        and selection["selected_context"] == context
    )
    if existing is not None and not reuse:
        raise RuntimeError("AFK context tag already exists with unknown identity")
    if not reuse:
        created, _detail = create(source, tag, context)
        if not created:
            raise RuntimeError("Could not create the selected context model")
    if _installed_digest(source, fetch) != original_digest:
        raise RuntimeError("Model identity changed during context creation")
    if not (seed or (lambda target, ctx: seed_owned_chat(layout, target, ctx)))(
        tag, context
    ):
        raise RuntimeError("Could not update the AFK-owned chat service")
    ensure_runtime_config(
        layout,
        model=tag,
        utility=UtilitySelection(
            source,
            original_digest,
            recommendation.recommended_context,
            context,
            context if override else None,
        ),
    )
    return {"configured_model": tag, **read_utility_selection(layout)}
