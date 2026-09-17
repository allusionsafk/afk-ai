"""Preload the default Ollama model, ported from ai-warm.ps1."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from localai.ops import run_command
from localai.paths import REPO_ROOT, compose_value, repo_path

DEFAULT_MODEL = "qwen2.5-grounded"


def warm_state_path() -> Path:
    """Machine-local state file for the operator's warm-model choice."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".localai"
    return root / "localai" / "warm-model.json"


def _read_warm_state() -> dict[str, Any]:
    try:
        data = json.loads(warm_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_warm_state(state: dict[str, Any]) -> None:
    path = warm_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def read_warm_model_override() -> str | None:
    """The operator's persisted warm-model choice; None means compose default."""
    model = _read_warm_state().get("model")
    return str(model) if model else None


def write_warm_model_override(model: str | None) -> None:
    state = _read_warm_state()
    state["model"] = model or None
    _write_warm_state(state)


def read_known_models() -> list[str]:
    """Last model list seen from Ollama, for when the engine is down."""
    known = _read_warm_state().get("known")
    if not isinstance(known, list):
        return []
    return [str(name) for name in known if name]


def write_known_models(models: list[str]) -> None:
    state = _read_warm_state()
    state["known"] = list(models)
    _write_warm_state(state)


def collect_warm_report(
    *,
    require_ollama: bool = False,
    model: str | None = None,
    keep_alive: str = "30m",
    num_ctx: int = 0,
    unload_others: bool = False,
    skip_if_any_loaded: bool = False,
    prefer_target: bool = False,
    server_attempts: int = 40,
    server_sleep_sec: float = 5,
    tag_timeout_sec: int = 3,
    chat_timeout_sec: int = 420,
) -> tuple[int, list[str]]:
    """Warm the selected model using the same API flow as ai-warm.ps1.

    ``prefer_target`` changes the ``skip_if_any_loaded`` contract for an
    explicit model choice: only the target itself being loaded skips the
    warm-up, so a different loaded model is replaced rather than preserved.
    """
    lines: list[str] = []
    target_model = model or read_default_model()
    target_num_ctx = resolve_num_ctx(target_model, num_ctx)
    ollama = ollama_path()

    if skip_if_any_loaded and ollama.exists():
        try:
            loaded_names = get_loaded_model_names(name_fallback=True)
            target_plain = strip_latest(target_model)
            target_loaded = any(
                strip_latest(name) == target_plain for name in loaded_names
            )
            if loaded_names and (target_loaded or not prefer_target):
                lines.append(
                    "[AI-Warm] preserving loaded model(s): "
                    + ", ".join(loaded_names)
                )
                return 0, lines
            if loaded_names:
                lines.append(
                    f"[AI-Warm] switching warm model to {target_model} "
                    "(explicit choice overrides loaded model)"
                )
        except (OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
            lines.append(
                "WARNING: [AI-Warm] could not check loaded models before warmup: "
                f"{exception_message(exc)}"
            )

    if unload_others and ollama.exists():
        try:
            loaded_names = get_loaded_model_names(name_fallback=False)
            target_plain = strip_latest(target_model)
            for loaded_name in loaded_names:
                if strip_latest(loaded_name) == target_plain:
                    continue
                lines.append(f"[AI-Warm] unloading stale model {loaded_name}")
                run_command([str(ollama), "stop", loaded_name], cwd=REPO_ROOT)
        except (OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
            lines.append(
                "WARNING: [AI-Warm] could not unload stale models: "
                f"{exception_message(exc)}"
            )

    if not wait_for_ollama(
        attempts=server_attempts,
        sleep_sec=server_sleep_sec,
        timeout_sec=tag_timeout_sec,
    ):
        lines.append(
            "WARNING: [AI-Warm] Ollama server is not reachable; skipping preload."
        )
        return (1 if require_ollama else 0), lines

    body = {
        "model": target_model,
        "messages": [{"role": "user", "content": "warmup"}],
        "stream": False,
        "keep_alive": keep_alive,
        "options": {
            "num_ctx": target_num_ctx,
            "num_predict": 1,
            "temperature": 0,
        },
    }
    try:
        request_json(
            "/api/chat",
            timeout_sec=chat_timeout_sec,
            method="POST",
            payload=body,
        )
    except (OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
        lines.append(
            f"WARNING: [AI-Warm] failed to preload {target_model}: "
            f"{exception_message(exc)}"
        )
        return 1, lines

    lines.append(
        f"[AI-Warm] preloaded {target_model} "
        f"(keep_alive={keep_alive}, num_ctx={target_num_ctx})"
    )
    return 0, lines


def read_default_model(compose_text: str | None = None) -> str:
    if compose_text is None:
        try:
            compose_text = repo_path("docker-compose.yml").read_text(encoding="utf-8")
        except OSError:
            return DEFAULT_MODEL
    match = re.search(r"DEFAULT_MODELS=([^\s]+)", compose_text)
    if match is None:
        return DEFAULT_MODEL
    return compose_value(match.group(1)) or DEFAULT_MODEL


def replace_default_model(compose_text: str, model: str) -> str:
    """Return ``compose_text`` with the ``DEFAULT_MODELS=`` value set to ``model``.

    The installer picks a per-tier tag, but the repo compose hardcodes the tier-A
    daily driver; rewriting this one literal is what makes every reader that regexes
    ``DEFAULT_MODELS=`` (warm.read_default_model, health, model_scout.baseline_model)
    plus Open WebUI's own env see the model that was actually pulled (finding 1).
    """
    new_text, count = re.subn(
        r"DEFAULT_MODELS=\S+",
        lambda _m: f"DEFAULT_MODELS={model}",
        compose_text,
    )
    if count == 0:
        raise ValueError("DEFAULT_MODELS= not found in docker-compose.yml")
    return new_text


def collect_set_default_model_report(
    model: str, *, dry_run: bool = False
) -> tuple[int, list[str]]:
    """Rewrite docker-compose.yml's DEFAULT_MODELS to ``model`` (installer step)."""
    path = repo_path("docker-compose.yml")
    try:
        compose_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return 1, [f"ERROR: cannot read {path}: {exception_message(exc)}"]
    try:
        new_text = replace_default_model(compose_text, model)
    except ValueError as exc:
        return 1, [f"ERROR: {exc}"]
    if dry_run:
        return 0, [f"[set-default-model] would set DEFAULT_MODELS={model} in {path}"]
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return 1, [f"ERROR: cannot write {path}: {exception_message(exc)}"]
    return 0, [f"[set-default-model] DEFAULT_MODELS={model}"]


def resolve_num_ctx(model: str, num_ctx: int) -> int:
    if num_ctx > 0:
        return num_ctx
    if "qwen3.6" in model:
        return 4096
    # Models tagged "-32k" (e.g. qwen3.5:9b-32k) bake num_ctx 32768 into their
    # Modelfile; warm at the same size so the first real chat does not force a
    # full reload for a context-size mismatch.
    if "32k" in model:
        return 32768
    if "16k" in model:
        return 16384
    return 8192


def ollama_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    return Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"


def get_loaded_model_names(*, name_fallback: bool) -> list[str]:
    payload = request_json("/api/ps", timeout_sec=3)
    models = payload.get("models", []) if isinstance(payload, dict) else []
    names: list[str] = []
    for row in models:
        if not isinstance(row, dict):
            continue
        value = row.get("name")
        if not value and name_fallback:
            value = row.get("model")
        if value:
            names.append(str(value))
    return names


def wait_for_ollama(*, attempts: int, sleep_sec: float, timeout_sec: int) -> bool:
    for attempt in range(max(0, attempts)):
        try:
            request_json("/api/tags", timeout_sec=timeout_sec)
            return True
        except (OSError, TimeoutError, URLError, json.JSONDecodeError):
            if attempt < attempts - 1 and sleep_sec > 0:
                time.sleep(sleep_sec)
    return False


def request_json(
    path: str,
    *,
    timeout_sec: int,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"http://localhost:11434{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    with urlopen(request, timeout=timeout_sec) as response:
        raw = response.read().decode("utf-8")
    if not raw:
        return {}
    return json.loads(raw)


def strip_latest(model: str) -> str:
    return re.sub(r":latest$", "", model)


def exception_message(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        return (
            "Response status code does not indicate success: "
            f"{exc.code} ({exc.reason})."
        )
    if isinstance(exc, URLError):
        return str(exc.reason)
    return str(exc)
