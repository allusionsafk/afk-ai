"""A support bundle that answers "what is wrong?" without copying user content.

The previous "diagnostics" was the Windows prerequisite check's JSON and nothing
else: it could not tell a broken backend from a missing model from a stopped
stack. This bundle is built from STRUCTURED facts the product already proves,
and it is privacy-bounded by construction:

- every field is assembled from an allowlist, never by dumping a file, a
  response body, or a command's output;
- chats, prompts, documents, credentials, tokens, generated text, browser data
  and file contents are never read into it;
- other Docker projects and other Ollama models are counted at most, never named;
- a final pass redacts the user's profile path, the product's own generated
  secret, and anything shaped like a token, in case a future field slips.

``classification`` is the one-word answer support needs first.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from localai import readiness
from localai.afk_ownership import docker_executable
from localai.installation import read_runtime_identity, verify_installation
from localai.ops import CommandResult, run_command
from localai.product_config import (
    DEFAULT_MODEL_KEY,
    SEARXNG_SECRET_KEY,
    ProductLayout,
    configured_model,
    read_env_file,
    valid_model_tag,
)

SCHEMA_VERSION = 1
EVENT_LOG_NAME = "lifecycle-events.jsonl"
MAX_EVENTS = 60

PRIVACY_EXCLUSIONS = (
    "chat content",
    "prompts and generated text",
    "documents and file contents",
    "credentials, secrets and tokens",
    "browser data",
    "names of other Docker projects, containers or models",
)

KNOWN_PHASES = {
    "environment-preflight",
    "environment-ready",
    "vet",
    "intent",
    "python",
    "pip",
    "scout",
    "ollama-docker",
    "pulls",
    "compose",
    "seed",
    "secure",
    "self-test",
    "recovery",
    "start",
    "stop",
    "status",
    "provision",
    "repair",
}
_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_KNOWN_CONTEXTS = {"default", "desktop-linux", "desktop-windows"}

# Shapes that must never leave the machine even if a field slips.
_TOKEN_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|xox[abp])[-_][A-Za-z0-9_-]{16,}\b"),
    # 48+ hex: long enough for generated secrets, short of a 40-hex commit id.
    re.compile(r"\b[0-9a-fA-F]{48,}\b"),
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
)
_SHA256_REF = re.compile(r"sha256:[0-9a-f]{64}")


def _code(value: object) -> str | None:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def _number(value: object) -> float | int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int | float) else None


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    # A PowerShell-written single-element array can serialise as a bare string.
    if isinstance(value, str):
        return [value]
    return value if isinstance(value, list) else []


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# ------------------------------------------------------------------ sections


def _product(layout: ProductLayout) -> dict[str, Any]:
    version = _load_json(layout.program_root / "version.json") or {}
    return {
        "name": "AFK AI",
        "display_version": _code(version.get("display_version")),
        "channel": _code(version.get("channel")),
    }


def _runtime(layout: ProductLayout) -> dict[str, Any]:
    identity = read_runtime_identity(layout.program_root)
    executable = Path(sys.executable)
    try:
        relative = executable.resolve().relative_to(layout.program_root.resolve())
        location = relative.as_posix()
    except ValueError:
        location = "outside this installation"
    return {
        "implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "interpreter": location,
        "isolated": bool(sys.flags.isolated),
        "site_disabled": bool(sys.flags.no_site),
        "pinned_version": identity.version if identity else None,
        "pinned_sha256": f"sha256:{identity.sha256.lower()}" if identity else None,
        "matches_pin": (
            identity is not None and identity.version == platform.python_version()
        ),
    }


def _setup(layout: ProductLayout) -> dict[str, Any]:
    state = _load_json(layout.installer_state)
    if state is None:
        return {"state_present": False}
    phases = [p for p in _list(state.get("phases_done")) if p in KNOWN_PHASES]
    pending = _dict(state.get("pending_reboot"))
    preflight = _dict(state.get("preflight"))
    components = _dict(preflight.get("components"))
    hardware = _dict(state.get("hardware"))
    gpu = hardware.get("gpu")
    return {
        "state_present": True,
        "phases_done": phases,
        "restart_pending": bool(pending.get("required")),
        "restart_reason": _code(pending.get("reason")),
        "preflight": {
            "overall": _code(preflight.get("overall")),
            "code": _code(preflight.get("code")),
            "components": {
                key: _code(value)
                for key, value in components.items()
                if _code(key) and _code(value)
            },
        },
        "hardware": {
            "tier": _code(hardware.get("tier")),
            "vram_gb": _number(hardware.get("vram_gb")),
            "ram_gb": _number(hardware.get("ram_gb")),
            "cpu_cores": _number(hardware.get("cpu_cores")),
            "disk_free_gb": _number(hardware.get("disk_free_gb")),
            "gpu": str(gpu)[:80] if isinstance(gpu, str) else None,
        },
        "intent": [
            i
            for i in _list(state.get("intent"))
            if i in {"chat", "coding", "web", "voice"}
        ],
    }


def _config(layout: ProductLayout) -> dict[str, Any]:
    values = read_env_file(layout.runtime_env)
    model = values.get(DEFAULT_MODEL_KEY)
    return {
        "runtime_config_present": layout.runtime_env.is_file(),
        "configured_model": configured_model(layout),
        "runtime_config_model_valid": valid_model_tag(model) if model else None,
        # Presence only. The value is a credential.
        "service_secret_present": bool(values.get(SEARXNG_SECRET_KEY)),
        "legacy_program_folder_files": sorted(
            name
            for name in (".env", "logs", "src/localai.egg-info")
            if (layout.program_root / name).exists()
        ),
    }


def _docker(
    layout: ProductLayout, runner: Callable[..., CommandResult]
) -> dict[str, Any]:
    context = runner([docker_executable(), "context", "show"], timeout_sec=15)
    name = context.stdout.strip() if context.code == 0 else ""
    found = readiness.discover(layout.compose_file, runner=runner, timeout_sec=30)
    images: dict[str, str] = {}
    if found.owned:
        listing = runner(
            [
                docker_executable(),
                "ps",
                "--all",
                "--no-trunc",
                "--format",
                "{{.ID}}\t{{.Image}}",
            ],
            timeout_sec=30,
        )
        owned_ids = {c.container_id for c in found.owned}
        for row in listing.stdout.splitlines():
            parts = row.strip().split("\t")
            if len(parts) == 2 and parts[0] in owned_ids:
                images[parts[0]] = parts[1][:160]
    return {
        "reachable": found.problem is None,
        "problem": found.problem,
        "context": (name if name in _KNOWN_CONTEXTS else ("other" if name else None)),
        "owned_containers": [
            {
                "service": _code(c.service),
                "state": _code(c.state),
                "health": _code(c.health) if c.health else None,
                "image": images.get(c.container_id),
            }
            for c in found.owned
        ],
        "foreign_same_project_count": found.foreign_same_project,
    }


def _ollama(probe: readiness.HttpProbe, model: str | None) -> dict[str, Any]:
    version: str | None = None
    try:
        code, body = probe(f"{readiness.OLLAMA_URL}/api/version", timeout_sec=3)
        if code == 200:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                version = _code(parsed.get("version"))
    except (OSError, ValueError):
        version = None
    state, detail, models = readiness.probe_ollama(probe=probe, timeout_sec=5)
    return {
        "reachable": state == readiness.SERVICE_READY,
        "detail": detail,
        "version": version,
        "configured_model_present": (
            readiness.model_present(model, models) if model else None
        ),
    }


def _events(layout: ProductLayout) -> list[dict[str, Any]]:
    path = layout.logs_root / EVENT_LOG_NAME
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 256 * 1024))
            tail = stream.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in tail[-MAX_EVENTS * 3 :]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict) or item.get("event_type") == "output":
            continue
        events.append(
            {
                "timestamp_utc": _code(item.get("timestamp_utc")),
                "event_type": _code(item.get("event_type")),
                "phase": _code(item.get("phase")),
                "status": _code(item.get("status")),
                "code": _code(item.get("code")),
            }
        )
    return events[-MAX_EVENTS:]


def legacy_python_registrations(
    layout: ProductLayout, *, roots: Iterable[Path] | None = None
) -> list[dict[str, Any]]:
    """Find earlier setups' global ``pip install -e`` registrations of localai.

    Read-only. The previous provisioning path registered the product into the
    user's own Python, so ``import localai`` there resolved to AFK - or, on a
    machine with an engineering checkout, to that checkout. Nothing here runs
    those interpreters or changes them; the finding is reported so support can
    explain it and a person can decide.
    """
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    candidates = (
        list(roots)
        if roots is not None
        else [
            *local.glob("Programs/Python/Python3*/Lib/site-packages"),
            *local.glob("Python/pythoncore-3*/Lib/site-packages"),
            *roaming.glob("Python/Python3*/site-packages"),
        ]
    )
    program = os.path.normcase(os.path.normpath(str(layout.program_root)))
    legacy = os.path.normcase(
        os.path.normpath(os.path.join(os.path.expanduser("~"), "localai"))
    )
    findings: list[dict[str, Any]] = []
    for site in candidates:
        for pth in sorted(site.glob("__editable__.localai-*.pth")):
            try:
                target = pth.read_text(encoding="utf-8", errors="replace")[:1024]
            except OSError:
                continue
            normalized = os.path.normcase(os.path.normpath(target.strip()))
            if normalized.startswith(program):
                points_to = "this_installation"
            elif normalized.startswith(legacy):
                points_to = "legacy_install_folder"
            else:
                points_to = "elsewhere"
            owner = site.parent.parent if site.parent.name == "Lib" else site.parent
            findings.append({"interpreter": owner.name[:40], "points_to": points_to})
    return findings


def classify(
    status: dict[str, Any], integrity: dict[str, Any], setup: dict[str, Any]
) -> str:
    """One support category, in the order a human should look."""
    if integrity.get("manifest_present") and not integrity.get("intact"):
        return "corrupt_installation"
    if setup.get("restart_pending"):
        return "prerequisite_failure"
    preflight = setup.get("preflight") or {}
    if preflight.get("overall") not in (None, "READY"):
        return "prerequisite_failure"
    reason = status.get("reason")
    if reason == "FOREIGN_PROJECT_COLLISION":
        return "foreign_resource_collision"
    if reason in ("PAYLOAD_NOT_FOUND",):
        return "corrupt_installation"
    if reason in (
        "DOCKER_UNREACHABLE",
        "NOT_STARTED",
        "WEBUI_NOT_RUNNING",
        "WEBUI_STARTING",
        "OLLAMA_UNAVAILABLE",
    ):
        return "runtime_failure"
    if reason in (
        "WEBUI_BACKEND_UNAVAILABLE",
        "WEBUI_DEGRADED",
        "BACKEND_CANNOT_REACH_OLLAMA",
    ):
        return "backend_failure"
    if reason in ("MODEL_NOT_CONFIGURED", "MODEL_MISSING", "INFERENCE_FAILED"):
        return "model_failure"
    if reason in ("LIVE", "READY"):
        chat = status.get("chat") or {}
        return "chat_onboarding" if chat.get("onboarding_required") else "healthy"
    return "unknown"


# --------------------------------------------------------------------- redact


def _redactor(layout: ProductLayout) -> Callable[[str], str]:
    secret = read_env_file(layout.runtime_env).get(SEARXNG_SECRET_KEY) or ""
    legacy_secret = read_env_file(layout.legacy_env).get(SEARXNG_SECRET_KEY) or ""
    replacements: list[tuple[str, str]] = []
    for env_name, label in (
        ("LOCALAPPDATA", "%LOCALAPPDATA%"),
        ("APPDATA", "%APPDATA%"),
        ("USERPROFILE", "%USERPROFILE%"),
    ):
        value = os.environ.get(env_name)
        if value and len(value) > 3:
            replacements.append((value, label))
    home = os.path.expanduser("~")
    if home and home != "~" and len(home) > 3:
        replacements.append((home, "%USERPROFILE%"))

    def redact(text: str) -> str:
        for value in (secret, legacy_secret):
            if len(value) >= 8:
                text = text.replace(value, "[redacted-secret]")
        for value, label in replacements:
            text = re.sub(
                re.escape(value), label.replace("\\", "\\\\"), text, flags=re.IGNORECASE
            )
        refs: list[str] = []

        def keep(match: re.Match[str]) -> str:
            refs.append(match.group(0))
            return f"\0{len(refs) - 1}\0"

        text = _SHA256_REF.sub(keep, text)
        for pattern in _TOKEN_PATTERNS:
            text = pattern.sub("[redacted]", text)
        return re.sub(r"\0(\d+)\0", lambda m: refs[int(m.group(1))], text)

    return redact


def _scrub(value: Any, redact: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_scrub(item, redact) for item in value]
    if isinstance(value, dict):
        return {str(key): _scrub(item, redact) for key, item in value.items()}
    return value


# ---------------------------------------------------------------------- build


def _location(path: Path, *standard: str) -> dict[str, Any]:
    """Where a root is, without copying folder names a person chose.

    The standard per-user locations are named as such. A custom location is
    described only by the properties that break installers - spaces, non-ASCII
    characters, length - never by its path.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    expected = os.path.normcase(os.path.normpath(os.path.join(local, *standard)))
    text = str(path)
    return {
        "kind": (
            "standard"
            if local and os.path.normcase(os.path.normpath(text)) == expected
            else "custom"
        ),
        "has_spaces": " " in text,
        "has_non_ascii": not text.isascii(),
        "length": len(text),
    }


def collect_diagnostics(
    layout: ProductLayout,
    *,
    runner: Callable[..., CommandResult] = run_command,
    probe: readiness.HttpProbe = readiness.http_get,
    site_roots: Iterable[Path] | None = None,
) -> dict[str, Any]:
    model = configured_model(layout)
    integrity = verify_installation(layout.program_root).to_dict()
    setup = _setup(layout)
    # Liveness only: diagnostics must be fast and must not load a model.
    status = readiness.collect_product_status(
        program_root=layout.program_root,
        configured_model=model,
        runner=runner,
        probe=probe,
        verify_inference=False,
    ).to_dict()
    bundle: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "privacy": {"excluded": list(PRIVACY_EXCLUSIONS)},
        "classification": classify(status, integrity, setup),
        "product": _product(layout),
        "installation": {
            "program_root": _location(layout.program_root, "Programs", "AFK LocalAI"),
            "data_root": _location(layout.data_root, "AFK LocalAI"),
            "integrity": integrity,
        },
        "runtime": _runtime(layout),
        "setup": setup,
        "config": _config(layout),
        "docker": _docker(layout, runner),
        "ollama": _ollama(probe, model),
        "status": status,
        "legacy_python_registrations": legacy_python_registrations(
            layout, roots=site_roots
        ),
        "recent_events": _events(layout),
        "system": {
            "os": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version()[:40],
            "machine": platform.machine(),
        },
    }
    return dict(_scrub(bundle, _redactor(layout)))
