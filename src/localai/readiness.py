"""What the product is actually doing, and whether chat really works.

A green prerequisite check is not a usable AI. A real install passed every
prerequisite gate, reported "Your local AI is ready", and opened a chat page
that could not talk to its own backend - because nothing in the readiness path
ever asked the backend a question.

So READY here is a claim about the product, not about Windows:

    Docker is reachable
      and this installation's own Open WebUI container is running
      and its BACKEND answers (not just the static frontend)
      and Ollama answers
      and the configured model exists
      and the chat backend itself can reach Ollama and see that model
      and a tiny generation actually completes

Anything less is DEGRADED, STARTING, STOPPED or UNKNOWN, never READY. Every
state carries a reason code, a human sentence and the next useful action, so the
shell never has to invent any of them.

Two observation modes exist because the evidence has very different costs:

``qualify``
    Everything above, including the tiny generation. Loads the model if it is
    not already loaded. This is the only mode that can produce READY.

``liveness``
    Everything except the container-to-Ollama exec and the generation: one
    ``docker ps`` and two loopback GETs. When all of that is healthy the state is
    LIVE - "nothing observable has regressed" - which the shell may use to KEEP a
    previous READY, but never to create one.

One thing this deliberately does NOT claim: that a human has completed Open
WebUI's first-run signup. ``onboarding_required`` is reported as a separate fact;
chat is still the right place to send someone who needs to create that account.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from localai.afk_ownership import (
    CONFIG_FILES_LABEL,
    PROJECT_LABEL,
    docker_executable,
    owned_compose_file,
)
from localai.ops import CommandResult, run_command

SCHEMA_VERSION = 2

MODE_QUALIFY = "qualify"
MODE_LIVENESS = "liveness"

# Product states.
STOPPED = "Stopped"
STARTING = "Starting"
DEGRADED = "Degraded"
LIVE = "Live"
READY = "Ready"
FAILED = "Failed"
NOT_INSTALLED = "NotInstalled"
UNKNOWN = "Unknown"

# Per-service states.
SERVICE_STOPPED = "Stopped"
SERVICE_STARTING = "Starting"
SERVICE_READY = "Ready"
SERVICE_DEGRADED = "Degraded"
SERVICE_FAILED = "Failed"
SERVICE_NOT_REQUIRED = "NotRequired"
SERVICE_NOT_CHECKED = "NotChecked"

# Inference qualification outcomes.
INFERENCE_PASSED = "passed"
INFERENCE_FAILED = "failed"
INFERENCE_NOT_RUN = "not_run"

WEBUI_URL = "http://127.0.0.1:3000"
CHAT_URL = f"{WEBUI_URL}/"
OLLAMA_URL = "http://127.0.0.1:11434"

# Required for the product to be usable at all. Kokoro (speech) and SearXNG
# (web search) are features; chat does not depend on them, so their absence
# degrades rather than fails.
REQUIRED_SERVICES = ("open-webui",)
OPTIONAL_SERVICES = ("searxng", "kokoro")

# The one next step the shell should offer for each reason. The shell renders
# these as buttons from a fixed allowlist; it does not reinterpret the reason.
NEXT_ACTION: dict[str, str] = {
    "READY": "open_chat",
    "LIVE": "check",
    "PAYLOAD_NOT_FOUND": "repair",
    "DOCKER_UNREACHABLE": "start",
    "DOCKER_TIMEOUT": "check",
    "NOT_STARTED": "start",
    "FOREIGN_PROJECT_COLLISION": "diagnostics",
    "WEBUI_NOT_RUNNING": "start",
    "WEBUI_STARTING": "wait",
    "WEBUI_BACKEND_UNAVAILABLE": "repair",
    "WEBUI_DEGRADED": "repair",
    "OLLAMA_UNAVAILABLE": "start",
    "MODEL_NOT_CONFIGURED": "repair",
    "MODEL_MISSING": "repair",
    "BACKEND_CANNOT_REACH_OLLAMA": "diagnostics",
    "INFERENCE_FAILED": "diagnostics",
    "STATUS_ERROR": "diagnostics",
}

_COMPOSE_NAME = re.compile(r"^name:\s*['\"]?([A-Za-z0-9][A-Za-z0-9_.-]*)", re.MULTILINE)


class CommandRunner(Protocol):
    """The subset of :func:`localai.ops.run_command` this module depends on."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult: ...


class HttpProbe(Protocol):
    """Returns (status_code, body) or raises OSError."""

    def __call__(self, url: str, *, timeout_sec: float) -> tuple[int, str]: ...


InferencePoster = Callable[..., tuple[int, str]]


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    state: str
    detail: str = ""


@dataclass
class ProductStatus:
    """A snapshot the UI can render without making a second judgement."""

    state: str = UNKNOWN
    reason: str = "STATUS_ERROR"
    message: str = ""
    mode: str = MODE_QUALIFY
    services: list[ServiceStatus] = field(default_factory=list)
    model: str | None = None
    chat_ready: bool = False
    onboarding_required: bool | None = None
    inference: str = INFERENCE_NOT_RUN
    inference_detail: str = ""
    observed_at_utc: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    @property
    def next_action(self) -> str:
        return NEXT_ACTION.get(self.reason, "diagnostics")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "observed_at_utc": self.observed_at_utc,
            "mode": self.mode,
            "state": self.state,
            "reason": self.reason,
            "message": self.message,
            "next_action": self.next_action,
            "chat": {
                "ready": self.chat_ready,
                # The URL is only handed out when chat is usable, so a caller
                # cannot route someone into a known-broken page by accident.
                "url": CHAT_URL if self.chat_ready else None,
                "onboarding_required": self.onboarding_required,
            },
            "qualification": {
                "inference": self.inference,
                "detail": self.inference_detail,
            },
            "model": self.model,
            "services": [
                {"name": s.name, "state": s.state, "detail": s.detail}
                for s in self.services
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def http_get(url: str, *, timeout_sec: float = 5.0) -> tuple[int, str]:
    """GET a local URL, returning (status, body) even for error statuses."""
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        # A 500 from the backend is the signal we care about most, not an
        # exception to swallow: it is exactly what "Backend Required" looks like.
        return int(error.code), error.read().decode("utf-8", "replace")


def _post_json(
    url: str, payload: dict[str, Any], *, timeout_sec: float
) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return int(error.code), error.read().decode("utf-8", "replace")


@dataclass(frozen=True)
class OwnedContainer:
    container_id: str
    name: str
    service: str
    state: str
    health: str


@dataclass(frozen=True)
class Discovery:
    owned: list[OwnedContainer]
    foreign_same_project: int
    problem: str | None
    timed_out: bool = False


def compose_project_name(compose_file: Path) -> str | None:
    """The ``name:`` the shipped compose file claims, or None if unreadable."""
    try:
        text = compose_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _COMPOSE_NAME.search(text)
    return match.group(1) if match else None


def discover(
    compose_file: Path,
    *,
    runner: CommandRunner = run_command,
    timeout_sec: int = 30,
) -> Discovery:
    """List containers this installation owns, by config-file path.

    Ownership is decided exactly the way the uninstall path decides it: the
    container's compose config-file label must be this installation's own
    compose file. A project NAME is never sufficient - another checkout on the
    same machine can claim the same one. Containers that claim this product's
    project name without being ours are counted (never named): compose would
    adopt them on ``up``, so their presence blocks a safe start.
    """
    template = (
        '{{.ID}}\t{{.Label "com.docker.compose.service"}}\t{{.Names}}\t'
        '{{.State}}\t{{.Status}}\t{{.Label "' + CONFIG_FILES_LABEL + '"}}\t'
        '{{.Label "' + PROJECT_LABEL + '"}}'
    )
    result = runner(
        [docker_executable(), "ps", "--all", "--no-trunc", "--format", template],
        timeout_sec=timeout_sec,
    )
    if result.code == 124:
        return Discovery([], 0, "Docker did not answer in time.", timed_out=True)
    if result.code != 0:
        return Discovery([], 0, "Docker is not reachable.")

    project = compose_project_name(compose_file)
    owned: list[OwnedContainer] = []
    foreign = 0
    for line in result.stdout.splitlines():
        row = line.rstrip("\r\n")
        if not row.strip():
            continue
        fields = row.split("\t")
        if len(fields) != 7:
            continue
        cid, service, name, state, status, config_files, row_project = (
            f.strip() for f in fields
        )
        if not service or not config_files:
            continue
        if not _same_path(config_files, str(compose_file)):
            if project and row_project == project:
                foreign += 1
            continue
        health = "healthy" if "healthy" in status.lower() else ""
        if "unhealthy" in status.lower():
            health = "unhealthy"
        owned.append(
            OwnedContainer(
                container_id=cid, name=name, service=service, state=state, health=health
            )
        )
    return Discovery(owned, foreign, None)


def discover_owned_services(
    compose_file: Path,
    *,
    runner: CommandRunner = run_command,
    timeout_sec: int = 30,
) -> tuple[list[OwnedContainer], str | None]:
    """Backwards-compatible view of :func:`discover`."""
    found = discover(compose_file, runner=runner, timeout_sec=timeout_sec)
    return found.owned, found.problem


def _same_path(left: str, right: str) -> bool:
    import os

    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(right)
    )


@dataclass(frozen=True)
class BackendProbe:
    state: str
    detail: str
    onboarding_required: bool | None = None


def probe_backend(
    *, probe: HttpProbe = http_get, timeout_sec: float = 5.0
) -> BackendProbe:
    """Ask the Open WebUI BACKEND a question the static frontend cannot answer.

    ``/health`` can be served while the API is broken, so the decisive check is
    ``/api/config``: that is the request the browser makes on load, and the one
    that returned 500 while the page still rendered - producing Open WebUI's
    "unsupported method (frontend only)" screen. The same response carries the
    ``onboarding`` flag, so no second request is made for it.
    """
    try:
        status, body = probe(f"{WEBUI_URL}/api/config", timeout_sec=timeout_sec)
    except OSError as error:
        return BackendProbe(
            SERVICE_STARTING, f"backend not answering yet ({type(error).__name__})"
        )
    if status == 200:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return BackendProbe(
                SERVICE_DEGRADED, "backend returned an unreadable configuration"
            )
        if not isinstance(payload, dict):
            return BackendProbe(
                SERVICE_DEGRADED, "backend returned an unexpected configuration"
            )
        version = str(payload.get("version") or "unknown")[:32]
        onboarding = payload.get("onboarding")
        return BackendProbe(
            SERVICE_READY,
            f"backend healthy (Open WebUI {version})",
            onboarding if isinstance(onboarding, bool) else False,
        )
    if status >= 500:
        return BackendProbe(
            SERVICE_FAILED,
            f"backend error HTTP {status}; the page would load but chat cannot work",
        )
    return BackendProbe(SERVICE_DEGRADED, f"backend answered HTTP {status}")


def probe_webui_backend(
    *, probe: HttpProbe = http_get, timeout_sec: float = 5.0
) -> tuple[str, str]:
    result = probe_backend(probe=probe, timeout_sec=timeout_sec)
    return result.state, result.detail


def probe_ollama(
    *, probe: HttpProbe = http_get, timeout_sec: float = 5.0
) -> tuple[str, str, list[str]]:
    try:
        status, body = probe(f"{OLLAMA_URL}/api/tags", timeout_sec=timeout_sec)
    except OSError as error:
        return SERVICE_STOPPED, f"not reachable ({type(error).__name__})", []
    if status != 200:
        return SERVICE_DEGRADED, f"answered HTTP {status}", []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return SERVICE_DEGRADED, "unreadable model list", []
    rows = payload.get("models", []) if isinstance(payload, dict) else []
    models = [str(m.get("name")) for m in rows if isinstance(m, dict) and m.get("name")]
    return SERVICE_READY, "model runtime answering", models


def model_present(model: str, models: Sequence[str]) -> bool:
    return any(m == model or m == f"{model}:latest" for m in models)


# Runs INSIDE this installation's Open WebUI container: can the chat backend
# reach the model runtime the way chat will, and does it see the configured
# model? Prints a single word, never the model inventory.
_REACH_SNIPPET = (
    "import json,sys,urllib.request\n"
    "m=sys.argv[1]\n"
    "try:\n"
    " d=json.load(urllib.request.urlopen("
    "'http://host.docker.internal:11434/api/tags',timeout=8))\n"
    "except Exception as e:\n"
    " print('unreachable');sys.exit(2)\n"
    "n=[x.get('name','') for x in d.get('models',[]) if isinstance(x,dict)]\n"
    "ok=any(v==m or v==m+':latest' for v in n)\n"
    "print('visible' if ok else 'absent');sys.exit(0 if ok else 1)\n"
)


def probe_backend_reaches_model(
    container_id: str,
    model: str,
    *,
    runner: CommandRunner = run_command,
    timeout_sec: int = 25,
) -> tuple[bool, str]:
    """Exec into the PROVEN container id - never a compose service name."""
    result = runner(
        [
            docker_executable(),
            "exec",
            container_id,
            "python",
            "-c",
            _REACH_SNIPPET,
            model,
        ],
        timeout_sec=timeout_sec,
    )
    word = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if result.code == 0 and word == "visible":
        return True, "chat backend can reach the model"
    if result.code == 124:
        return False, "chat backend check timed out"
    if word == "absent":
        return False, "chat backend reaches Ollama but cannot see the model"
    if word == "unreachable":
        return False, "chat backend cannot reach Ollama"
    return False, f"chat backend check failed (exit {result.code})"


def probe_tiny_inference(
    model: str,
    *,
    poster: InferencePoster | None = None,
    timeout_sec: float = 90.0,
) -> tuple[bool, str]:
    """Generate a handful of tokens. This is what makes READY mean something."""
    post = poster or _post_json
    try:
        status, body = post(
            f"{OLLAMA_URL}/api/generate",
            {
                "model": model,
                "prompt": "Reply with the single word: ok",
                "stream": False,
                "options": {"num_predict": 8},
            },
            timeout_sec=timeout_sec,
        )
    except OSError as error:
        return False, f"inference did not run ({type(error).__name__})"
    if status != 200:
        return False, f"inference failed with HTTP {status}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False, "inference returned an unreadable response"
    if not isinstance(payload, dict):
        return False, "inference returned an unexpected response"

    # Success is "the model generated tokens", not "the model produced visible
    # prose". The shipped default is a thinking model: asked for 8 tokens it
    # spends all 8 inside its reasoning block and returns response="" with the
    # text in "thinking". Requiring response text called a working runtime
    # broken - found by running this against the real machine, not in a fixture.
    generated = payload.get("eval_count")
    tokens = generated if isinstance(generated, int) else 0
    if not payload.get("done") or tokens <= 0:
        reason = str(payload.get("done_reason") or "no tokens generated")[:40]
        return False, f"inference produced nothing ({reason})"

    text = str(payload.get("response", "")).strip()
    thinking = str(payload.get("thinking", "")).strip()
    shape = "text" if text else ("reasoning" if thinking else "tokens")
    # Only the SHAPE of the answer is reported: the generated text itself is
    # never copied into status or diagnostics.
    return True, f"inference ok ({tokens} tokens, {shape})"


def _set(status: ProductStatus, state: str, reason: str, message: str) -> ProductStatus:
    status.state = state
    status.reason = reason
    status.message = message
    return status


def collect_product_status(
    *,
    program_root: Path | str | None,
    configured_model: str | None = None,
    runner: CommandRunner = run_command,
    probe: HttpProbe = http_get,
    inference: InferencePoster | None = None,
    verify_inference: bool = True,
    timeout_sec: int = 30,
) -> ProductStatus:
    """One honest answer to "is my local AI usable right now?".

    Any unexpected failure while observing becomes UNKNOWN - never a guess.
    """
    mode = MODE_QUALIFY if verify_inference else MODE_LIVENESS
    try:
        return _collect(
            program_root=program_root,
            configured_model=configured_model,
            runner=runner,
            probe=probe,
            inference=inference,
            mode=mode,
            timeout_sec=timeout_sec,
        )
    except Exception as error:  # noqa: BLE001 - an observer must not crash
        status = ProductStatus(mode=mode, model=configured_model)
        return _set(
            status,
            UNKNOWN,
            "STATUS_ERROR",
            f"AFK AI could not check its services ({type(error).__name__}).",
        )


def _collect(
    *,
    program_root: Path | str | None,
    configured_model: str | None,
    runner: CommandRunner,
    probe: HttpProbe,
    inference: InferencePoster | None,
    mode: str,
    timeout_sec: int,
) -> ProductStatus:
    status = ProductStatus(mode=mode, model=configured_model)

    compose_file = owned_compose_file(program_root)
    if compose_file is None:
        return _set(
            status,
            NOT_INSTALLED,
            "PAYLOAD_NOT_FOUND",
            "AFK AI's own files could not be found. Reinstall AFK AI to repair it.",
        )

    found = discover(compose_file, runner=runner, timeout_sec=timeout_sec)
    if found.problem is not None:
        status.services = [ServiceStatus("Docker", SERVICE_STOPPED, found.problem)]
        if found.timed_out:
            return _set(
                status,
                UNKNOWN,
                "DOCKER_TIMEOUT",
                "Docker did not answer in time, so AFK AI cannot tell whether it "
                "is running.",
            )
        return _set(
            status,
            STOPPED,
            "DOCKER_UNREACHABLE",
            "AFK AI is stopped: Docker Desktop is not running.",
        )
    status.services.append(ServiceStatus("Docker", SERVICE_READY, "engine reachable"))

    if found.foreign_same_project:
        return _set(
            status,
            FAILED,
            "FOREIGN_PROJECT_COLLISION",
            "Another Docker project on this PC uses AFK AI's project name. AFK AI "
            "will not start or change it. Open Diagnostics for details.",
        )

    by_service = {c.service: c for c in found.owned}
    if not by_service:
        for name in REQUIRED_SERVICES + OPTIONAL_SERVICES:
            status.services.append(ServiceStatus(name, SERVICE_STOPPED, "not created"))
        return _set(status, STOPPED, "NOT_STARTED", "AFK AI is stopped.")

    # --- Open WebUI: container first, then the backend behind it.
    webui = by_service.get("open-webui")
    backend = BackendProbe(SERVICE_STOPPED, "container missing")
    if webui is not None and webui.state != "running":
        backend = BackendProbe(SERVICE_STOPPED, f"container {webui.state}")
    elif webui is not None:
        backend = probe_backend(probe=probe)
    status.services.append(ServiceStatus("Open WebUI", backend.state, backend.detail))

    for name in OPTIONAL_SERVICES:
        container = by_service.get(name)
        if container is None:
            status.services.append(
                ServiceStatus(name, SERVICE_NOT_REQUIRED, "not installed")
            )
            continue
        service_state = (
            SERVICE_READY if container.state == "running" else SERVICE_STOPPED
        )
        status.services.append(
            ServiceStatus(name, service_state, f"container {container.state}")
        )

    if backend.state == SERVICE_FAILED:
        return _set(
            status,
            DEGRADED,
            "WEBUI_BACKEND_UNAVAILABLE",
            "Chat is running but its backend is not answering, so chat will not "
            "work yet.",
        )
    if backend.state == SERVICE_STARTING:
        return _set(status, STARTING, "WEBUI_STARTING", "AFK AI is starting chat.")
    if backend.state == SERVICE_STOPPED:
        return _set(status, STOPPED, "WEBUI_NOT_RUNNING", "AFK AI's chat is stopped.")
    if backend.state != SERVICE_READY:
        return _set(
            status,
            DEGRADED,
            "WEBUI_DEGRADED",
            f"Chat is not fully healthy: {backend.detail}.",
        )
    status.onboarding_required = backend.onboarding_required

    # --- Ollama and the model.
    ollama_state, ollama_detail, models = probe_ollama(probe=probe)
    status.services.append(ServiceStatus("Ollama", ollama_state, ollama_detail))
    if ollama_state != SERVICE_READY:
        return _set(
            status,
            DEGRADED,
            "OLLAMA_UNAVAILABLE",
            "The model runtime (Ollama) is not running.",
        )

    model = configured_model
    if not model:
        status.services.append(
            ServiceStatus("Model", SERVICE_DEGRADED, "no model configured")
        )
        return _set(
            status,
            DEGRADED,
            "MODEL_NOT_CONFIGURED",
            "No chat model is configured yet. Run setup to choose one.",
        )
    if not model_present(model, models):
        status.services.append(
            ServiceStatus("Model", SERVICE_DEGRADED, "not downloaded")
        )
        return _set(
            status,
            DEGRADED,
            "MODEL_MISSING",
            f"The chat model {model} has not been downloaded yet.",
        )
    status.services.append(ServiceStatus("Model", SERVICE_READY, "available"))

    if mode == MODE_LIVENESS:
        status.services.append(
            ServiceStatus("Inference", SERVICE_NOT_CHECKED, "not run in liveness mode")
        )
        return _set(status, LIVE, "LIVE", "AFK AI is running.")

    assert webui is not None  # backend READY implies a running container
    reachable, reach_detail = probe_backend_reaches_model(
        webui.container_id, model, runner=runner
    )
    status.services.append(
        ServiceStatus(
            "Chat to model",
            SERVICE_READY if reachable else SERVICE_FAILED,
            reach_detail,
        )
    )
    if not reachable:
        return _set(
            status,
            DEGRADED,
            "BACKEND_CANNOT_REACH_OLLAMA",
            "Chat is running but cannot reach the model runtime, so it would have "
            "no model to answer with.",
        )

    ok, inference_detail = probe_tiny_inference(model, poster=inference)
    status.inference = INFERENCE_PASSED if ok else INFERENCE_FAILED
    status.inference_detail = inference_detail
    status.services.append(
        ServiceStatus(
            "Inference", SERVICE_READY if ok else SERVICE_FAILED, inference_detail
        )
    )
    if not ok:
        return _set(
            status,
            DEGRADED,
            "INFERENCE_FAILED",
            "The model did not answer a short test prompt.",
        )

    status.chat_ready = True
    message = "Your local AI is ready."
    if status.onboarding_required:
        message = (
            "Your local AI is ready. Open Chat to create the local account for this PC."
        )
    return _set(status, READY, "READY", message)
