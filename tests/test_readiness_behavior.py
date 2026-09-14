"""READY must mean the local AI is usable, not that Windows is fine.

The failure these pin is not hypothetical. A real install reported every
prerequisite green, exited setup successfully, said "Your local AI is ready",
and opened a chat page that showed Open WebUI's "Backend Required (frontend
only)" screen - because the Open WebUI backend was returning HTTP 500 from
/api/config and nothing in the readiness path had ever asked it anything.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from localai import readiness
from localai.ops import CommandResult

AFK_MODEL = "qwen3.5:9b-32k"


def _install(tmp_path: Path) -> Path:
    program_root = tmp_path / "Programs" / "AFK LocalAI"
    program_root.mkdir(parents=True)
    (program_root / "docker-compose.yml").write_text("name: afk-localai\n")
    return program_root


def _ps_row(service: str, name: str, state: str, status: str, config: str) -> str:
    return f"{service}\t{name}\t{state}\t{status}\t{config}\t afk-localai\n"


def _runner_for(compose: str, *, running: bool = True) -> Any:
    state = "running" if running else "exited"
    status = "Up 2 minutes (healthy)" if running else "Exited (0) 1 minute ago"
    listing = (
        _ps_row("open-webui", "afk-localai-open-webui-1", state, status, compose)
        + _ps_row("searxng", "afk-localai-searxng-1", state, status, compose)
        + _ps_row("kokoro", "afk-localai-kokoro-1", state, status, compose)
    )

    def runner(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        return CommandResult(tuple(str(a) for a in args), 0, listing, "")

    return runner


def _probe(
    *,
    config_status: int = 200,
    config_body: str | None = None,
    tags_status: int = 200,
    models: list[str] | None = None,
) -> Any:
    if config_body is None:
        config_body = json.dumps({"version": "0.11.0", "onboarding": False})
    tags = {"models": [{"name": m} for m in (models or [AFK_MODEL])]}

    def probe(url: str, *, timeout_sec: float) -> tuple[int, str]:
        if "/api/config" in url:
            return config_status, config_body
        if "/api/tags" in url:
            return tags_status, json.dumps(tags)
        return 200, "{}"

    return probe


def _inference(ok: bool = True, *, thinking_only: bool = False) -> Any:
    def post(
        url: str, payload: dict[str, Any], *, timeout_sec: float
    ) -> tuple[int, str]:
        if not ok:
            return 500, "{}"
        if thinking_only:
            # The shipped default model spends short budgets entirely inside
            # its reasoning block, returning response="".
            return 200, json.dumps(
                {"done": True, "eval_count": 8, "response": "",
                 "thinking": "Thinking Process:", "done_reason": "length"}
            )
        return 200, json.dumps({"done": True, "eval_count": 5, "response": "ok"})

    return post


def _status(program_root: Path, **kwargs: Any) -> readiness.ProductStatus:
    compose = str((program_root / "docker-compose.yml").resolve())
    defaults: dict[str, Any] = {
        "program_root": program_root,
        "configured_model": AFK_MODEL,
        "runner": _runner_for(compose),
        "probe": _probe(),
        "inference": _inference(),
    }
    defaults.update(kwargs)
    return readiness.collect_product_status(**defaults)


# --------------------------------------------------------------- the regression


def test_a_broken_backend_is_never_ready(tmp_path: Path) -> None:
    """THE regression: frontend reachable, backend 500 -> must not be READY."""
    program_root = _install(tmp_path)

    status = _status(
        program_root,
        probe=_probe(config_status=500, config_body="Internal Server Error"),
    )

    assert status.state != readiness.READY
    assert status.state == readiness.DEGRADED
    assert status.reason == "WEBUI_BACKEND_UNAVAILABLE"
    assert status.chat_ready is False
    assert "chat will not work" in status.message.lower()


def test_open_chat_is_gated_on_the_backend(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    broken = _status(program_root, probe=_probe(config_status=500))
    healthy = _status(program_root)

    assert broken.chat_ready is False
    assert healthy.chat_ready is True


def test_prerequisites_alone_cannot_produce_ready(tmp_path: Path) -> None:
    """Docker reachable but nothing running is not READY."""
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())

    status = _status(program_root, runner=_runner_for(compose, running=False))

    assert status.state != readiness.READY
    assert status.chat_ready is False


def test_backend_failure_names_the_failing_service(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    status = _status(program_root, probe=_probe(config_status=500))
    webui = next(s for s in status.services if s.name == "Open WebUI")

    assert webui.state == readiness.SERVICE_FAILED
    assert "500" in webui.detail


# ------------------------------------------------------------------- the rest


def test_a_healthy_stack_is_ready(tmp_path: Path) -> None:
    status = _status(_install(tmp_path))

    assert status.state == readiness.READY
    assert status.reason == "READY"
    assert status.chat_ready is True


def test_inference_participates_in_readiness(tmp_path: Path) -> None:
    """Everything green but the model cannot answer -> not READY."""
    program_root = _install(tmp_path)

    status = _status(program_root, inference=_inference(ok=False))

    assert status.state == readiness.DEGRADED
    assert status.reason == "INFERENCE_FAILED"
    assert status.chat_ready is False


def test_a_missing_model_is_not_ready(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    status = _status(program_root, probe=_probe(models=["something-else:7b"]))

    assert status.state == readiness.DEGRADED
    assert status.reason == "MODEL_MISSING"
    assert AFK_MODEL in status.message


def test_unreachable_ollama_is_not_ready(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    status = _status(program_root, probe=_probe(tags_status=503))

    assert status.state == readiness.DEGRADED
    assert status.reason == "OLLAMA_UNAVAILABLE"


def test_a_missing_payload_reports_not_installed(tmp_path: Path) -> None:
    status = readiness.collect_product_status(program_root=tmp_path / "nope")

    assert status.state == readiness.NOT_INSTALLED
    assert status.reason == "PAYLOAD_NOT_FOUND"


def test_unreachable_docker_is_reported_honestly(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    def runner(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        return CommandResult(tuple(str(a) for a in args), 1, "", "cannot connect")

    status = _status(program_root, runner=runner)

    assert status.state == readiness.STOPPED
    assert status.reason == "DOCKER_UNREACHABLE"


def test_only_this_installation_s_containers_are_considered(tmp_path: Path) -> None:
    """A foreign checkout's containers must not make this install look ready."""
    program_root = _install(tmp_path)
    foreign = r"Q:\example-other-checkout\docker-compose.yml"
    listing = (
        _ps_row(
            "open-webui", "localai-open-webui-1", "running", "Up (healthy)", foreign
        )
        + _ps_row("searxng", "localai-searxng-1", "running", "Up", foreign)
    )

    def runner(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        return CommandResult(tuple(str(a) for a in args), 0, listing, "")

    status = _status(program_root, runner=runner)

    assert status.state == readiness.STOPPED
    assert status.reason == "NOT_STARTED"


def test_human_signup_is_reported_separately_from_backend_readiness(
    tmp_path: Path,
) -> None:
    """Backend+inference ready is not the same claim as "a human can chat"."""
    program_root = _install(tmp_path)

    status = _status(
        program_root,
        probe=_probe(config_body=json.dumps({"version": "0.11.0", "onboarding": True})),
    )

    assert status.state == readiness.READY
    assert status.chat_ready is True
    assert status.human_signup_complete is False
    assert "account" in status.message.lower()


def test_status_serialises_for_the_shell(tmp_path: Path) -> None:
    status = _status(_install(tmp_path))

    payload = json.loads(status.to_json())

    assert payload["state"] == readiness.READY
    assert payload["chat_ready"] is True
    assert any(s["name"] == "Open WebUI" for s in payload["services"])


def test_a_thinking_model_still_counts_as_working_inference(tmp_path: Path) -> None:
    """Tokens generated is the criterion, not visible prose.

    Found on the real machine: qwen3.5:9b-32k asked for 8 tokens returns
    done=true, eval_count=8, response="" and the text in "thinking". Demanding
    response text reported a perfectly working runtime as broken.
    """
    program_root = _install(tmp_path)

    status = _status(program_root, inference=_inference(thinking_only=True))

    assert status.state == readiness.READY
    assert status.chat_ready is True
    inference = next(s for s in status.services if s.name == "Inference")
    assert "8 tokens" in inference.detail
    assert "reasoning" in inference.detail


def test_a_model_that_generates_nothing_is_not_ready(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    def post(
        url: str, payload: dict[str, Any], *, timeout_sec: float
    ) -> tuple[int, str]:
        return 200, json.dumps({"done": True, "eval_count": 0, "response": ""})

    status = _status(program_root, inference=post)

    assert status.state == readiness.DEGRADED
    assert status.reason == "INFERENCE_FAILED"
