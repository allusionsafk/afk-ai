"""Performance/context guard ported from ai-perf.ps1."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from localai.ops import run_command
from localai.paths import REPO_ROOT, repo_path
from localai.power import format_number

# WebBrain (the supported browser agent) connects directly to Ollama once this
# extension origin is allowlisted in OLLAMA_ORIGINS.
WEBBRAIN_ORIGIN = "chrome-extension://ljhijonmfahplgbbacgcfnaihbjljhhb"
AddLine = Callable[[str, str, str], None]


@dataclass
class Counters:
    ok: int = 0
    warn: int = 0
    fail: int = 0

    def add(self, status: str) -> None:
        if status == "OK":
            self.ok += 1
        elif status == "WARN":
            self.warn += 1
        elif status == "FAIL":
            self.fail += 1


@dataclass(frozen=True)
class PersistedEnv:
    name: str
    value: str | None
    process: str | None
    user: str | None
    machine: str | None


def collect_perf_report(
    *,
    max_daily_context: int = 8192,
    max_think_light_context: int = 4096,
    strict: bool = False,
    now: datetime | None = None,
) -> tuple[int, list[str]]:
    """Collect the same read-only performance checks as ai-perf.ps1."""
    counters = Counters()
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"==== localai performance guard ====  {stamp}"]

    def add_line(status: str, name: str, detail: str) -> None:
        counters.add(status)
        lines.append(format_status_line(status, name, detail))

    ollama = ollama_path()
    if ollama.exists():
        add_line("OK", "Ollama binary", str(ollama))
    else:
        add_line("FAIL", "Ollama binary", f"missing: {ollama}")

    # Compose's DEFAULT_MODELS is the deliberate source of truth; any set value
    # is a valid configuration.
    default_model = read_default_model()
    if default_model:
        add_line("OK", "Daily default", default_model)
    else:
        add_line(
            "WARN",
            "Daily default",
            "could not read DEFAULT_MODELS from docker-compose.yml",
        )

    task_model = read_task_model()
    if task_model == "":
        add_line(
            "OK",
            "Task model",
            "blank; Open WebUI tasks use the current chat model",
        )
    elif task_model:
        add_line(
            "WARN",
            "Task model",
            f"{task_model} can evict the active chat model after background tasks",
        )
    else:
        add_line(
            "WARN",
            "Task model",
            "could not read TASK_MODEL from docker-compose.yml",
        )

    test_env_equals(add_line, "OLLAMA_FLASH_ATTENTION", "1")
    test_env_equals(add_line, "OLLAMA_KV_CACHE_TYPE", "q8_0")
    test_env_equals(add_line, "OLLAMA_KEEP_ALIVE", "30m")
    test_max_loaded_models(add_line)

    compose_text = read_compose_text()
    test_open_webui_request_params(add_line, compose_text)
    test_open_webui_memories(add_line, compose_text)
    test_open_webui_think_light_rows(add_line)
    test_context_length(add_line, max_daily_context)
    test_webbrain_origin(add_line)
    test_ollama_api(add_line, max_think_light_context)
    test_gpu_headroom(add_line)

    lines.append("")
    lines.append(
        f"Summary: {counters.ok} OK, {counters.warn} WARN, {counters.fail} FAIL"
    )
    if counters.fail > 0:
        return 1, lines
    if strict and counters.warn > 0:
        return 1, lines
    return 0, lines


def format_status_line(status: str, name: str, detail: str) -> str:
    return f"[{status}] {name:<24} {detail}"


def ollama_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    return Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"


def read_compose_text() -> str:
    path = repo_path("docker-compose.yml")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_default_model(compose_text: str | None = None) -> str:
    text = compose_text if compose_text is not None else read_compose_text()
    match = re.search(r"DEFAULT_MODELS=([^\s]+)", text)
    if match:
        return match.group(1).strip()
    return ""


def read_task_model(compose_text: str | None = None) -> str | None:
    text = compose_text if compose_text is not None else read_compose_text()
    match = re.search(r"TASK_MODEL=([^\r\n]*)", text)
    if match:
        return match.group(1).strip()
    return None


def read_default_model_params(compose_text: str) -> dict[str, object] | None:
    match = re.search(r"DEFAULT_MODEL_PARAMS=(\{[^\r\n]+\})", compose_text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def get_persisted_env(name: str) -> PersistedEnv:
    process = os.environ.get(name)
    user = read_registry_env("User", name)
    machine = read_registry_env("Machine", name)
    value = process or user or machine
    return PersistedEnv(name, value, process, user, machine)


def read_registry_env(scope: str, name: str) -> str | None:
    try:
        import winreg
    except ImportError:
        return None

    if scope == "User":
        root = winreg.HKEY_CURRENT_USER
        subkey = "Environment"
    elif scope == "Machine":
        root = winreg.HKEY_LOCAL_MACHINE
        subkey = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    else:
        return None

    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _value_type = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return str(value)


def test_env_equals(add_line: AddLine, name: str, expected: str) -> None:
    row = get_persisted_env(name)
    if str(row.value) == expected:
        add_line("OK", name, expected)
    elif row.value:
        add_line("WARN", name, f"expected {expected}; current {row.value}")
    else:
        add_line("WARN", name, f"missing; expected {expected}")


def test_open_webui_request_params(add_line: AddLine, compose_text: str) -> None:
    params = read_default_model_params(compose_text)
    if params is None:
        add_line("WARN", "Open WebUI params", "DEFAULT_MODEL_PARAMS missing or invalid")
        return

    stream_ok = bool(params.get("stream_response"))
    keep = str(params.get("keep_alive") or "")
    keep_ok = bool(keep) and keep not in {"0", "0s", "0m"}
    global_think_off = params.get("think") is False

    if stream_ok and keep_ok and not global_think_off:
        add_line("OK", "Open WebUI params", f"stream_response=true, keep_alive={keep}")
        return

    missing = []
    if not stream_ok:
        missing.append("stream_response=true")
    if not keep_ok:
        missing.append("request keep_alive=30m")
    if global_think_off:
        missing.append("remove global think=false")
    add_line("WARN", "Open WebUI params", f"missing: {', '.join(missing)}")


def test_open_webui_memories(add_line: AddLine, compose_text: str) -> None:
    if re.search(r"(?m)^\s*-\s*ENABLE_MEMORIES=True\s*$", compose_text):
        add_line(
            "OK",
            "Open WebUI memories",
            "enabled; think-light uses per-model think=false",
        )
    else:
        add_line(
            "WARN",
            "Open WebUI memories",
            "ENABLE_MEMORIES=True missing; memory tools may be unavailable",
        )


def test_open_webui_think_light_rows(add_line: AddLine) -> None:
    code = r'''
import json
import sqlite3
import sys

db = "/app/backend/data/webui.db"
thinklight = [
    "qwen3.6-thinklight-grounded:latest",
    "deep-thinking-qwen3.6:latest",
    "web-search-deep-qwen3.6:latest",
]
full = [
    "qwen3.6-35b-a3b-grounded:latest",
    "full-thinking-qwen3.6:latest",
]
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
bad = []
for model_id in thinklight:
    row = con.execute("select params from model where id=?", (model_id,)).fetchone()
    params = json.loads(row["params"]) if row and row["params"] else {}
    if params.get("think") is not False:
        bad.append(model_id + " missing think=false")
for model_id in full:
    row = con.execute("select params from model where id=?", (model_id,)).fetchone()
    params = json.loads(row["params"]) if row and row["params"] else {}
    if params.get("think") is False:
        bad.append(model_id + " should be allowed to think")
con.close()
if bad:
    print("; ".join(bad))
    sys.exit(1)
print("think-light only")
'''
    result = run_command(
        ["docker", "exec", "afk-localai-open-webui-1", "python", "-c", code],
        cwd=REPO_ROOT,
        timeout_sec=20,
    )
    if result.code == 0:
        add_line(
            "OK",
            "Open WebUI thinking",
            "think=false only on Qwen3.6 think-light rows",
        )
    else:
        add_line(
            "WARN",
            "Open WebUI thinking",
            f"run ai-openwebui-thinklight.ps1; {result.text.strip()}",
        )


def test_context_length(add_line: AddLine, max_daily_context: int) -> None:
    ctx = get_persisted_env("OLLAMA_CONTEXT_LENGTH")
    if ctx.value:
        try:
            ctx_value = int(str(ctx.value))
        except ValueError:
            add_line("WARN", "OLLAMA_CONTEXT_LENGTH", f"not numeric: {ctx.value}")
            return
        if ctx_value <= max_daily_context:
            add_line(
                "OK",
                "OLLAMA_CONTEXT_LENGTH",
                f"{ctx_value} (daily ceiling {max_daily_context})",
            )
        else:
            add_line(
                "WARN",
                "OLLAMA_CONTEXT_LENGTH",
                f"{ctx_value} is above daily ceiling {max_daily_context}; CPU "
                "spill risk",
            )
    else:
        add_line(
            "WARN",
            "OLLAMA_CONTEXT_LENGTH",
            f"missing; expected {max_daily_context}",
        )


def test_max_loaded_models(add_line: AddLine) -> None:
    # Ollama RAG embeddings (RAG_EMBEDDING_ENGINE=ollama + nomic-embed-text) load a
    # second model; with OLLAMA_MAX_LOADED_MODELS effectively 1 the embed model evicts
    # the chat model on every search, forcing a reload. nomic is tiny (~274MB) so >=2
    # lets both stay resident on the 12GB GPU.
    row = get_persisted_env("OLLAMA_MAX_LOADED_MODELS")
    if not row.value:
        add_line(
            "WARN",
            "OLLAMA_MAX_LOADED_MODELS",
            "missing; Ollama RAG embeddings need >=2 (embed model evicts chat at 1)",
        )
        return
    try:
        loaded = int(str(row.value))
    except ValueError:
        add_line("WARN", "OLLAMA_MAX_LOADED_MODELS", f"not numeric: {row.value}")
        return
    if loaded >= 2:
        add_line(
            "OK",
            "OLLAMA_MAX_LOADED_MODELS",
            f"{loaded} (chat + embed can coexist)",
        )
    else:
        add_line(
            "WARN",
            "OLLAMA_MAX_LOADED_MODELS",
            f"{loaded}; Ollama RAG embeddings need >=2 or the embed model evicts chat",
        )


def test_webbrain_origin(add_line: AddLine) -> None:
    origins = get_persisted_env("OLLAMA_ORIGINS")
    if origins.value and WEBBRAIN_ORIGIN in str(origins.value):
        add_line("OK", "WebBrain origin", "extension origin is allowed")
    elif origins.value:
        add_line(
            "WARN",
            "WebBrain origin",
            "extension origin missing from OLLAMA_ORIGINS",
        )
    else:
        add_line("WARN", "WebBrain origin", "OLLAMA_ORIGINS missing")


def test_ollama_api(add_line: AddLine, max_think_light_context: int) -> None:
    try:
        ollama_api("/api/tags", timeout_sec=5)
    except (OSError, TimeoutError, URLError, json.JSONDecodeError):
        add_line("WARN", "Ollama API", "not reachable; live model checks skipped")
        return

    add_line("OK", "Ollama API", "http://localhost:11434")
    test_think_light_model(add_line, max_think_light_context)
    test_loaded_models(add_line)


def ollama_api(
    path: str,
    body: dict[str, object] | None = None,
    *,
    timeout_sec: int,
) -> dict[str, object]:
    url = f"http://localhost:11434{path}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    else:
        request = Request(url)

    with urlopen(request, timeout=timeout_sec) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def test_think_light_model(
    add_line: AddLine,
    max_think_light_context: int,
) -> None:
    try:
        think = ollama_api(
            "/api/show",
            {"model": "qwen3.6-thinklight-grounded"},
            timeout_sec=15,
        )
    except (OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
        add_line("WARN", "Qwen3.6 think-light", str(exc))
        return

    think_text = f"{think.get('modelfile', '')}\n{think.get('parameters', '')}"
    has_ctx = re.search(
        rf"(?m)^\s*(PARAMETER\s+)?num_ctx\s+{max_think_light_context}\s*$",
        think_text,
    )
    # 1536, not 512: clients that omit think:false burn budget on thinking
    # first, and a 512 cap left zero tokens for the actual answer (verified
    # 2026-07-03: plain /api/chat returned 512 thinking tokens, empty content).
    has_predict = re.search(
        r"(?m)^\s*(PARAMETER\s+)?num_predict\s+1536\s*$", think_text
    )
    has_prompt_think_prefill = re.search(
        r"(?s)<\|im_start\|>assistant\s*<think>\s*</think>",
        think_text,
    )
    if has_ctx and has_predict and not has_prompt_think_prefill:
        add_line(
            "OK",
            "Qwen3.6 think-light",
            f"num_ctx={max_think_light_context}, num_predict=1536, "
            "Cherry-safe template",
        )
        return

    missing = []
    if not has_ctx:
        missing.append(f"num_ctx {max_think_light_context}")
    if not has_predict:
        missing.append("num_predict 1536")
    if has_prompt_think_prefill:
        missing.append("remove prompt-level think prefill")
    add_line("WARN", "Qwen3.6 think-light", f"missing: {', '.join(missing)}")


def test_loaded_models(add_line: AddLine) -> None:
    try:
        ps = ollama_api("/api/ps", timeout_sec=10)
    except (OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
        add_line("WARN", "Loaded models", str(exc))
        return

    loaded = ps.get("models")
    if not isinstance(loaded, list) or not loaded:
        add_line("OK", "Loaded models", "none loaded; no active GPU spill")
        return

    # Disk sizes let each loaded model split weights vs context cost.
    try:
        tags = ollama_api("/api/tags", timeout_sec=10)
        tag_models = tags.get("models")
        disk_sizes = {
            str(m.get("name")): as_float(m.get("size"))
            for m in (tag_models if isinstance(tag_models, list) else [])
            if isinstance(m, dict)
        }
    except (OSError, TimeoutError, URLError, json.JSONDecodeError):
        disk_sizes = {}

    gib = 1024**3
    total_vram_attr = 0.0
    for model in loaded:
        if not isinstance(model, dict):
            continue
        label = str(model.get("name") or model.get("model") or "loaded model")
        size = as_float(model.get("size"))
        size_vram = as_float(model.get("size_vram"))
        if not (size > 0 and size_vram > 0):
            add_line("WARN", f"{label} residency", "API did not report size_vram/size")
            continue
        total_vram_attr += size_vram
        pct = round((size_vram / size) * 100)
        segments = [
            f"{pct}% GPU ({round(size_vram / gib, 1)}/{round(size / gib, 1)} GB)"
        ]
        weights = disk_sizes.get(label, 0.0)
        if weights > 0:
            # loaded - disk = graph/runtime overhead only. Measured 2026-07-15:
            # /api/ps size EXCLUDES the KV cache (9b@64k reported +0.3 GB while
            # nvidia-smi showed ~3 GB more) - KV is reported separately below.
            segments.append(f"weights {round(weights / gib, 1)} GB")
            segments.append(f"overhead {round(max(size - weights, 0.0) / gib, 1)} GB")
        cpu_bytes = max(size - size_vram, 0.0)
        if cpu_bytes > 0.05 * gib:
            segments.append(f"CPU side {round(cpu_bytes / gib, 1)} GB")
        ctx = model.get("context_length")
        if isinstance(ctx, (int, float)) and ctx > 0:
            segments.append(f"num_ctx {int(ctx)}")
        status = "OK" if pct >= 98 else "WARN"
        detail = ", ".join(segments)
        if pct < 98:
            detail += "; CPU spill likely"
        add_line(status, f"{label} residency", detail)

    # KV cache is invisible in /api/ps accounting (see comment above), so show
    # it as the gap between what the GPU reports used and what Ollama claims:
    # gap = KV cache + any non-Ollama GPU apps (browser, desktop compositor).
    if total_vram_attr > 0:
        result = run_command(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            cwd=REPO_ROOT,
            timeout_sec=15,
        )
        if result.code == 0 and result.text.strip():
            try:
                used_bytes = float(result.text.splitlines()[0].strip()) * 1024**2
            except ValueError:
                used_bytes = 0.0
            gap = used_bytes - total_vram_attr
            if used_bytes > 0 and gap > 0.2 * gib:
                add_line(
                    "OK",
                    "Context/KV cache",
                    f"~{round(gap / gib, 1)} GB GPU beyond Ollama-attributed "
                    "(KV cache + other GPU apps)",
                )


def test_gpu_headroom(add_line: AddLine) -> None:
    result = run_command(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ],
        cwd=REPO_ROOT,
        timeout_sec=15,
    )
    if result.code != 0 or not result.text.strip():
        add_line("WARN", "GPU headroom", "nvidia-smi returned nothing")
        return

    first = result.text.splitlines()[0]
    parts = [part.strip() for part in first.split(",")]
    try:
        used = float(parts[0])
        total = float(parts[1])
    except (IndexError, ValueError):
        add_line("WARN", "GPU headroom", "nvidia-smi returned nothing")
        return

    free = total - used
    used_gb = round(used / 1024, 1)
    total_gb = round(total / 1024, 1)
    free_gb = round(free / 1024, 1)
    detail = (
        f"{format_number(used_gb)}/{format_number(total_gb)} GB used; "
        f"{format_number(free_gb)} GB free"
    )
    if free_gb < 0.8:
        add_line(
            "WARN",
            "GPU headroom",
            f"{format_number(used_gb)}/{format_number(total_gb)} GB used; "
            f"only {format_number(free_gb)} GB free",
        )
    else:
        add_line("OK", "GPU headroom", detail)


def as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
