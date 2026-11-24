"""Validated, atomic snapshots of Scout estimates, never fresh runtime evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TypeGuard

from localai.scout_categories import CATEGORIES
from localai.scout_evidence import MAX_PARALLEL, QuantArtefact

SCHEMA = 2
MAX_SNAPSHOT_BYTES = 512 * 1024


def _number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value >= 0
    )


def _fingerprint(context: dict[str, object]) -> str:
    # Detect accidental inconsistencies. This is not an authenticity signature.
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _valid_candidate(value: object, context: dict[str, object]) -> bool:
    if not isinstance(value, dict):
        return False
    repo = value.get("id")
    curated = value.get("curated")
    if type(curated) is not bool or not isinstance(repo, str):
        return False
    if curated:
        if repo not in {
            f"curated/{tag}" for category in CATEGORIES for tag in category.curated
        }:
            return False
    elif not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", repo
    ):
        return False
    if (
        value.get("fitStage") != "final"
        or value.get("runtimeStatus") != "unverified"
        or value.get("weightSource") not in {"parameter-estimate", "repository-bytes"}
        or value.get("kvSource") not in {
            "parameter-f16-estimate", "config-f16-estimate"
        }
        or not _number(value.get("score"))
        or not isinstance(value.get("why"), str)
        or not value["why"]
        or not isinstance(value.get("name"), str)
        or not value["name"]
    ):
        return False
    quant = value.get("quant")
    if quant is not None:
        try:
            QuantArtefact(quant, None)
        except (ValueError, TypeError):
            return False
    if value["weightSource"] == "repository-bytes" and quant is None:
        return False
    status = value.get("metadataStatus")
    if status not in {"available", "unavailable", "not-checked", "curated"}:
        return False
    if curated != (status == "curated"):
        return False
    if status in {"unavailable", "not-checked", "curated"} and (
        quant is not None or value["weightSource"] != "parameter-estimate"
        or value["kvSource"] != "parameter-f16-estimate"
    ):
        return False
    if status == "available" and quant is None and (
        value["kvSource"] != "config-f16-estimate"
    ):
        return False
    weights, kv = value.get("weightGb"), value.get("kvGb")
    if value.get("verdict") == "Unknown":
        return (
            curated and weights is None and kv is None
            and value.get("sizeGb") is None
            and quant is None and value["weightSource"] == "parameter-estimate"
            and value["kvSource"] == "parameter-f16-estimate"
        )
    if not (_number(weights) and weights > 0 and _number(kv)):
        return False
    ram, vram = context.get("ramGb"), context.get("vramGb")
    if not (_number(ram) and _number(vram)):
        return False
    demand = weights + kv
    verdict = (
        "TooBig" if weights > ram - 5 else
        "Good" if demand <= vram - 1.5 else
        "Tight" if demand <= ram - 5 else "Poor"
    )
    return (
        verdict in {"Good", "Tight"} and value.get("verdict") == verdict
        and _number(value.get("sizeGb")) and value["sizeGb"] == round(demand, 1)
        and (not curated or (
            quant is None and value["weightSource"] == "parameter-estimate"
            and value["kvSource"] == "parameter-f16-estimate"
        ))
    )


def _valid_snapshot(value: object) -> TypeGuard[dict[str, object]]:
    if (
        not isinstance(value, dict) or type(value.get("schema")) is not int
        or value.get("schema") != SCHEMA
    ):
        return False
    if (
        value.get("evidenceStatus") != "cached-estimate"
        or value.get("scope") != "bounded-shortlist"
        or not isinstance(value.get("generated"), str)
    ):
        return False
    try:
        datetime.strptime(value["generated"], "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    context = value.get("fitContext")
    if not isinstance(context, dict) or set(context) != {
        "ramGb", "vramGb", "parallel", "kvDtype", "categoryContexts"
    }:
        return False
    if (
        not _number(context["ramGb"]) or not _number(context["vramGb"])
        or type(context["parallel"]) is not int
        or not 1 <= context["parallel"] <= MAX_PARALLEL
        or context["kvDtype"] != "f16"
        or context["categoryContexts"] != {
            category.id: category.target_ctx for category in CATEGORIES
        }
        or value.get("fitFingerprint") != _fingerprint(context)
    ):
        return False
    groups = value.get("groups")
    if not isinstance(groups, dict) or set(groups) != {c.id for c in CATEGORIES}:
        return False
    for key, group in groups.items():
        if not isinstance(group, dict) or group.get("category") != key:
            return False
        runners, top, dropped = (
            group.get("runnersUp"), group.get("top"), group.get("dropped")
        )
        if (
            not isinstance(runners, list) or len(runners) > 2
            or (top is None and runners)
            or not isinstance(dropped, list) or len(dropped) > 6
            or not isinstance(group.get("why"), str)
        ):
            return False
        picks = ([top] if top is not None else []) + runners
        if not all(_valid_candidate(pick, context) for pick in picks):
            return False
        if len({pick["id"] for pick in picks}) != len(picks):
            return False
        if [pick["score"] for pick in picks] != sorted(
            (pick["score"] for pick in picks), reverse=True
        ):
            return False
        if any(
            not isinstance(row, dict)
            or not all(
                isinstance(row.get(k), str) and row[k] for k in ("name", "reason")
            )
            for row in dropped
        ):
            return False
    return True


def write_snapshot(
    path: Path, groups: dict[str, object], *, now: datetime,
    ram_gb: float, vram_gb: float, parallel: int,
) -> None:
    context: dict[str, object] = {
        "ramGb": ram_gb, "vramGb": vram_gb, "parallel": parallel,
        "kvDtype": "f16",
        "categoryContexts": {c.id: c.target_ctx for c in CATEGORIES},
    }
    payload: dict[str, object] = {
        "schema": SCHEMA, "generated": now.strftime("%Y-%m-%d %H:%M"),
        "evidenceStatus": "cached-estimate", "scope": "bounded-shortlist",
        "fitContext": context, "fitFingerprint": _fingerprint(context),
        "groups": groups,
    }
    if not _valid_snapshot(payload):
        raise ValueError("invalid final Scout snapshot")
    encoded = json.dumps(payload, indent=2, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ValueError("final Scout snapshot too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = output.name
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def read_snapshot(path: Path) -> dict[str, object] | None:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate snapshot key")
            result[key] = value
        return result

    try:
        with path.open("rb") as source:
            raw = source.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            return None
        payload = json.loads(raw, object_pairs_hook=unique_object)
        # Even presentation fields must be safe for the dashboard's strict
        # JSON/UTF-8 response: reject nonfinite numbers and lone surrogates.
        json.dumps(payload, allow_nan=False, ensure_ascii=False).encode("utf-8")
        return payload if _valid_snapshot(payload) else None
    except (OSError, ValueError, RecursionError, TypeError, OverflowError):
        return None
