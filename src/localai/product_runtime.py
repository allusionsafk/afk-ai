"""Operations the installed AFK AI performs on its own runtime.

These replace the engineering-workbench commands the shell used to call:

- ``start`` used to be the workbench start: Tailscale Serve, model warm-up,
  purpose aliases, a full engineering health check (MCP bundles, git hooks,
  browser agents) that could fail Start on a normal PC, and it opened the browser
  itself - routing someone to chat without ever asking whether chat worked.
- model download used to be ``ollama pull`` captured to completion: a multi-GB
  download showed nothing for tens of minutes.
- Open WebUI seeding used ``docker compose exec``, which addresses a container by
  PROJECT NAME. Another stack claiming the same name would have received the
  write.

Every Docker action below addresses containers this installation has PROVEN it
owns (by compose config-file label), and Start refuses outright when a foreign
stack claims the product's project name.

Progress is reported as ``AFK-EVENT:`` lines (schema 1) while work is running.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from localai import readiness
from localai.afk_ownership import AFK_COMPOSE_PROJECT, docker_executable
from localai.compose import docker_env
from localai.ops import CommandResult, run_command
from localai.product_config import (
    ProductLayout,
    configured_model,
    ensure_runtime_config,
    valid_model_tag,
)

Emit = Callable[..., None]
EVENT_PREFIX = "AFK-EVENT:"
STATUS_PREFIX = "AFK-STATUS:"

_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def event_line(
    event_type: str,
    phase: str,
    *,
    status: str = "",
    code: str = "",
    message: str = "",
    data: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event_type": event_type,
        "phase": phase,
        "status": status,
        "code": code,
        "message": message,
    }
    if data is not None:
        payload["data"] = data
    return EVENT_PREFIX + json.dumps(payload, separators=(",", ":"))


def stdout_emitter(
    event_type: str,
    phase: str,
    *,
    status: str = "",
    code: str = "",
    message: str = "",
    data: dict[str, Any] | None = None,
) -> None:
    sys.stdout.write(
        event_line(
            event_type, phase, status=status, code=code, message=message, data=data
        )
        + "\n"
    )
    sys.stdout.flush()


# ------------------------------------------------------------ external apps


def ollama_app_path() -> Path:
    return (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "Ollama"
        / "ollama app.exe"
    )


def ollama_cli_path() -> str | None:
    bundled = (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    )
    if bundled.is_file():
        return str(bundled)
    return shutil.which("ollama")


def docker_desktop_path() -> Path:
    return (
        Path(os.environ.get("PROGRAMFILES", ""))
        / "Docker"
        / "Docker"
        / "Docker Desktop.exe"
    )


def _persisted_user_env(names: tuple[str, ...]) -> dict[str, str]:
    """User-scope values setup persisted for Ollama (Windows registry)."""
    if sys.platform != "win32":
        return {}
    import winreg

    found: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for name in names:
                try:
                    value, _kind = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                if isinstance(value, str) and value:
                    found[name] = value
    except OSError:
        return {}
    return found


OLLAMA_ENV_NAMES = (
    "OLLAMA_HOST",
    "OLLAMA_KV_CACHE_TYPE",
    "OLLAMA_FLASH_ATTENTION",
    "OLLAMA_KEEP_ALIVE",
    "OLLAMA_ORIGINS",
)


def launch_detached(path: Path, *, env: dict[str, str] | None = None) -> bool:
    if not path.is_file():
        return False
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    try:
        subprocess.Popen([str(path)], creationflags=flags, close_fds=True, env=env)
    except OSError:
        return False
    return True


# ------------------------------------------------------------------ streaming


def stream_command(
    args: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str] | None,
    timeout_sec: float,
    on_line: Callable[[str], None],
) -> CommandResult:
    """Run a command, delivering each output line while it runs."""
    try:
        process = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_NO_WINDOW,
        )
    except OSError as error:
        return CommandResult(tuple(args), 1, "", f"Launch failed: {error}\n")

    # A reader thread, so a child that goes silent cannot block the deadline.
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            lines.put(raw.rstrip("\r\n"))
        lines.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout_sec
    captured: list[str] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            process.wait()
            return CommandResult(tuple(args), 124, "\n".join(captured), "Timed out\n")
        try:
            item = lines.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            continue
        if item is None:
            break
        captured.append(item)
        if item.strip():
            on_line(item)
    code = process.wait()
    return CommandResult(tuple(args), code, "\n".join(captured), "")


# --------------------------------------------------------------------- start


@dataclass
class StartDeps:
    """Seams for tests; defaults are the real machine."""

    runner: Callable[..., CommandResult] = run_command
    probe: readiness.HttpProbe = readiness.http_get
    inference: readiness.InferencePoster | None = None
    launch: Callable[..., bool] = launch_detached
    stream: Callable[..., CommandResult] = stream_command
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


def _docker_ready(deps: StartDeps) -> bool:
    result = deps.runner(
        [docker_executable(), "info", "--format", "{{.ID}}"], timeout_sec=20
    )
    return result.code == 0


def _ollama_ready(deps: StartDeps) -> bool:
    state, _detail, _models = readiness.probe_ollama(probe=deps.probe, timeout_sec=3)
    return state == readiness.SERVICE_READY


def _wait(
    predicate: Callable[[], bool],
    *,
    deps: StartDeps,
    timeout_sec: float,
    interval_sec: float,
) -> bool:
    deadline = deps.clock() + timeout_sec
    while True:
        if predicate():
            return True
        if deps.clock() >= deadline:
            return False
        deps.sleep(interval_sec)


def compose_up_args(layout: ProductLayout) -> list[str]:
    """``docker compose up`` pinned to THIS installation's file, config and project.

    The project is named explicitly: Compose ranks ``COMPOSE_PROJECT_NAME``
    (from the environment or an env file) above the file's ``name:``, so an
    inherited value would otherwise start AFK AI as some other project. Start
    refuses before this runs if any container outside this installation claims
    AFK's project.
    """
    args = [
        docker_executable(),
        "compose",
        "--project-name",
        AFK_COMPOSE_PROJECT,
        "--project-directory",
        str(layout.program_root),
        "--file",
        str(layout.compose_file),
    ]
    if layout.runtime_env.is_file():
        args += ["--env-file", str(layout.runtime_env)]
    return args + ["--progress", "plain", "up", "--detach"]


def compose_env() -> dict[str, str]:
    """Docker's environment without an inherited Compose project name.

    ``--project-name`` already decides the project; nothing a user or another
    tool left in the environment is passed through to compete with it.
    """
    return {
        key: value
        for key, value in docker_env().items()
        if key.upper() != "COMPOSE_PROJECT_NAME"
    }


def start_product(
    layout: ProductLayout,
    *,
    emit: Emit = stdout_emitter,
    deps: StartDeps | None = None,
    backend_timeout_sec: float = 240,
) -> tuple[int, readiness.ProductStatus]:
    """Start this installation's runtime, then QUALIFY it. Never opens a browser."""
    deps = deps or StartDeps()
    phase = "start"

    def step(code: str, message: str) -> None:
        emit("progress", phase, status="running", code=code, message=message)

    step("config", "Checking AFK AI's configuration.")
    ensure_runtime_config(layout)
    model = configured_model(layout)

    step("ollama", "Starting the model runtime (Ollama).")
    if not _ollama_ready(deps):
        env = dict(os.environ)
        env.update(_persisted_user_env(OLLAMA_ENV_NAMES))
        deps.launch(ollama_app_path(), env=env)
        if not _wait(
            lambda: _ollama_ready(deps), deps=deps, timeout_sec=90, interval_sec=2
        ):
            return _finish(layout, deps, emit, model, "Ollama did not start.")

    step("docker", "Starting Docker Desktop.")
    if not _docker_ready(deps):
        deps.launch(docker_desktop_path())
        if not _wait(
            lambda: _docker_ready(deps), deps=deps, timeout_sec=240, interval_sec=5
        ):
            return _finish(layout, deps, emit, model, "Docker Desktop did not start.")

    found = readiness.discover(layout.compose_file, runner=deps.runner)
    if found.foreign_same_project:
        emit(
            "phase-failure",
            phase,
            status="failure",
            code="FOREIGN_PROJECT_COLLISION",
            message=(
                "Another Docker project uses AFK AI's project name; nothing was "
                "started."
            ),
        )
        return _finish(layout, deps, emit, model, None)

    step("services", "Starting chat services.")
    up = deps.stream(
        compose_up_args(layout),
        cwd=layout.program_root,
        env=compose_env(),
        timeout_sec=1800,
        on_line=lambda line: emit(
            "output", phase, status="running", code="compose", message=line[:300]
        ),
    )
    if up.code != 0:
        return _finish(layout, deps, emit, model, "Chat services did not start.")

    step("backend", "Waiting for chat to answer.")

    def backend_live() -> bool:
        snapshot = readiness.collect_product_status(
            program_root=layout.program_root,
            configured_model=model,
            runner=deps.runner,
            probe=deps.probe,
            verify_inference=False,
        )
        return snapshot.state not in (readiness.STARTING,)

    _wait(backend_live, deps=deps, timeout_sec=backend_timeout_sec, interval_sec=3)
    step("qualify", "Checking that chat can answer.")
    return _finish(layout, deps, emit, model, None)


def _finish(
    layout: ProductLayout,
    deps: StartDeps,
    emit: Emit,
    model: str | None,
    failure: str | None,
) -> tuple[int, readiness.ProductStatus]:
    if failure:
        emit(
            "phase-failure",
            "start",
            status="failure",
            code="start-failed",
            message=failure,
        )
    status = readiness.collect_product_status(
        program_root=layout.program_root,
        configured_model=model,
        runner=deps.runner,
        probe=deps.probe,
        inference=deps.inference,
        verify_inference=True,
    )
    return (0 if status.state == readiness.READY else 1), status


# ---------------------------------------------------------------- model pull


def _stream_json_lines(
    url: str, payload: dict[str, Any], *, timeout_sec: float
) -> Iterator[dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        for raw in response:
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                yield item


def pull_model(
    source: str,
    *,
    emit: Emit = stdout_emitter,
    stream: Callable[..., Iterator[dict[str, Any]]] = _stream_json_lines,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, str]:
    """Download ``source`` through Ollama's API, reporting byte progress."""
    if not valid_model_tag(source):
        return False, "invalid model name"
    last_emit: float | None = None
    last_percent = -1
    try:
        for item in stream(
            f"{readiness.OLLAMA_URL}/api/pull",
            {"model": source, "stream": True},
            timeout_sec=600,
        ):
            if item.get("error"):
                return False, f"download failed: {str(item['error'])[:200]}"
            state = str(item.get("status") or "")
            total = item.get("total")
            completed = item.get("completed")
            if isinstance(total, int) and total > 0 and isinstance(completed, int):
                percent = int(completed * 100 / total)
                now = clock()
                if percent != last_percent and (
                    last_emit is None or now - last_emit >= 1.0 or percent == 100
                ):
                    last_emit, last_percent = now, percent
                    emit(
                        "progress",
                        "pulls",
                        status="running",
                        code="download",
                        message=f"Downloading {source}: {percent}%",
                        data={
                            "completed": completed,
                            "total": total,
                            "percent": percent,
                        },
                    )
            elif state == "success":
                emit(
                    "progress",
                    "pulls",
                    status="running",
                    code="download",
                    message=f"Downloaded {source}.",
                )
                return True, "downloaded"
            elif state:
                emit(
                    "progress",
                    "pulls",
                    status="running",
                    code="download",
                    message=state[:120],
                )
    except (OSError, urllib.error.URLError) as error:
        return False, f"download interrupted ({type(error).__name__})"
    return False, "download ended without confirmation"


def create_context_tag(
    source: str,
    tag: str,
    num_ctx: int,
    *,
    runner: Callable[..., CommandResult] = run_command,
) -> tuple[bool, str]:
    """``ollama create <tag>`` = ``source`` with this tier's context size."""
    if (
        not (valid_model_tag(source) and valid_model_tag(tag))
        or not 512 <= num_ctx <= 262144
    ):
        return False, "invalid model or context size"
    ollama = ollama_cli_path()
    if ollama is None:
        return False, "Ollama is not installed"
    with tempfile.TemporaryDirectory(prefix="afk-modelfile-") as directory:
        modelfile = Path(directory) / "Modelfile"
        modelfile.write_text(
            f"FROM {source}\nPARAMETER num_ctx {num_ctx}\n", encoding="ascii"
        )
        result = runner(
            [ollama, "create", tag, "-f", str(modelfile)],
            cwd=Path(directory),
            timeout_sec=600,
        )
    if result.code != 0:
        return False, f"could not create {tag} (exit {result.code})"
    return True, f"created {tag}"


# --------------------------------------------------------------------- seed


def owned_container_id(
    layout: ProductLayout,
    service: str,
    *,
    runner: Callable[..., CommandResult] = run_command,
) -> str | None:
    found = readiness.discover(layout.compose_file, runner=runner)
    if found.problem or found.foreign_same_project:
        return None
    for container in found.owned:
        if container.service == service and container.state == "running":
            return container.container_id
    return None


def owned_exec(
    layout: ProductLayout, *, runner: Callable[..., CommandResult] = run_command
) -> Callable[..., CommandResult]:
    """An ``exec_fn`` for webui_seed that targets the proven container id."""

    def exec_fn(service: str, args: list[str], *, timeout_sec: int) -> CommandResult:
        container = owned_container_id(layout, service, runner=runner)
        if container is None:
            return CommandResult(tuple(args), 3, "", "AFK-owned container not running")
        return runner(
            [docker_executable(), "exec", container, *args], timeout_sec=timeout_sec
        )

    return exec_fn
