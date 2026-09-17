"""Diagnostics must help support without copying anything that belongs to the user.

Every source the bundle reads from is seeded with a CANARY string. The privacy
boundary holds only if no canary - and no secret - reaches the output, while the
facts support actually needs are still there.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from localai import diagnostics
from localai.ops import CommandResult
from localai.product_config import resolve_layout

MODEL = "qwen3.5:9b-32k"
SECRET = "c0ffee" * 8  # 48 hex characters, the shape setup generates
LEGACY_SECRET = "beef" * 12


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    profile = tmp_path / "Users" / "CANARY-USERNAME"
    local = profile / "AppData" / "Local"
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(profile / "AppData" / "Roaming"))
    monkeypatch.setenv("HOME", str(profile))

    program = local / "Programs" / "AFK LocalAI"
    program.mkdir(parents=True)
    (program / "docker-compose.yml").write_text("name: afk-localai\n", encoding="utf-8")
    (program / "version.json").write_text(
        json.dumps({"display_version": "0.3.0-rc1", "channel": "prerelease"})
    )
    (program / ".env").write_text(f"SEARXNG_SECRET={LEGACY_SECRET}\n")
    layout = resolve_layout(program, local / "AFK LocalAI")

    layout.config_root.mkdir(parents=True)
    layout.runtime_env.write_text(
        f"AFK_DEFAULT_MODEL={MODEL}\nSEARXNG_SECRET={SECRET}\n", encoding="utf-8"
    )
    layout.state_root.mkdir(parents=True)
    layout.installer_state.write_text(
        json.dumps(
            {
                "phases_done": ["vet", "pulls", "CANARY-PHASE"],
                "hardware": {"tier": "A", "vram_gb": 12.0, "gpu": "Example GPU"},
                "intent": ["chat", "CANARY-INTENT"],
                "models": {"chat": {"tag": MODEL}},
                "pending_reboot": {"required": False, "reason": None},
                "preflight": {
                    "overall": "READY",
                    "code": "PREFLIGHT-READY",
                    "components": {"docker": "DOCKER_HEALTHY_LOCAL"},
                },
                "chat_history": "CANARY-CHAT-CONTENT",
                "api_token": "CANARY-TOKEN",
            }
        ),
        encoding="utf-8",
    )
    layout.logs_root.mkdir(parents=True)
    events = [
        {
            "timestamp_utc": "2026-09-16T10:00:00Z",
            "event_type": "phase-start",
            "phase": "pulls",
            "status": "running",
            "code": "phase-start",
            "message": "CANARY-PROMPT tell me about my medical results",
        },
        {
            "timestamp_utc": "2026-09-16T10:00:01Z",
            "event_type": "output",
            "phase": "start",
            "status": "running",
            "code": "compose",
            "message": "CANARY-OUTPUT-LINE",
        },
    ]
    (layout.logs_root / diagnostics.EVENT_LOG_NAME).write_text(
        "\n".join(json.dumps(e) for e in events) + "\nnot json CANARY-GARBAGE\n",
        encoding="utf-8",
    )

    site = tmp_path / "site" / "Python312" / "Lib" / "site-packages"
    site.mkdir(parents=True)
    (site / "__editable__.localai-0.2.0rc1.pth").write_text(
        str(program / "src") + "\n", encoding="utf-8"
    )
    other = tmp_path / "site2" / "Python314" / "Lib" / "site-packages"
    other.mkdir(parents=True)
    (other / "__editable__.localai-9.9.9.pth").write_text(
        "Q:\\CANARY-PRIVATE-CHECKOUT\\src\n", encoding="utf-8"
    )

    compose = str(layout.compose_file.resolve())

    def runner(args: Sequence[str], **_kwargs: Any) -> CommandResult:
        argv = [str(a) for a in args]
        if "context" in argv:
            return CommandResult(tuple(argv), 0, "CANARY-REMOTE-CONTEXT\n", "")
        if "{{.ID}}\t{{.Image}}" in argv:
            rows = (
                f"{'a' * 64}\tghcr.io/open-webui/open-webui@sha256:{'1' * 64}\n"
                f"{'f' * 64}\tCANARY-PRIVATE-IMAGE:latest\n"
            )
            return CommandResult(tuple(argv), 0, rows, "")
        rows = (
            f"{'a' * 64}\topen-webui\tafk-localai-open-webui-1\trunning\t"
            f"Up (healthy)\t{compose}\tafk-localai\n"
            f"{'f' * 64}\tweb\tCANARY-FOREIGN-CONTAINER\trunning\tUp\t"
            f"Q:\\CANARY-OTHER\\docker-compose.yml\tafk-localai\n"
            f"{'e' * 64}\tdb\tCANARY-UNRELATED\trunning\tUp\tQ:\\x.yml\tunrelated\n"
        )
        return CommandResult(tuple(argv), 0, rows, "")

    def probe(url: str, *, timeout_sec: float) -> tuple[int, str]:
        if url.endswith("/api/version"):
            return 200, json.dumps({"version": "0.12.3"})
        if url.endswith("/api/tags"):
            return 200, json.dumps(
                {"models": [{"name": MODEL}, {"name": "CANARY-PRIVATE-MODEL:7b"}]}
            )
        if url.endswith("/api/config"):
            return 200, json.dumps(
                {
                    "version": "0.11.0",
                    "name": "CANARY-OWNER-NAME",
                    "default_prompt_suggestions": ["CANARY-SUGGESTION"],
                }
            )
        return 404, "{}"

    return {
        "layout": layout,
        "runner": runner,
        "probe": probe,
        "sites": [site, other],
        "profile": profile,
    }


def _bundle(machine: dict[str, Any]) -> dict[str, Any]:
    return diagnostics.collect_diagnostics(
        machine["layout"],
        runner=machine["runner"],
        probe=machine["probe"],
        site_roots=machine["sites"],
    )


def test_no_user_content_credential_or_foreign_inventory_leaves_the_machine(
    machine: dict[str, Any],
) -> None:
    text = json.dumps(_bundle(machine))

    assert "CANARY" not in text
    assert SECRET not in text
    assert LEGACY_SECRET not in text
    assert str(machine["profile"]) not in text


def test_install_locations_are_described_not_copied(
    machine: dict[str, Any], tmp_path: Path
) -> None:
    """Found on a real machine: echoed roots carried folder names a person chose."""
    standard = _bundle(machine)["installation"]
    assert standard["program_root"]["kind"] == "standard"
    assert standard["data_root"]["kind"] == "standard"

    custom_program = tmp_path / "CANARY Private Projects" / "AFK"
    custom_program.mkdir(parents=True)
    (custom_program / "docker-compose.yml").write_text("name: afk-localai\n")
    layout = resolve_layout(custom_program, tmp_path / "CANARY-data-ünï")
    bundle = diagnostics.collect_diagnostics(
        layout, runner=machine["runner"], probe=machine["probe"], site_roots=[]
    )

    assert "CANARY" not in json.dumps(bundle)
    assert bundle["installation"]["program_root"] == {
        "kind": "custom",
        "has_spaces": True,
        "has_non_ascii": False,
        "length": len(str(custom_program)),
    }
    assert bundle["installation"]["data_root"]["has_non_ascii"] is True


def test_the_facts_support_needs_are_still_present(machine: dict[str, Any]) -> None:
    bundle = _bundle(machine)

    assert bundle["classification"] == "foreign_resource_collision"
    assert bundle["docker"]["foreign_same_project_count"] == 1
    assert bundle["docker"]["owned_containers"][0]["service"] == "open-webui"
    assert bundle["docker"]["owned_containers"][0]["image"].endswith("1" * 64)
    assert bundle["docker"]["context"] == "other"
    assert bundle["ollama"]["configured_model_present"] is True
    assert bundle["ollama"]["version"] == "0.12.3"
    assert bundle["config"]["configured_model"] == MODEL
    assert bundle["config"]["service_secret_present"] is True
    assert bundle["config"]["legacy_program_folder_files"] == [".env"]
    assert bundle["setup"]["phases_done"] == ["vet", "pulls"]
    assert bundle["setup"]["hardware"]["tier"] == "A"
    assert bundle["setup"]["intent"] == ["chat"]
    assert [e["phase"] for e in bundle["recent_events"]] == ["pulls"]
    assert sorted(r["points_to"] for r in bundle["legacy_python_registrations"]) == [
        "elsewhere",
        "this_installation",
    ]
    assert bundle["status"]["mode"] == "liveness"
    assert bundle["privacy"]["excluded"]


def test_diagnostics_never_run_inference(machine: dict[str, Any]) -> None:
    posted: list[str] = []
    runner = machine["runner"]

    def spying_runner(args: Sequence[str], **kwargs: Any) -> CommandResult:
        posted.extend(str(a) for a in args)
        return runner(args, **kwargs)

    machine["runner"] = spying_runner
    _bundle(machine)

    assert "exec" not in posted


@pytest.mark.parametrize(
    ("reason", "extra", "expected"),
    [
        ("DOCKER_UNREACHABLE", {}, "runtime_failure"),
        ("WEBUI_BACKEND_UNAVAILABLE", {}, "backend_failure"),
        ("BACKEND_CANNOT_REACH_OLLAMA", {}, "backend_failure"),
        ("MODEL_MISSING", {}, "model_failure"),
        ("INFERENCE_FAILED", {}, "model_failure"),
        ("FOREIGN_PROJECT_COLLISION", {}, "foreign_resource_collision"),
        ("PAYLOAD_NOT_FOUND", {}, "corrupt_installation"),
        ("STATUS_ERROR", {}, "unknown"),
        ("LIVE", {"onboarding_required": True}, "chat_onboarding"),
        ("READY", {"onboarding_required": False}, "healthy"),
    ],
)
def test_classification_separates_the_support_categories(
    reason: str, extra: dict[str, Any], expected: str
) -> None:
    status = {"reason": reason, "chat": {"onboarding_required": None, **extra}}
    setup = {"preflight": {"overall": "READY"}, "restart_pending": False}

    assert diagnostics.classify(status, {"manifest_present": False}, setup) == expected


def test_a_prerequisite_problem_outranks_runtime_symptoms() -> None:
    status = {"reason": "DOCKER_UNREACHABLE", "chat": {}}

    assert (
        diagnostics.classify(
            status, {}, {"preflight": {"overall": "RECOVERABLE_BLOCKER"}}
        )
        == "prerequisite_failure"
    )
    assert (
        diagnostics.classify(status, {}, {"restart_pending": True, "preflight": {}})
        == "prerequisite_failure"
    )


def test_a_corrupt_installation_outranks_everything() -> None:
    status = {"reason": "READY", "chat": {}}

    assert (
        diagnostics.classify(
            status,
            {"manifest_present": True, "intact": False},
            {"restart_pending": True},
        )
        == "corrupt_installation"
    )
