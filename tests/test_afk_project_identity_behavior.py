"""An AFK-owned container must match BOTH ownership keys, and AFK names its project.

Independent adversarial review of PR #24 reproduced two ownership breaks.

Legacy containers. The 0.1.7 release candidate shipped ``name: localai`` from
the same installed program folder, so its containers carry the SAME compose
config-file label as the current install under a DIFFERENT project. Deciding
ownership by path alone treated them as ours:

- current stack + exited rc1 containers read as Stopped / WEBUI_NOT_RUNNING,
  because the old ``open-webui`` row replaced the current one;
- a running rc1 chat container could be selected instead of the current one;
- Stop refused outright because it saw two project names.

Inherited environment. ``COMPOSE_PROJECT_NAME`` outranks the compose file's
``name:``, so a value left in the user's environment redirected ``up`` into
another project entirely.

The legitimate stack must win because it satisfies the full ownership
contract - never because Docker happened to list it first or last.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from localai import afk_ownership, product_runtime, readiness
from localai.ops import CommandResult
from localai.product_config import ensure_runtime_config, resolve_layout

ROOT = Path(__file__).resolve().parents[1]
MODEL = "qwen3.5:9b-32k"
CURRENT_WEBUI = "c" * 64
LEGACY_WEBUI = "1" * 64
FOREIGN_COMPOSE = r"Q:\example-other-checkout\docker-compose.yml"


def _install(tmp_path: Path) -> tuple[Path, str]:
    program_root = tmp_path / "Programs" / "AFK LocalAI"
    program_root.mkdir(parents=True)
    (program_root / "docker-compose.yml").write_text("name: afk-localai\n")
    return program_root, str((program_root / "docker-compose.yml").resolve())


def _status_row(
    cid: str, service: str, state: str, config: str, project: str
) -> str:
    status = "Up 2 minutes (healthy)" if state == "running" else "Exited (0)"
    name = f"{project}-{service}-1"
    return f"{cid}\t{service}\t{name}\t{state}\t{status}\t{config}\t{project}\n"


def _stop_row(cid: str, project: str, config: str) -> str:
    return f"{cid}\t{project}\t{config}\n"


class _Docker:
    """Answers ``docker ps`` with a fixed listing and records every call."""

    def __init__(self, listing: str) -> None:
        self.listing = listing
        self.calls: list[list[str]] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        argv = [str(a) for a in args]
        self.calls.append(argv)
        if "exec" in argv:
            return CommandResult(tuple(argv), 0, "visible\n", "")
        if "ps" in argv:
            return CommandResult(tuple(argv), 0, self.listing, "")
        return CommandResult(tuple(argv), 0, "", "")


def _probe(url: str, *, timeout_sec: float) -> tuple[int, str]:
    if "/api/tags" in url:
        return 200, json.dumps({"models": [{"name": MODEL}]})
    return 200, json.dumps({"version": "0.11.0"})


def _inference(*_args: Any, **_kwargs: Any) -> tuple[int, str]:
    return 200, json.dumps({"done": True, "eval_count": 3, "response": "ok"})


def _status(
    program_root: Path, docker: _Docker, *, mode: str = readiness.MODE_LIVENESS
) -> readiness.ProductStatus:
    return readiness.collect_product_status(
        program_root=program_root,
        configured_model=MODEL,
        runner=docker,
        probe=_probe,
        inference=_inference,
        verify_inference=mode == readiness.MODE_QUALIFY,
    )


# ------------------------------------------------------ the canonical identity


def test_the_canonical_project_is_the_one_the_shipped_compose_file_declares() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    declared = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)

    assert afk_ownership.AFK_COMPOSE_PROJECT == "afk-localai"
    assert declared and declared.group(1) == afk_ownership.AFK_COMPOSE_PROJECT


# --------------------------------------------------------- readiness discovery


def test_current_stack_alone_is_live(tmp_path: Path) -> None:
    program_root, compose = _install(tmp_path)
    docker = _Docker(
        _status_row(CURRENT_WEBUI, "open-webui", "running", compose, "afk-localai")
    )

    status = _status(program_root, docker)

    assert status.state == readiness.LIVE


def test_exited_legacy_rc1_containers_do_not_mask_the_current_stack(
    tmp_path: Path,
) -> None:
    program_root, compose = _install(tmp_path)
    # Same installed compose path, older project name, listed AFTER the current
    # container - the order that made the stale row win.
    docker = _Docker(
        _status_row(CURRENT_WEBUI, "open-webui", "running", compose, "afk-localai")
        + _status_row(LEGACY_WEBUI, "open-webui", "exited", compose, "localai")
        + _status_row("2" * 64, "searxng", "exited", compose, "localai")
    )

    status = _status(program_root, docker)

    assert status.state == readiness.LIVE
    assert status.reason == "LIVE"


def test_a_running_legacy_chat_container_is_never_selected(tmp_path: Path) -> None:
    program_root, compose = _install(tmp_path)
    for order in (0, 1):
        rows = [
            _status_row(CURRENT_WEBUI, "open-webui", "running", compose, "afk-localai"),
            _status_row(LEGACY_WEBUI, "open-webui", "running", compose, "localai"),
        ]
        docker = _Docker("".join(rows if order == 0 else reversed(rows)))

        status = _status(program_root, docker, mode=readiness.MODE_QUALIFY)

        assert status.state == readiness.READY
        execs = [call for call in docker.calls if "exec" in call]
        assert execs and all(CURRENT_WEBUI in call for call in execs)
        assert not any(LEGACY_WEBUI in call for call in docker.calls)


def test_same_path_label_under_the_wrong_project_is_not_owned(tmp_path: Path) -> None:
    program_root, compose = _install(tmp_path)
    docker = _Docker(
        _status_row(LEGACY_WEBUI, "open-webui", "running", compose, "localai")
    )

    found = readiness.discover(Path(compose), runner=docker)
    status = _status(program_root, docker)

    assert found.owned == []
    assert found.foreign_same_project == 0
    assert status.state == readiness.STOPPED
    assert status.reason == "NOT_STARTED"


def test_the_right_project_from_a_foreign_path_is_not_owned(tmp_path: Path) -> None:
    program_root, compose = _install(tmp_path)
    docker = _Docker(
        _status_row(CURRENT_WEBUI, "open-webui", "running", compose, "afk-localai")
        + _status_row("f" * 64, "open-webui", "running", FOREIGN_COMPOSE, "afk-localai")
    )

    found = readiness.discover(Path(compose), runner=docker)
    status = _status(program_root, docker)

    assert [c.container_id for c in found.owned] == [CURRENT_WEBUI]
    assert found.foreign_same_project == 1
    assert status.reason == "FOREIGN_PROJECT_COLLISION"


# ------------------------------------------------------------ stop ownership


def test_stop_stops_only_the_current_project_beside_legacy_rc1_containers(
    tmp_path: Path,
) -> None:
    program_root, compose = _install(tmp_path)
    docker = _Docker(
        _stop_row("legacy-webui", "localai", compose)
        + _stop_row("current-webui", "afk-localai", compose)
        + _stop_row("legacy-searxng", "localai", compose)
        + _stop_row("current-searxng", "afk-localai", compose)
    )

    code, lines = afk_ownership.collect_afk_stop_report(
        program_root=program_root, runner=docker
    )

    assert code == 0
    stops = [call for call in docker.calls if "stop" in call]
    assert len(stops) == 1
    assert stops[0][1:] == ["stop", "current-webui", "current-searxng"]
    assert not any("legacy" in arg for call in docker.calls for arg in call)
    assert not any("conflicting" in line for line in lines)


def test_stop_does_not_touch_same_path_containers_of_another_project(
    tmp_path: Path,
) -> None:
    program_root, compose = _install(tmp_path)
    docker = _Docker(_stop_row("legacy-webui", "localai", compose))

    code, lines = afk_ownership.collect_afk_stop_report(
        program_root=program_root, runner=docker
    )

    assert code == 0
    assert [call for call in docker.calls if "stop" in call] == []
    assert any("nothing to stop" in line for line in lines)


def test_stop_does_not_touch_our_project_name_from_a_foreign_path(
    tmp_path: Path,
) -> None:
    program_root, _compose = _install(tmp_path)
    docker = _Docker(_stop_row("impostor", "afk-localai", FOREIGN_COMPOSE))

    code, lines = afk_ownership.collect_afk_stop_report(
        program_root=program_root, runner=docker
    )

    assert code == 0
    assert [call for call in docker.calls if "stop" in call] == []
    assert any("nothing to stop" in line for line in lines)


# -------------------------------------------- inherited COMPOSE_PROJECT_NAME


def _layout(tmp_path: Path) -> Any:
    program = tmp_path / "Programs" / "AFK AI"
    program.mkdir(parents=True)
    (program / "docker-compose.yml").write_text("name: afk-localai\n", encoding="utf-8")
    return resolve_layout(program, tmp_path / "Data" / "AFK AI")


def test_compose_up_names_the_afk_project_explicitly(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    args = product_runtime.compose_up_args(layout)

    assert args[args.index("--project-name") + 1] == "afk-localai"
    assert args.index("--project-name") < args.index("up")


def test_a_hostile_inherited_project_name_cannot_redirect_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "reviewcanary")
    layout = _layout(tmp_path)
    ensure_runtime_config(layout, model=MODEL)
    compose = str(layout.compose_file.resolve())
    docker = _Docker("")
    streamed: list[tuple[list[str], dict[str, str]]] = []

    def stream(
        args: list[str], *, env: dict[str, str], **_kwargs: Any
    ) -> CommandResult:
        streamed.append((args, env))
        docker.listing = _status_row(
            CURRENT_WEBUI, "open-webui", "running", compose, "afk-localai"
        )
        return CommandResult(tuple(args), 0, "", "")

    clock = iter(float(i) for i in range(100000))
    deps = product_runtime.StartDeps(
        runner=docker,
        probe=_probe,
        inference=_inference,
        launch=lambda *_a, **_k: True,
        stream=stream,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
    )

    code, status = product_runtime.start_product(
        layout, emit=lambda *a, **k: None, deps=deps
    )

    assert code == 0 and status.state == readiness.READY
    (args, env), = streamed
    assert args[args.index("--project-name") + 1] == "afk-localai"
    assert "reviewcanary" not in args
    assert not any(key.upper() == "COMPOSE_PROJECT_NAME" for key in env)
    assert "reviewcanary" not in env.values()


def test_observation_and_stop_ignore_an_inherited_project_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "reviewcanary")
    program_root, compose = _install(tmp_path)
    listing = _status_row(
        "9" * 64, "open-webui", "running", compose, "reviewcanary"
    )
    docker = _Docker(listing)

    status = _status(program_root, docker)
    stop_docker = _Docker(_stop_row("canary", "reviewcanary", compose))
    afk_ownership.collect_afk_stop_report(program_root=program_root, runner=stop_docker)

    assert status.reason == "NOT_STARTED"
    assert [call for call in stop_docker.calls if "stop" in call] == []
