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
      and a tiny generation actually completes

Anything less is DEGRADED or STARTING, never READY. Every state carries a
reason code and a human sentence, so the shell never has to invent either.

One thing this deliberately does NOT claim: that a human has completed Open
WebUI's first-run signup. A fresh install reports ``onboarding`` and chat is
gated behind that account. Backend-and-inference readiness and human-chat
readiness are reported as separate facts rather than collapsed into one
optimistic "ready".
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from localai.afk_ownership import (
    CONFIG_FILES_LABEL,
    PROJECT_LABEL,
    docker_executable,
    owned_compose_file,
)
from localai.ops import CommandResult, run_command

# Product states. Ordered from least to most usable; the overall state is the
# weakest state any required component is in.
STOPPED = "Stopped"
STARTING = "Starting"
DEGRADED = "Degraded"
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

WEBUI_URL = "http://127.0.0.1:3000"
OLLAMA_URL = "http://127.0.0.1:11434"

# Required for the product to be usable at all. Kokoro (speech) and SearXNG
# (web search) are features; chat does not depend on them, so their absence
# degrades rather than fails.
REQUIRED_SERVICES = ("open-webui",)
OPTIONAL_SERVICES = ("searxng", "kokoro")


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
    """Returns (status_code, body) or raises."""

    def __call__(self, url: str, *, timeout_sec: float) -> tuple[int, str]: ...


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    state: str
    detail: str = ""


@dataclass
class ProductStatus:
    """A snapshot the UI can render without making a second judgement."""

    state: str = UNKNOWN
    reason: str = "UNKNOWN"
    message: str = ""
    services: list[ServiceStatus] = field(default_factory=list)
    model: str | None = None
    chat_ready: bool = False
    human_signup_complete: bool | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "state": self.state,
                "reason": self.reason,
                "message": self.message,
                "chat_ready": self.chat_ready,
                "human_signup_complete": self.human_signup_complete,
                "model": self.model,
                "services": [
                    {"name": s.name, "state": s.state, "detail": s.detail}
                    for s in self.services
                ],
            },
            sort_keys=True,
        )


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
    name: str
    service: str
    state: str
    health: str


def discover_owned_services(
    compose_file: Path,
    *,
    runner: CommandRunner = run_command,
    timeout_sec: int = 30,
) -> tuple[list[OwnedContainer], str | None]:
    """List containers this installation owns, by config-file path.

    Ownership is decided exactly the way the uninstall path decides it: the
    container's compose config-file label must be this installation's own
    compose file. A project NAME is never sufficient - another checkout on the
    same machine can claim the same one.
    """
    template = (
        "{{.Label \"com.docker.compose.service\"}}\t{{.Names}}\t{{.State}}\t"
        "{{.Status}}\t{{.Label \"" + CONFIG_FILES_LABEL + "\"}}\t"
        "{{.Label \"" + PROJECT_LABEL + "\"}}"
    )
    result = runner(
        [docker_executable(), "ps", "--all", "--format", template],
        timeout_sec=timeout_sec,
    )
    if result.code == 124:
        return [], "Docker did not answer in time."
    if result.code != 0:
        return [], "Docker is not reachable."

    owned: list[OwnedContainer] = []
    for line in result.stdout.splitlines():
        row = line.rstrip("\r\n")
        if not row.strip():
            continue
        fields = row.split("\t")
        if len(fields) != 6:
            continue
        service, name, state, status, config_files, _project = (
            f.strip() for f in fields
        )
        if not service or not config_files:
            continue
        if not _same_path(config_files, str(compose_file)):
            continue
        health = "healthy" if "healthy" in status.lower() else ""
        if "unhealthy" in status.lower():
            health = "unhealthy"
        owned.append(
            OwnedContainer(name=name, service=service, state=state, health=health)
        )
    return owned, None


def _same_path(left: str, right: str) -> bool:
    import os

    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(right)
    )


def probe_webui_backend(
    *, probe: HttpProbe = http_get, timeout_sec: float = 5.0
) -> tuple[str, str]:
    """Ask the Open WebUI BACKEND a question the static frontend cannot answer.

    ``/health`` can be served while the API is broken, so the decisive check is
    ``/api/config``: that is the request the browser makes on load, and the one
    that returned 500 while the page still rendered - producing Open WebUI's
    "unsupported method (frontend only)" screen.
    """
    try:
        status, body = probe(f"{WEBUI_URL}/api/config", timeout_sec=timeout_sec)
    except OSError as error:
        return SERVICE_STARTING, f"backend not answering yet ({type(error).__name__})"
    if status == 200:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return SERVICE_DEGRADED, "backend returned an unreadable configuration"
        version = str(payload.get("version") or "unknown")
        return SERVICE_READY, f"backend healthy (Open WebUI {version})"
    if status >= 500:
        return (
            SERVICE_FAILED,
            f"backend error HTTP {status}; the page would load but chat cannot work",
        )
    return SERVICE_DEGRADED, f"backend answered HTTP {status}"


def webui_onboarding_pending(
    *, probe: HttpProbe = http_get, timeout_sec: float = 5.0
) -> bool | None:
    """True when Open WebUI still needs its first human account."""
    try:
        status, body = probe(f"{WEBUI_URL}/api/config", timeout_sec=timeout_sec)
    except OSError:
        return None
    if status != 200:
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    onboarding = payload.get("onboarding")
    return bool(onboarding) if isinstance(onboarding, bool) else None


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
    models = [
        str(m.get("name"))
        for m in payload.get("models", [])
        if isinstance(m, dict) and m.get("name")
    ]
    return SERVICE_READY, f"{len(models)} model(s) available", models


def probe_tiny_inference(
    model: str,
    *,
    poster: Any = None,
    timeout_sec: float = 60.0,
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

    # Success is "the model generated tokens", not "the model produced visible
    # prose". The shipped default is a thinking model: asked for 8 tokens it
    # spends all 8 inside its reasoning block and returns response="" with the
    # text in "thinking". Requiring response text called a working runtime
    # broken - found by running this against the real machine, not in a fixture.
    generated = payload.get("eval_count")
    tokens = int(generated) if isinstance(generated, int) else 0
    if not payload.get("done") or tokens <= 0:
        reason = str(payload.get("done_reason") or "no tokens generated")
        return False, f"inference produced nothing ({reason})"

    text = str(payload.get("response", "")).strip()
    thinking = str(payload.get("thinking", "")).strip()
    shape = "text" if text else ("reasoning" if thinking else "tokens")
    return True, f"inference ok ({tokens} tokens, {shape})"


def collect_product_status(
    *,
    program_root: Path | str | None,
    configured_model: str | None = None,
    runner: CommandRunner = run_command,
    probe: HttpProbe = http_get,
    inference: Any = None,
    verify_inference: bool = True,
    timeout_sec: int = 30,
) -> ProductStatus:
    """One honest answer to "is my local AI usable right now?".

    The overall state is the weakest state of anything chat depends on. It is
    never allowed to be better than the Open WebUI backend's state, which is the
    specific mistake that shipped: prerequisites green, process exited 0,
    product unusable.
    """
    status = ProductStatus()

    compose_file = owned_compose_file(program_root)
    if compose_file is None:
        status.state = NOT_INSTALLED
        status.reason = "PAYLOAD_NOT_FOUND"
        status.message = "AFK LocalAI's own files could not be found."
        return status

    containers, docker_problem = discover_owned_services(
        compose_file, runner=runner, timeout_sec=timeout_sec
    )
    if docker_problem is not None:
        status.state = STOPPED
        status.reason = "DOCKER_UNREACHABLE"
        status.message = f"Docker is not available. {docker_problem}"
        status.services = [ServiceStatus("Docker", SERVICE_STOPPED, docker_problem)]
        return status

    status.services.append(ServiceStatus("Docker", SERVICE_READY, "engine reachable"))

    by_service = {c.service: c for c in containers}
    if not by_service:
        status.state = STOPPED
        status.reason = "NOT_STARTED"
        status.message = "AFK LocalAI is not running yet."
        for name in REQUIRED_SERVICES + OPTIONAL_SERVICES:
            status.services.append(ServiceStatus(name, SERVICE_STOPPED, "not created"))
        return status

    # --- Open WebUI: container first, then the backend behind it.
    webui = by_service.get("open-webui")
    backend_state = SERVICE_STOPPED
    backend_detail = "not created"
    if webui is None:
        backend_detail = "container missing"
    elif webui.state != "running":
        backend_state = SERVICE_STOPPED
        backend_detail = f"container {webui.state}"
    else:
        backend_state, backend_detail = probe_webui_backend(probe=probe)
    status.services.append(ServiceStatus("Open WebUI", backend_state, backend_detail))

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

    # --- Ollama and the model.
    ollama_state, ollama_detail, models = probe_ollama(probe=probe)
    status.services.append(ServiceStatus("Ollama", ollama_state, ollama_detail))

    model = configured_model
    status.model = model
    model_state = SERVICE_NOT_REQUIRED
    model_detail = "no model configured"
    model_present = False
    if model:
        model_present = any(m == model or m.startswith(f"{model}:") for m in models)
        model_state = SERVICE_READY if model_present else SERVICE_DEGRADED
        model_detail = "available" if model_present else f"{model} not pulled yet"
    status.services.append(ServiceStatus("Model", model_state, model_detail))

    # --- The decisive verdict.
    if backend_state == SERVICE_FAILED:
        status.state = DEGRADED
        status.reason = "WEBUI_BACKEND_UNAVAILABLE"
        status.message = (
            "Open WebUI is running but its backend is not answering, so chat "
            "will not work yet."
        )
        return status
    if backend_state in (SERVICE_STARTING, SERVICE_STOPPED):
        status.state = STARTING if backend_state == SERVICE_STARTING else STOPPED
        status.reason = "WEBUI_NOT_READY"
        status.message = (
            "Open WebUI is still starting."
            if backend_state == SERVICE_STARTING
            else "Open WebUI is not running."
        )
        return status
    if backend_state != SERVICE_READY:
        status.state = DEGRADED
        status.reason = "WEBUI_DEGRADED"
        status.message = f"Open WebUI is not fully healthy: {backend_detail}."
        return status

    if ollama_state != SERVICE_READY:
        status.state = DEGRADED
        status.reason = "OLLAMA_UNAVAILABLE"
        status.message = f"The model runtime is not available: {ollama_detail}."
        return status

    if model and not model_present:
        status.state = DEGRADED
        status.reason = "MODEL_MISSING"
        status.message = f"The configured model {model} has not been downloaded yet."
        return status

    if verify_inference and model:
        ok, inference_detail = probe_tiny_inference(model, poster=inference)
        if not ok:
            status.state = DEGRADED
            status.reason = "INFERENCE_FAILED"
            status.message = (
                f"The model did not answer a test prompt: {inference_detail}."
            )
            return status
        status.services.append(
            ServiceStatus("Inference", SERVICE_READY, inference_detail)
        )

    status.state = READY
    status.reason = "READY"
    status.chat_ready = True
    pending = webui_onboarding_pending(probe=probe)
    status.human_signup_complete = None if pending is None else (not pending)
    status.message = (
        "Your local AI is ready."
        if status.human_signup_complete is not False
        else (
            "Your local AI is ready. Open WebUI will ask you to create a "
            "local account."
        )
    )
    return status
