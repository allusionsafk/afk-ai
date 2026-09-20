"""Local Ollama profiling and bounded streaming measurement adapter."""

from __future__ import annotations

import json
import os
import platform
import re
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from localai.model_scout import get_total_physical_memory_bytes
from localai.ops import run_command
from localai.optimizer import (
    GIB,
    GPU,
    NUM_PREDICT,
    PROMPT,
    RUNS,
    WARMUPS,
    HardwareProfile,
    Measurement,
    ModelProfile,
    Run,
    RuntimeProfile,
    summarize,
)
from localai.paths import REPO_ROOT
from localai.system_info import _memory_status

BASE = "http://127.0.0.1:11434"


def _number(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = int(value)
        return result if result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _json(
    path: str, *, body: dict[str, object] | None = None, timeout: float = 5
) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Ollama {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"Ollama {path}: invalid response")
    return value


def profile_hardware() -> HardwareProfile:
    memory = _memory_status() or {}
    total = get_total_physical_memory_bytes()
    available_gb = memory.get("ramTotalGb", 0) - memory.get("ramUsedGb", 0)
    available = int(available_gb * GIB) if available_gb > 0 else None
    result = run_command(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free",
            "--format=csv,noheader,nounits",
        ],
        cwd=REPO_ROOT,
        timeout_sec=5,
    )
    gpus: list[GPU] = []
    if result.code == 0:
        for line in result.text.splitlines():
            parts = [part.strip() for part in line.rsplit(",", 2)]
            if len(parts) == 3 and parts[0]:
                total_mib = _number(parts[1])
                free_mib = _number(parts[2])
                gpus.append(
                    GPU(
                        parts[0],
                        total_mib * 1024**2 if total_mib else None,
                        free_mib * 1024**2 if free_mib else None,
                    )
                )
    cpu = os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or None
    return HardwareProfile(
        cpu=cpu,
        logical_cores=os.cpu_count(),
        ram_total_bytes=total,
        ram_available_bytes=available,
        gpus=tuple(gpus),
        platform=f"{platform.system()} {platform.release()} {platform.machine()}",
    )


def _parameter_count(raw: object) -> int | None:
    if not isinstance(raw, str):
        return None
    match = re.fullmatch(r"\s*([\d.]+)\s*([MB])\s*", raw, re.I)
    if not match:
        return None
    scale = 1e9 if match.group(2).upper() == "B" else 1e6
    return _number(float(match.group(1)) * scale)


def _architecture(
    info: Mapping[str, object],
) -> tuple[int | None, int | None, dict[str, int]]:
    arch = str(info.get("general.architecture") or "")
    ctx = _number(info.get(f"{arch}.context_length")) if arch else None
    keys = (
        "block_count",
        "attention.head_count_kv",
        "attention.key_length",
        "attention.value_length",
        "embedding_length",
        "attention.head_count",
    )
    fields = {
        key: value
        for key in keys
        if (value := _number(info.get(f"{arch}.{key}"))) is not None
    }
    layers = fields.get("block_count")
    heads = fields.get("attention.head_count_kv")
    key_dim = fields.get("attention.key_length")
    value_dim = fields.get("attention.value_length")
    if (
        not key_dim
        and fields.get("embedding_length")
        and fields.get("attention.head_count")
    ):
        embedding = fields["embedding_length"]
        full_heads = fields["attention.head_count"]
        if embedding % full_heads == 0:
            key_dim = embedding // full_heads
    if not value_dim:
        value_dim = key_dim
    kv = (
        2 * layers * heads * (key_dim + value_dim)
        if layers is not None
        and heads is not None
        and key_dim is not None
        and value_dim is not None
        else None
    )
    return ctx, kv, fields


def profile_ollama(model_tag: str) -> tuple[ModelProfile, RuntimeProfile]:
    version = _json("/api/version")
    listing = _json("/api/tags")
    models = listing.get("models")
    if not isinstance(models, list):
        raise RuntimeError("Ollama /api/tags: no model inventory")
    row = next(
        (
            item
            for item in models
            if isinstance(item, dict) and item.get("name") == model_tag
        ),
        None,
    )
    if row is None:
        raise ValueError(f"installed Ollama model not found: {model_tag}")
    shown = _json("/api/show", body={"model": model_tag})
    info = shown.get("model_info")
    info = info if isinstance(info, dict) else {}
    details = shown.get("details")
    details = details if isinstance(details, dict) else {}
    ctx, kv, fields = _architecture(info)
    digest = row.get("digest")
    version_name = version.get("version")
    model = ModelProfile(
        identifier=model_tag,
        digest=digest if isinstance(digest, str) and digest else None,
        family=details.get("family")
        if isinstance(details.get("family"), str)
        else None,
        parameter_count=_parameter_count(details.get("parameter_size")),
        quantization=(
            details.get("quantization_level")
            if isinstance(details.get("quantization_level"), str)
            else None
        ),
        artifact_bytes=_number(row.get("size")),
        max_context=ctx or _number(details.get("context_length")),
        kv_bytes_per_token=kv,
        architecture=fields,
    )
    runtime = RuntimeProfile(
        name="ollama",
        version=version_name
        if isinstance(version_name, str) and version_name
        else None,
        backend=None,  # /api/ps residency is not a reliable backend label.
        options={
            "temperature": 0,
            "seed": 1,
            "num_predict": NUM_PREDICT,
            "stream": True,
            # Client-visible settings tighten cache identity without claiming
            # these are the server process's effective settings.
            "client_env_kv_cache_type": os.environ.get(
                "OLLAMA_KV_CACHE_TYPE", "unknown"
            ),
            "client_env_num_parallel": os.environ.get("OLLAMA_NUM_PARALLEL", "unknown"),
            "client_env_flash_attention": os.environ.get(
                "OLLAMA_FLASH_ATTENTION", "unknown"
            ),
        },
    )
    return model, runtime


def _one_run(model: str, context: int, *, timeout_sec: float) -> Run:
    body = {
        "model": model,
        "prompt": PROMPT,
        "stream": True,
        "options": {
            "num_ctx": context,
            "num_predict": NUM_PREDICT,
            "temperature": 0,
            "seed": 1,
        },
    }
    request = Request(
        BASE + "/api/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    first: float | None = None
    final: dict[str, Any] | None = None
    try:
        with urlopen(request, timeout=timeout_sec) as response:
            for line in response:
                elapsed = time.perf_counter() - start
                if elapsed > timeout_sec:
                    raise TimeoutError(f"benchmark exceeded {timeout_sec:g}s")
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("invalid stream event")
                if event.get("error"):
                    raise RuntimeError(str(event["error"]))
                if first is None and (event.get("response") or event.get("thinking")):
                    first = elapsed
                if event.get("done") is True:
                    final = event
                    break
    except (
        HTTPError,
        URLError,
        OSError,
        TimeoutError,
        ValueError,
        RuntimeError,
    ) as error:
        return Run(
            False,
            time.perf_counter() - start,
            first,
            None,
            None,
            None,
            None,
            str(error),
        )
    elapsed = time.perf_counter() - start
    if final is None:
        return Run(False, elapsed, first, None, None, None, None, "missing final event")
    prompt_tokens = _number(final.get("prompt_eval_count"))
    decode_tokens = _number(final.get("eval_count"))
    prompt_ns = _number(final.get("prompt_eval_duration"))
    decode_ns = _number(final.get("eval_duration"))
    if first is None or not decode_tokens or not decode_ns:
        return Run(
            False,
            elapsed,
            first,
            prompt_tokens,
            None,
            decode_tokens,
            None,
            "missing token timing",
        )
    prompt_rate = (
        prompt_tokens * 1e9 / prompt_ns if prompt_tokens and prompt_ns else None
    )
    decode_rate = decode_tokens * 1e9 / decode_ns
    return Run(
        True, elapsed, first, prompt_tokens, prompt_rate, decode_tokens, decode_rate
    )


def benchmark_ollama(
    model: str, context: int, *, timeout_sec: float = 90
) -> Measurement:
    for _ in range(WARMUPS):
        warmup = _one_run(model, context, timeout_sec=timeout_sec)
        if not warmup.success:
            return summarize(context, (warmup,))
    runs: list[Run] = []
    for _ in range(RUNS):
        result = _one_run(model, context, timeout_sec=timeout_sec)
        runs.append(result)
        if not result.success:
            break
    measured = summarize(context, tuple(runs))
    if not measured.successful:
        return measured
    try:
        loaded = _json("/api/ps")
        rows = loaded.get("models")
        row = (
            next(
                (
                    item
                    for item in rows
                    if isinstance(item, dict) and item.get("name") == model
                ),
                None,
            )
            if isinstance(rows, list)
            else None
        )
        if row is None:
            return replace(
                measured, successful=False, error="model absent from /api/ps"
            )
        effective = _number(row.get("context_length"))
        if effective != context:
            return replace(
                measured,
                successful=False,
                effective_context=effective,
                error="effective context did not match requested context",
            )
        return replace(
            measured,
            effective_context=effective,
            resident_vram_bytes=_number(row.get("size_vram")),
            resident_total_bytes=_number(row.get("size")),
        )
    except RuntimeError as error:
        return replace(measured, successful=False, error=str(error))
