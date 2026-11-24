"""FastAPI dashboard for the Python localai orchestrator."""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from importlib.resources import files
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from localai import __version__
from localai.anywhere import collect_anywhere_report
from localai.backup import collect_backup_report
from localai.firewall import collect_firewall_report
from localai.game_mode import collect_game_mode_report
from localai.health import collect_health_report
from localai.model_scout import collect_model_scout_report, read_scout_groups
from localai.paths import REPO_ROOT, repo_path
from localai.perf import collect_perf_report
from localai.power import collect_power_report
from localai.scout_categories import category_by_id
from localai.start import collect_start_report
from localai.stop import collect_stop_report
from localai.system_info import collect_system
from localai.terminal_check import collect_terminal_check_report
from localai.update import collect_update_report
from localai.warm import (
    read_default_model,
    read_known_models,
    read_warm_model_override,
    write_known_models,
    write_warm_model_override,
)

CHERRY_STUDIO_PATH = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Programs"
    / "Cherry Studio"
    / "Cherry Studio.exe"
)

ReportFactory = Callable[[], tuple[int, list[str]]]


@dataclass(frozen=True)
class DashboardCheck:
    """A safe dashboard action backed by a Python report collector."""

    id: str
    label: str
    group: str
    mutates: bool
    command: str
    factory: ReportFactory


@dataclass(frozen=True)
class DashboardLink:
    """A browser-openable local tool link."""

    id: str
    label: str
    url: str
    port: int


class RunCheckRequest(BaseModel):
    """Request body for running a dashboard check."""

    confirmed: bool = False


class WarmModelRequest(BaseModel):
    """Request body for choosing the warm model (None/default clears it)."""

    model: str | None = None


class ScoutPrepareRequest(BaseModel):
    """Request body for preparing a category's top pick from the dashboard."""

    category: str
    confirmed: bool = False


_MODEL_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-/@]*$")


def _ollama_tags() -> list[str] | None:
    """Installed model tags from Ollama; None when the engine is down."""
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=1.5
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return [str(m.get("name")) for m in data.get("models") or [] if m.get("name")]


def _backup_check() -> tuple[int, list[str]]:
    return collect_backup_report(timeout_sec=30)


def _console_flags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def _launch_console(
    args: list[str], *, cwd: Path | None = None
) -> tuple[int, list[str]]:
    """Launch a long-running or interactive command in its own console window."""
    try:
        subprocess.Popen(args, cwd=cwd, creationflags=_console_flags(), close_fds=True)
    except OSError as exc:
        return 1, [f"[FAIL] Could not launch {args[0]}: {exc}"]
    return 0, [f"Started '{' '.join(args)}' in a new console window."]


def _console_python() -> str:
    """A console-capable interpreter for child consoles.

    Under the desktop shortcut the dashboard runs on pythonw.exe; spawning
    console children from sys.executable gives them no stdout, so their
    first print() dies with AttributeError inside an empty console window.
    """
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def _start_console() -> tuple[int, list[str]]:
    """Launch the live start in its own console window without blocking the request.

    Live start takes minutes (Docker up, model warm, health check), so it runs as a
    detached ``localai start`` process instead of blocking the dashboard request -
    mirroring how the legacy Control Center launched Start-LocalAI.bat in a window.
    """
    code, lines = _launch_console([_console_python(), "-m", "localai", "start"])
    if code != 0:
        return code, lines
    return 0, [
        *lines,
        "Watch that window for progress; refresh the status pill when it reports UP.",
    ]


def _scout_prepare_console() -> tuple[int, list[str]]:
    """Launch model-scout Prepare in its own console window.

    Prepare pulls a multi-GB model, builds a grounded wrapper, and benchmarks
    it - that needs visible progress and can run for an hour, so it gets a
    console like Start Local AI instead of blocking a dashboard request.
    """
    code, lines = _launch_console(
        [_console_python(), "-m", "localai", "model-scout", "--mode", "Prepare"]
    )
    if code != 0:
        return code, lines
    return 0, [
        *lines,
        "Pulls + grounds + benchmarks the top new pick; watch that window "
        "for progress (multi-GB download).",
        "It never changes your default model - Promote stays manual.",
    ]


def _scout_prepare_category_console(category: str) -> tuple[int, list[str]]:
    """Launch Prepare for one category's top pick in its own console window.

    Like ``_scout_prepare_console`` but pins the category (its target context
    flows into the grounded Modelfile's num_ctx). Multi-GB download, so it gets a
    visible console rather than blocking the dashboard request.
    """
    code, lines = _launch_console(
        [
            _console_python(),
            "-m",
            "localai",
            "model-scout",
            "--mode",
            "Prepare",
            "--category",
            category,
        ]
    )
    if code != 0:
        return code, lines
    return 0, [
        *lines,
        f"Preparing the '{category}' top pick; watch that window for progress "
        "(multi-GB download).",
        "It never changes your default model - Promote stays manual.",
    ]


def _game_mode_console() -> tuple[int, list[str]]:
    """Launch the real Game Mode cleanup in its own console window.

    Game Mode scans processes (WMI), stops containers, and shuts down WSL - it
    can run for a minute or more. Run in-request it blocks the dashboard past
    the webview's patience and the panel shows nothing (the 'glitches out, no
    output' bug). Like Start and scout Prepare, it gets a visible console.
    """
    code, lines = _launch_console(
        [_console_python(), "-m", "localai", "game-mode", "--disable-warm-task"]
    )
    if code != 0:
        return code, lines
    return 0, [
        *lines,
        "Frees GPU/RAM for gaming: unloads models, stops containers, shuts down "
        "WSL. Watch that window for the step-by-step result.",
    ]


def _webbrain_setup_check() -> tuple[int, list[str]]:
    """Report WebBrain (browser agent) readiness against Ollama.

    WebBrain talks to Ollama directly over the OpenAI-style /v1 API; the only
    server-side setup is its extension origin in OLLAMA_ORIGINS (the installer's
    web intent adds it). No proxy, no Node. See docs/webbrain.md.
    """
    from localai.perf import WEBBRAIN_ORIGIN, get_persisted_env

    steps = [
        "WebBrain: install from the Chrome Web Store, set its server URL to",
        "http://localhost:11434 (it adds /v1 itself), and keep the Chrome",
        "window visible during tasks. Details: docs/webbrain.md",
    ]
    origins = get_persisted_env("OLLAMA_ORIGINS")
    if origins.value and WEBBRAIN_ORIGIN in str(origins.value):
        return 0, ["[OK] WebBrain origin allowlisted in OLLAMA_ORIGINS.", *steps]
    return 1, [
        "[FAIL] OLLAMA_ORIGINS does not allow the WebBrain extension origin.",
        f"Fix: add {WEBBRAIN_ORIGIN} to user-scope OLLAMA_ORIGINS, restart Ollama.",
        *steps,
    ]


def _self_test_check() -> tuple[int, list[str]]:
    """Launch the full self-test in its own console; it runs real inference probes."""
    script = repo_path("ai-selftest.ps1")
    if not script.exists():
        return 1, [f"[FAIL] Missing {script}"]
    code, lines = _launch_console(
        ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=REPO_ROOT,
    )
    if code != 0:
        return code, lines
    return 0, [*lines, "Runs real chat/web/vision/code probes; no permanent changes."]


_AGENT_PICKER_LOCK = threading.Lock()


def _pick_folder() -> str | None:
    """Ask the native dashboard window for a folder.

    Returns the picked path, ``""`` when the user cancels, or ``None`` when no
    native window exists to host the dialog (browser/headless mode).
    """
    try:
        import webview
    except ImportError:
        return None
    if not webview.windows:
        return None
    try:
        picked = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
    except Exception:
        return None
    if not picked:
        return ""
    return str(picked[0])


def _agent_check() -> tuple[int, list[str]]:
    """Pick a project folder, then launch the AI code agent there in a console.

    The agent (opencode) is interactive - the prompt is typed in its own console
    window, so the dashboard only needs to supply the project folder.
    """
    script = repo_path("Start-AI-Agent.ps1")
    if not script.exists():
        return 1, [f"[FAIL] Missing {script}"]
    if not _AGENT_PICKER_LOCK.acquire(blocking=False):
        return 0, ["[WARN] A folder picker is already open - finish that one first."]
    try:
        directory = _pick_folder()
    finally:
        _AGENT_PICKER_LOCK.release()
    if directory is None:
        return 1, [
            "[FAIL] No native window to host the folder picker.",
            "Run the dashboard as a window, or launch from a terminal: "
            f"pwsh -File {script} -Dir <project>",
        ]
    if not directory:
        return 0, ["Cancelled - no folder selected."]
    code, lines = _launch_console(
        [
            "pwsh",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Dir",
            directory,
        ],
        cwd=Path(directory),
    )
    if code != 0:
        return code, lines
    return 0, [*lines, "opencode asks before edits/commands; type /exit to quit."]


def _cherry_studio_check() -> tuple[int, list[str]]:
    if not CHERRY_STUDIO_PATH.exists():
        return 1, [f"[FAIL] Cherry Studio not found at {CHERRY_STUDIO_PATH}"]
    try:
        subprocess.Popen(
            [str(CHERRY_STUDIO_PATH)],
            cwd=str(CHERRY_STUDIO_PATH.parent),
            close_fds=True,
        )
    except OSError as exc:
        return 1, [f"[FAIL] Could not launch Cherry Studio: {exc}"]
    return 0, ["Launched Cherry Studio."]


CHECKS: dict[str, DashboardCheck] = {
    "start": DashboardCheck(
        "start",
        "Start Local AI",
        "Everyday",
        True,
        "localai start",
        _start_console,
    ),
    "start-dry-run": DashboardCheck(
        "start-dry-run",
        "Start Dry Run",
        "Maintenance",
        False,
        "localai start --dry-run",
        lambda: collect_start_report(dry_run=True),
    ),
    "webbrain": DashboardCheck(
        "webbrain",
        "WebBrain",
        "Everyday",
        False,
        "check WebBrain origin",
        _webbrain_setup_check,
    ),
    "health": DashboardCheck(
        "health",
        "Health",
        "Status",
        False,
        "localai health",
        collect_health_report,
    ),
    "perf": DashboardCheck(
        "perf",
        "Performance",
        "Status",
        False,
        "localai perf",
        collect_perf_report,
    ),
    "power": DashboardCheck(
        "power",
        "Power",
        "Status",
        False,
        "localai power",
        collect_power_report,
    ),
    "terminal": DashboardCheck(
        "terminal",
        "Terminal",
        "Status",
        False,
        "localai terminal-check",
        collect_terminal_check_report,
    ),
    "anywhere": DashboardCheck(
        "anywhere",
        "Anywhere",
        "Status",
        False,
        "localai anywhere",
        collect_anywhere_report,
    ),
    "firewall": DashboardCheck(
        "firewall",
        "Firewall",
        "Status",
        False,
        "localai firewall",
        collect_firewall_report,
    ),
    "update-check": DashboardCheck(
        "update-check",
        "Update Check",
        "Maintenance",
        False,
        "localai update --mode Check --quiet",
        lambda: collect_update_report(mode="Check", quiet=True),
    ),
    "update-now": DashboardCheck(
        "update-now",
        "Update Now",
        "Maintenance",
        True,
        "localai update --mode Apply --quiet",
        lambda: collect_update_report(mode="Apply", quiet=True),
    ),
    "model-scout": DashboardCheck(
        "model-scout",
        "Model Scout",
        "Maintenance",
        False,
        "localai model-scout --mode Scout --quiet --top-n 3",
        lambda: collect_model_scout_report(mode="Scout", quiet=True, top_n=3),
    ),
    "scout-prepare": DashboardCheck(
        "scout-prepare",
        "Prepare Model",
        "Maintenance",
        True,
        "localai model-scout --mode Prepare",
        _scout_prepare_console,
    ),
    "backup": DashboardCheck(
        "backup",
        "Backup",
        "Maintenance",
        True,
        "localai backup --timeout-sec 30",
        _backup_check,
    ),
    "game-dry-run": DashboardCheck(
        "game-dry-run",
        "Game Mode Dry Run",
        "Maintenance",
        False,
        "localai game-mode --dry-run",
        lambda: collect_game_mode_report(dry_run=True),
    ),
    "game-mode": DashboardCheck(
        "game-mode",
        "Game Mode",
        "Maintenance",
        True,
        "localai game-mode --disable-warm-task",
        _game_mode_console,
    ),
    "stop": DashboardCheck(
        "stop",
        "Stop Local AI",
        "Maintenance",
        True,
        "localai stop",
        collect_stop_report,
    ),
    "doctor": DashboardCheck(
        "doctor",
        "Full Self-Test",
        "Maintenance",
        False,
        "ai-selftest.ps1",
        _self_test_check,
    ),
    "cherry": DashboardCheck(
        "cherry",
        "Cherry Studio",
        "Everyday",
        False,
        str(CHERRY_STUDIO_PATH),
        _cherry_studio_check,
    ),
    "agent": DashboardCheck(
        "agent",
        "AI Code Agent",
        "Everyday",
        False,
        "Start-AI-Agent.ps1 -Dir <picked folder>",
        _agent_check,
    ),
}

LINKS: tuple[DashboardLink, ...] = (
    DashboardLink("chat", "Open Chat", "http://localhost:3000", 3000),
    DashboardLink("image", "Image Studio", "http://localhost:8188", 8188),
)

# Legacy Control Center actions that still lack a dashboard port. Empty since
# scout Prepare landed; Promote is deliberately manual, not pending.
PENDING_ACTIONS: tuple[dict[str, str], ...] = ()


def _app_version() -> str:
    try:
        return package_version("localai")
    except PackageNotFoundError:
        return __version__


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ollama_ps() -> dict[str, Any] | None:
    """Fetch loaded-model state from Ollama; None when the engine is down."""
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/ps", timeout=1.5
        ) as resp:
            payload: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
            return payload
    except (OSError, urllib.error.URLError, ValueError):
        return None


def _keep_alive_minutes(expires_at: str) -> int | None:
    """Minutes until Ollama unloads the model. Ollama emits ns fractions."""
    trimmed = re.sub(r"\.(\d{1,6})\d*", r".\1", expires_at)
    try:
        expires = datetime.fromisoformat(trimmed)
    except ValueError:
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    remaining = (expires - _utcnow()).total_seconds()
    return max(0, round(remaining / 60))


def collect_runtime() -> dict[str, Any]:
    """Live engine telemetry for the dashboard's Runtime strip."""
    info: dict[str, Any] = {
        "host": socket.gethostname(),
        "version": _app_version(),
        "engine": "offline",
        "model": None,
        "vramGb": None,
        "gpuPercent": None,
        "keepAliveMin": None,
    }
    data = _ollama_ps()
    if data is None:
        return info
    info["engine"] = "ok"
    models = data.get("models") or []
    if not models:
        return info
    model = models[0]
    info["model"] = model.get("name")
    size = int(model.get("size") or 0)
    size_vram = int(model.get("size_vram") or 0)
    if size_vram:
        info["vramGb"] = round(size_vram / 1024**3, 1)
    if size:
        info["gpuPercent"] = max(0, min(100, round(size_vram * 100 / size)))
    expires_at = model.get("expires_at")
    if expires_at:
        info["keepAliveMin"] = _keep_alive_minutes(str(expires_at))
    return info


def create_app() -> FastAPI:
    """Create the localai dashboard app."""
    app = FastAPI(title="localai dashboard", version=_app_version())
    static_root = files("localai").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static_root)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html = static_root.joinpath("dashboard.html").read_text(encoding="utf-8")
        # Stamp asset URLs with their mtime so the WebView2 cache never serves
        # stale CSS/JS after an update (a real problem this session).
        for asset in ("dashboard.css", "dashboard.js"):
            token = int(Path(str(static_root.joinpath(asset))).stat().st_mtime)
            html = html.replace(f"/static/{asset}", f"/static/{asset}?v={token}")
        return html

    @app.get("/api/dashboard")
    def dashboard_manifest() -> dict[str, Any]:
        return {
            "checks": [serialize_check(check) for check in CHECKS.values()],
            "links": [link.__dict__ for link in LINKS],
            "pendingActions": list(PENDING_ACTIONS),
        }

    @app.get("/api/runtime")
    def runtime() -> dict[str, Any]:
        return collect_runtime()

    @app.get("/api/system")
    def system() -> dict[str, Any]:
        return collect_system()

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        tags = _ollama_tags()
        source = "ollama" if tags is not None else "cache"
        if tags is not None:
            write_known_models(tags)
        else:
            tags = read_known_models()
        default = read_default_model()
        selected = read_warm_model_override() or default
        ordered: list[str] = []
        for name in (default, selected, *tags):
            if name and name not in ordered:
                ordered.append(name)
        return {
            "models": ordered,
            "selected": selected,
            "default": default,
            "source": source,
        }

    @app.post("/api/warm-model")
    def set_warm_model(request: WarmModelRequest) -> dict[str, Any]:
        model = (request.model or "").strip()
        if model and not _MODEL_TAG_RE.match(model):
            raise HTTPException(status_code=400, detail="Invalid model tag")
        default = read_default_model()
        write_warm_model_override(model if model and model != default else None)
        return {"selected": read_warm_model_override() or default}

    @app.get("/api/scout")
    def scout_status() -> dict[str, Any]:
        cache = read_scout_groups()
        if cache is None:
            return {"generated": None, "groups": None}
        return cache

    @app.post("/api/scout/refresh")
    def scout_refresh() -> dict[str, Any]:
        # Scout fetches bounded metadata (no model download) in a worker
        # thread and rewrites the dated cache. Socket timeouts are not a total
        # refresh deadline. Prepare uses its own console for multi-GB pulls.
        code, lines = collect_model_scout_report(mode="Scout", quiet=True, top_n=3)
        cache = read_scout_groups() or {"generated": None, "groups": None}
        return {
            "exitCode": code,
            "status": status_from_report(code, lines),
            "lines": lines,
            **cache,
        }

    @app.post("/api/scout/prepare")
    def scout_prepare(request: ScoutPrepareRequest) -> dict[str, Any]:
        if category_by_id(request.category) is None:
            raise HTTPException(
                status_code=400, detail=f"Unknown category: {request.category}"
            )
        if not request.confirmed:
            raise HTTPException(
                status_code=409,
                detail="Confirmation required to prepare a model (multi-GB pull)",
            )
        code, lines = _scout_prepare_category_console(request.category)
        return {
            "category": request.category,
            "exitCode": code,
            "status": status_from_report(code, lines),
            "lines": lines,
        }

    @app.post("/api/checks/{check_id}")
    def run_check(
        check_id: str,
        request: RunCheckRequest | None = None,
    ) -> dict[str, Any]:
        check = CHECKS.get(check_id)
        if check is None:
            raise HTTPException(status_code=404, detail=f"Unknown check: {check_id}")
        if check.mutates and (request is None or not request.confirmed):
            raise HTTPException(
                status_code=409,
                detail=f"Confirmation required for mutating check: {check_id}",
            )

        code, lines = check.factory()
        return {
            **serialize_check(check),
            "exitCode": code,
            "status": status_from_report(code, lines),
            "lines": lines,
        }

    return app


def serialize_check(check: DashboardCheck) -> dict[str, Any]:
    return {
        "id": check.id,
        "label": check.label,
        "group": check.group,
        "mutates": check.mutates,
        "requiresConfirmation": check.mutates,
        "command": check.command,
    }


def status_from_report(code: int, lines: list[str]) -> str:
    if code != 0 or any(line.startswith("[FAIL]") for line in lines):
        return "fail"
    if any(line.startswith("[WARN]") for line in lines):
        return "warn"
    return "ok"


def serve_dashboard(
    host: str,
    port: int,
    open_browser: bool,
    *,
    window: bool = True,
) -> None:
    """Open the dashboard as a native window, or serve it for the browser.

    Native-window mode (the default) runs the FastAPI app in a daemon thread and
    shows it in a pywebview OS window. If pywebview is unavailable it falls back to
    browser/headless serving.
    """
    if window:
        if _run_window(host, port):
            return
        # pywebview is unavailable: degrade to a browser tab so there is still a
        # visible UI, rather than a silent headless server (which shows nothing
        # at all when launched via pythonw from the desktop shortcut).
        open_browser = True
    if open_browser:
        webbrowser.open(f"http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


def _say(message: str) -> None:
    """Report a launcher problem without dying under pythonw (stdout is None)."""
    with contextlib.suppress(Exception):
        print(message)
    try:
        log_dir = REPO_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = _utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with (log_dir / "dashboard.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {message}\n")
    except OSError:
        pass


def _apply_window_icon(title: str) -> None:
    """Swap pythonw's default window/taskbar icon for the project icon."""
    if sys.platform != "win32":
        return
    icon = repo_path("AI-Dashboard.ico")
    if not icon.exists():
        return
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = 0
    for _ in range(80):
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            break
        time.sleep(0.25)
    if not hwnd:
        return
    image_icon, load_from_file = 1, 0x10
    for size, which in ((16, 0), (32, 1)):  # WM_SETICON small, big
        handle = user32.LoadImageW(
            None, str(icon), image_icon, size, size, load_from_file
        )
        if handle:
            user32.SendMessageW(hwnd, 0x0080, which, handle)


def _run_window(host: str, port: int) -> bool:
    """Show the dashboard in a native window. Returns False if pywebview is absent."""
    try:
        import webview
    except ImportError:
        return False

    title = "localai Control Center"

    # A dashboard is already serving this port (a second shortcut click or a
    # leftover instance): reuse it and just show a window, rather than starting a
    # rival server that fails to bind and shows nothing at all under pythonw.
    if _dashboard_alive(host, port):
        webview.create_window(title, f"http://{host}:{port}", width=1240, height=900)
        threading.Thread(target=_apply_window_icon, args=(title,), daemon=True).start()
        webview.start()
        return True

    # Otherwise take the requested port, or the next free one if it is taken
    # (e.g. a WinNAT-reserved range that raises WinError 10013 on bind).
    port = _bindable_port(host, port)
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host=host, port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        if _wait_for_server(host, port):
            webview.create_window(
                title, f"http://{host}:{port}", width=1240, height=900
            )
            threading.Thread(
                target=_apply_window_icon, args=(title,), daemon=True
            ).start()
            webview.start()
        else:
            _say("Dashboard server did not come up; try: localai dashboard --web")
    except Exception as exc:  # surface any GUI backend failure instead of crashing
        _say(f"Native window failed ({exc}); try: localai dashboard --web")
    finally:
        server.should_exit = True
    return True


def _wait_for_server(host: str, port: int, *, attempts: int = 50) -> bool:
    url = f"http://{host}:{port}/"
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    return False


def _dashboard_alive(host: str, port: int) -> bool:
    """True if a localai dashboard already answers on this port."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/dashboard", timeout=1):
            return True
    except (OSError, urllib.error.URLError):
        return False


def _bindable_port(host: str, port: int, *, span: int = 20) -> int:
    """Return the requested port if free, else the next free port within span."""
    for candidate in range(port, port + span):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    return port
