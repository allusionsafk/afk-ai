"""READY must mean the local AI is usable, not that Windows is fine.

The failure these pin is not hypothetical. A real install reported every
prerequisite green, exited setup successfully, said "Your local AI is ready",
and opened a chat page that showed Open WebUI's "Backend Required (frontend
only)" screen - because the Open WebUI backend was returning HTTP 500 from
/api/config and nothing in the readiness path had ever asked it anything.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from localai import readiness
from localai.ops import CommandResult

AFK_MODEL = "qwen3.5:9b-32k"
WEBUI_ID = "a" * 64


def _install(tmp_path: Path) -> Path:
    program_root = tmp_path / "Programs" / "AFK LocalAI"
    program_root.mkdir(parents=True)
    (program_root / "docker-compose.yml").write_text("name: afk-localai\n")
    return program_root


def _ps_row(
    service: str,
    name: str,
    state: str,
    status: str,
    config: str,
    *,
    cid: str | None = None,
    project: str = "afk-localai",
) -> str:
    container = cid or (service[0] * 64)
    return f"{container}\t{service}\t{name}\t{state}\t{status}\t{config}\t{project}\n"


def _listing(compose: str, *, running: bool = True) -> str:
    state = "running" if running else "exited"
    status = "Up 2 minutes (healthy)" if running else "Exited (0) 1 minute ago"
    return (
        _ps_row(
            "open-webui",
            "afk-localai-open-webui-1",
            state,
            status,
            compose,
            cid=WEBUI_ID,
        )
        + _ps_row("searxng", "afk-localai-searxng-1", state, status, compose)
        + _ps_row("kokoro", "afk-localai-kokoro-1", state, status, compose)
    )


def _runner_for(
    compose: str,
    *,
    running: bool = True,
    listing: str | None = None,
    reach: tuple[int, str] = (0, "visible\n"),
    ps_code: int = 0,
    calls: list[list[str]] | None = None,
) -> Any:
    rows = listing if listing is not None else _listing(compose, running=running)

    def runner(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        argv = [str(a) for a in args]
        if calls is not None:
            calls.append(argv)
        if "exec" in argv:
            return CommandResult(tuple(argv), reach[0], reach[1], "")
        return CommandResult(tuple(argv), ps_code, rows if ps_code == 0 else "", "")

    return runner


def _probe(
    *,
    config_status: int = 200,
    config_body: str | None = None,
    tags_status: int = 200,
    models: list[str] | None = None,
) -> Any:
    if config_body is None:
        config_body = json.dumps({"version": "0.11.0"})
    tags = {"models": [{"name": m} for m in (models or [AFK_MODEL])]}

    def probe(url: str, *, timeout_sec: float) -> tuple[int, str]:
        if "/api/config" in url:
            return config_status, config_body
        if "/api/tags" in url:
            return tags_status, json.dumps(tags)
        return 200, "{}"

    return probe


def _inference(
    ok: bool = True, *, thinking_only: bool = False, text: str = "ok"
) -> Any:
    def post(
        url: str, payload: dict[str, Any], *, timeout_sec: float
    ) -> tuple[int, str]:
        if not ok:
            return 500, "{}"
        if thinking_only:
            # The shipped default model spends short budgets entirely inside
            # its reasoning block, returning response="".
            return 200, json.dumps(
                {
                    "done": True,
                    "eval_count": 8,
                    "response": "",
                    "thinking": "Thinking Process:",
                    "done_reason": "length",
                }
            )
        return 200, json.dumps({"done": True, "eval_count": 5, "response": text})

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
    assert broken.to_dict()["chat"]["url"] is None
    assert healthy.chat_ready is True
    assert healthy.to_dict()["chat"]["url"] == "http://127.0.0.1:3000/"


def test_prerequisites_alone_cannot_produce_ready(tmp_path: Path) -> None:
    """Docker reachable but nothing running is not READY."""
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())

    status = _status(program_root, runner=_runner_for(compose, running=False))

    assert status.state != readiness.READY
    assert status.chat_ready is False
    assert status.reason == "WEBUI_NOT_RUNNING"
    assert status.next_action == "start"


def test_backend_failure_names_the_failing_service(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    status = _status(program_root, probe=_probe(config_status=500))
    webui = next(s for s in status.services if s.name == "Open WebUI")

    assert webui.state == readiness.SERVICE_FAILED
    assert "500" in webui.detail


# ------------------------------------------------------ liveness vs qualification


def test_liveness_can_never_produce_ready(tmp_path: Path) -> None:
    """Cheap evidence may keep a READY the shell already has; it never creates one."""
    program_root = _install(tmp_path)

    def must_not_run(*_args: Any, **_kwargs: Any) -> tuple[int, str]:
        raise AssertionError("liveness must not run inference")

    status = _status(program_root, inference=must_not_run, verify_inference=False)

    assert status.state == readiness.LIVE
    assert status.chat_ready is False
    assert status.to_dict()["chat"]["url"] is None
    assert status.inference == readiness.INFERENCE_NOT_RUN
    assert status.mode == readiness.MODE_LIVENESS


def test_liveness_never_execs_into_the_container(tmp_path: Path) -> None:
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())
    calls: list[list[str]] = []

    _status(
        program_root, runner=_runner_for(compose, calls=calls), verify_inference=False
    )

    assert len(calls) == 1
    assert "exec" not in calls[0]


def test_liveness_still_reports_regressions(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    status = _status(
        program_root, probe=_probe(tags_status=503), verify_inference=False
    )

    assert status.state == readiness.DEGRADED
    assert status.reason == "OLLAMA_UNAVAILABLE"


# ------------------------------------------------------------------- the rest


def test_a_healthy_stack_is_ready(tmp_path: Path) -> None:
    status = _status(_install(tmp_path))

    assert status.state == readiness.READY
    assert status.reason == "READY"
    assert status.chat_ready is True
    assert status.inference == readiness.INFERENCE_PASSED
    assert status.next_action == "open_chat"


def test_inference_participates_in_readiness(tmp_path: Path) -> None:
    """Everything green but the model cannot answer -> not READY."""
    program_root = _install(tmp_path)

    status = _status(program_root, inference=_inference(ok=False))

    assert status.state == readiness.DEGRADED
    assert status.reason == "INFERENCE_FAILED"
    assert status.chat_ready is False
    assert status.inference == readiness.INFERENCE_FAILED


def test_the_chat_backend_must_reach_the_model(tmp_path: Path) -> None:
    """Host Ollama answering is not enough: chat talks to it from the container."""
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())

    unreachable = _status(
        program_root, runner=_runner_for(compose, reach=(2, "unreachable\n"))
    )
    invisible = _status(
        program_root, runner=_runner_for(compose, reach=(1, "absent\n"))
    )

    for status in (unreachable, invisible):
        assert status.state == readiness.DEGRADED
        assert status.reason == "BACKEND_CANNOT_REACH_OLLAMA"
        assert status.chat_ready is False
    assert "cannot reach" in next(
        s.detail for s in unreachable.services if s.name.startswith("Chat")
    )


def test_the_reachability_exec_targets_the_proven_container_id(tmp_path: Path) -> None:
    """Never ``compose exec <service>``: a project name is weaker than the proof."""
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())
    calls: list[list[str]] = []

    _status(program_root, runner=_runner_for(compose, calls=calls))

    execs = [c for c in calls if "exec" in c]
    assert len(execs) == 1
    assert WEBUI_ID in execs[0]
    assert "compose" not in execs[0]
    assert execs[0][-1] == AFK_MODEL


def test_a_missing_model_is_not_ready(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    status = _status(program_root, probe=_probe(models=["something-else:7b"]))

    assert status.state == readiness.DEGRADED
    assert status.reason == "MODEL_MISSING"
    assert AFK_MODEL in status.message


def test_a_different_tag_of_the_same_model_is_not_the_configured_model(
    tmp_path: Path,
) -> None:
    program_root = _install(tmp_path)

    status = _status(
        program_root, configured_model="llama3", probe=_probe(models=["llama3:70b"])
    )

    assert status.reason == "MODEL_MISSING"


def test_no_configured_model_is_not_ready(tmp_path: Path) -> None:
    """READY promises a specific model answered; without one nothing was proven."""
    program_root = _install(tmp_path)

    status = _status(program_root, configured_model=None)

    assert status.state == readiness.DEGRADED
    assert status.reason == "MODEL_NOT_CONFIGURED"
    assert status.chat_ready is False


def test_unreachable_ollama_is_not_ready(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    status = _status(program_root, probe=_probe(tags_status=503))

    assert status.state == readiness.DEGRADED
    assert status.reason == "OLLAMA_UNAVAILABLE"


def test_a_missing_payload_reports_not_installed(tmp_path: Path) -> None:
    status = readiness.collect_product_status(program_root=tmp_path / "nope")

    assert status.state == readiness.NOT_INSTALLED
    assert status.reason == "PAYLOAD_NOT_FOUND"
    assert status.next_action == "repair"


def test_unreachable_docker_is_reported_honestly(tmp_path: Path) -> None:
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())

    status = _status(program_root, runner=_runner_for(compose, ps_code=1))

    assert status.state == readiness.STOPPED
    assert status.reason == "DOCKER_UNREACHABLE"


def test_a_docker_that_hangs_is_unknown_not_stopped(tmp_path: Path) -> None:
    """A timeout proves nothing either way; it must not be reported as a fact."""
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())

    status = _status(program_root, runner=_runner_for(compose, ps_code=124))

    assert status.state == readiness.UNKNOWN
    assert status.reason == "DOCKER_TIMEOUT"
    assert status.chat_ready is False


def test_an_observer_crash_becomes_unknown_not_ready(tmp_path: Path) -> None:
    program_root = _install(tmp_path)

    def exploding(url: str, *, timeout_sec: float) -> tuple[int, str]:
        raise RuntimeError("probe bug")

    status = _status(program_root, probe=exploding)

    assert status.state == readiness.UNKNOWN
    assert status.reason == "STATUS_ERROR"
    assert status.chat_ready is False
    assert "probe bug" not in status.message


def test_only_this_installation_s_containers_are_considered(tmp_path: Path) -> None:
    """A foreign checkout's containers must not make this install look ready."""
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())
    foreign = r"Q:\example-other-checkout\docker-compose.yml"
    listing = _ps_row(
        "open-webui",
        "localai-open-webui-1",
        "running",
        "Up (healthy)",
        foreign,
        project="localai",
    ) + _ps_row(
        "searxng", "localai-searxng-1", "running", "Up", foreign, project="localai"
    )

    status = _status(program_root, runner=_runner_for(compose, listing=listing))

    assert status.state == readiness.STOPPED
    assert status.reason == "NOT_STARTED"


def test_a_foreign_stack_claiming_our_project_name_blocks_readiness(
    tmp_path: Path,
) -> None:
    """Compose would adopt it on ``up``; that is a collision, never a READY."""
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())
    foreign = r"Q:\impostor\docker-compose.yml"
    listing = _listing(compose) + _ps_row(
        "open-webui",
        "impostor-webui",
        "running",
        "Up (healthy)",
        foreign,
        cid="f" * 64,
    )

    status = _status(program_root, runner=_runner_for(compose, listing=listing))

    assert status.state == readiness.FAILED
    assert status.reason == "FOREIGN_PROJECT_COLLISION"
    assert status.chat_ready is False
    assert "impostor" not in status.to_json()


def test_onboarding_is_reported_separately_from_backend_readiness(
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
    assert status.onboarding_required is True
    assert status.to_dict()["chat"]["onboarding_required"] is True
    assert "account" in status.message.lower()


def test_a_completed_signup_needs_no_onboarding(tmp_path: Path) -> None:
    """Open WebUI omits the flag once an account exists."""
    status = _status(_install(tmp_path))

    assert status.onboarding_required is False


def test_status_serialises_for_the_shell(tmp_path: Path) -> None:
    status = _status(_install(tmp_path))

    payload = json.loads(status.to_json())

    assert payload["schema_version"] == 2
    assert payload["state"] == readiness.READY
    assert payload["chat"]["ready"] is True
    assert payload["next_action"] == "open_chat"
    assert payload["qualification"]["inference"] == "passed"
    assert any(s["name"] == "Open WebUI" for s in payload["services"])


def test_generated_text_is_never_copied_into_status(tmp_path: Path) -> None:
    canary = "CANARY-GENERATED-SECRET-TEXT"

    status = _status(_install(tmp_path), inference=_inference(text=canary))

    assert status.state == readiness.READY
    assert canary not in status.to_json()


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


def test_every_reason_the_collector_emits_has_a_next_action() -> None:
    """The shell renders next_action; an unmapped reason would silently fall back."""
    source = Path(readiness.__file__).read_text(encoding="utf-8")
    emitted = set(re.findall(r'_set\(\s*status,\s*\w+,\s*"([A-Z_]+)"', source))

    assert emitted
    assert emitted <= set(readiness.NEXT_ACTION)
    assert set(readiness.NEXT_ACTION.values()) <= {
        "open_chat",
        "check",
        "start",
        "wait",
        "repair",
        "diagnostics",
    }
