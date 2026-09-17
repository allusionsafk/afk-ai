from __future__ import annotations

from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from localai import warm
from localai.ops import CommandResult
from localai.paths import REPO_ROOT


def test_warm_override_state_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "warm-model.json"
    monkeypatch.setattr(warm, "warm_state_path", lambda: state)

    assert warm.read_warm_model_override() is None
    warm.write_warm_model_override("deep-thinking-qwen3.6")
    assert warm.read_warm_model_override() == "deep-thinking-qwen3.6"

    warm.write_known_models(["a:latest", "b"])
    assert warm.read_known_models() == ["a:latest", "b"]
    # Clearing the override must not clear the cached model list.
    warm.write_warm_model_override(None)
    assert warm.read_warm_model_override() is None
    assert warm.read_known_models() == ["a:latest", "b"]


def test_warm_override_survives_garbage_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "warm-model.json"
    state.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(warm, "warm_state_path", lambda: state)

    assert warm.read_warm_model_override() is None
    assert warm.read_known_models() == []
    warm.write_warm_model_override("qwen3-grounded")
    assert warm.read_warm_model_override() == "qwen3-grounded"


def test_warm_prefer_target_warms_choice_over_loaded_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ollama = Path("C:/fake/Ollama/ollama.exe")
    original_exists = Path.exists
    monkeypatch.setattr(warm, "ollama_path", lambda: ollama)
    monkeypatch.setattr(
        Path, "exists", lambda p: True if p == ollama else original_exists(p)
    )

    stopped: list[str] = []
    monkeypatch.setattr(
        warm,
        "run_command",
        lambda args, cwd=None: (
            stopped.append(args[-1]),
            CommandResult(args=args, code=0, stdout="", stderr=""),
        )[1],
    )
    chats: list[dict[str, Any]] = []

    def fake_request_json(
        path: str,
        *,
        timeout_sec: int,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if path == "/api/ps":
            return {"models": [{"name": "qwen2.5-grounded:latest"}]}
        if path == "/api/tags":
            return {"models": []}
        assert path == "/api/chat"
        assert payload is not None
        chats.append(payload)
        return {}

    monkeypatch.setattr(warm, "request_json", fake_request_json)
    monkeypatch.setattr(warm, "wait_for_ollama", lambda **_: True)

    code, lines = warm.collect_warm_report(
        model="deep-thinking-qwen3.6",
        skip_if_any_loaded=True,
        prefer_target=True,
        unload_others=True,
    )

    assert code == 0
    assert not any("preserving loaded model" in line for line in lines)
    assert stopped == ["qwen2.5-grounded:latest"]
    assert [c["model"] for c in chats] == ["deep-thinking-qwen3.6"]


def test_warm_prefer_target_still_preserves_when_choice_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ollama = Path("C:/fake/Ollama/ollama.exe")
    original_exists = Path.exists
    monkeypatch.setattr(warm, "ollama_path", lambda: ollama)
    monkeypatch.setattr(
        Path, "exists", lambda p: True if p == ollama else original_exists(p)
    )

    def fake_request_json(
        path: str,
        *,
        timeout_sec: int,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        assert path == "/api/ps"
        return {"models": [{"name": "deep-thinking-qwen3.6:latest"}]}

    monkeypatch.setattr(warm, "request_json", fake_request_json)

    code, lines = warm.collect_warm_report(
        model="deep-thinking-qwen3.6",
        skip_if_any_loaded=True,
        prefer_target=True,
    )

    assert code == 0
    assert lines == [
        "[AI-Warm] preserving loaded model(s): deep-thinking-qwen3.6:latest"
    ]


def test_warm_defaults_follow_legacy_model_rules() -> None:
    compose = """
    services:
      open-webui:
        environment:
          - DEFAULT_MODELS=deep-thinking-qwen3.6
    """

    assert warm.read_default_model(compose) == "deep-thinking-qwen3.6"
    assert warm.resolve_num_ctx("deep-thinking-qwen3.6", 0) == 4096
    assert warm.resolve_num_ctx("qwen2.5-grounded", 0) == 8192
    assert warm.resolve_num_ctx("qwen2.5-grounded", 1234) == 1234


def test_replace_default_model_makes_readers_see_the_installed_pick() -> None:
    # Finding 1: the installer picks a per-tier tag but compose hardcodes the
    # daily driver, so warm/health/model-scout all warm a model a non-A box does
    # not have. Rewriting the one literal makes every DEFAULT_MODELS= reader agree.
    compose = """
    services:
      open-webui:
        environment:
          - DEFAULT_MODELS=qwen3.5:9b-32k
          - WEBUI_AUTH=False
    """

    out = warm.replace_default_model(compose, "qwen3.5:4b-16k")

    assert warm.read_default_model(out) == "qwen3.5:4b-16k"
    assert "qwen3.5:9b-32k" not in out  # the stale literal is gone
    # untouched lines survive (only the one value changed)
    assert "WEBUI_AUTH=False" in out
    assert "open-webui" in out


def test_replace_default_model_raises_when_marker_absent() -> None:
    # A compose without the marker is broken; surface it (Fable's throw-on-failure
    # contract) rather than silently writing nothing.
    with pytest.raises(ValueError):
        warm.replace_default_model("services:\n  open-webui: {}\n", "qwen3.5:2b-8k")


def test_cli_set_default_model_command_registered() -> None:
    # The installer calls `localai set-default-model --model <tag>`; it must exist
    # with the --model option or the compose rewrite phase silently no-ops.
    import inspect

    from localai import cli

    cmd = next(
        info.callback
        for info in cli.app.registered_commands
        if info.callback and info.callback.__name__ == "set_default_model"
    )
    assert "model" in inspect.signature(cmd).parameters


def test_warm_skip_if_any_loaded_preserves_existing_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ollama = Path("C:/fake/Ollama/ollama.exe")
    original_exists = Path.exists

    def fake_exists(path: Path) -> bool:
        if path == ollama:
            return True
        return original_exists(path)

    def fake_request_json(
        path: str,
        *,
        timeout_sec: int,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        assert path == "/api/ps"
        assert timeout_sec == 3
        assert method == "GET"
        assert payload is None
        return {"models": [{"name": "qwen2.5-grounded:latest"}]}

    monkeypatch.setattr(warm, "ollama_path", lambda: ollama)
    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(warm, "request_json", fake_request_json)

    code, lines = warm.collect_warm_report(skip_if_any_loaded=True)

    assert code == 0
    assert lines == [
        "[AI-Warm] preserving loaded model(s): qwen2.5-grounded:latest"
    ]


def test_warm_unload_others_stops_only_stale_named_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ollama = Path("C:/fake/Ollama/ollama.exe")
    original_exists = Path.exists
    stopped: list[tuple[str, ...]] = []

    def fake_exists(path: Path) -> bool:
        if path == ollama:
            return True
        return original_exists(path)

    def fake_request_json(
        path: str,
        *,
        timeout_sec: int,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if path == "/api/ps":
            assert timeout_sec == 3
            return {
                "models": [
                    {"name": "stale:latest"},
                    {"name": "qwen2.5-grounded:latest"},
                    {"model": "model-only:latest"},
                ]
            }
        if path == "/api/tags":
            assert timeout_sec == 3
            return {}
        if path == "/api/chat":
            assert timeout_sec == 420
            assert method == "POST"
            assert payload == {
                "model": "qwen2.5-grounded",
                "messages": [{"role": "user", "content": "warmup"}],
                "stream": False,
                "keep_alive": "30m",
                "options": {
                    "num_ctx": 8192,
                    "num_predict": 1,
                    "temperature": 0,
                },
            }
            return {}
        msg = f"unexpected path {path}"
        raise AssertionError(msg)

    def fake_run_command(
        args: list[str],
        *,
        cwd: Path,
    ) -> CommandResult:
        assert cwd == REPO_ROOT
        stopped.append(tuple(args))
        return CommandResult(tuple(args), 0, "", "")

    monkeypatch.setattr(warm, "ollama_path", lambda: ollama)
    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(warm, "request_json", fake_request_json)
    monkeypatch.setattr(warm, "run_command", fake_run_command)

    code, lines = warm.collect_warm_report(
        model="qwen2.5-grounded",
        unload_others=True,
        server_attempts=1,
        server_sleep_sec=0,
    )

    assert code == 0
    assert stopped == [(str(ollama), "stop", "stale:latest")]
    assert lines == [
        "[AI-Warm] unloading stale model stale:latest",
        "[AI-Warm] preloaded qwen2.5-grounded (keep_alive=30m, num_ctx=8192)",
    ]


def test_warm_missing_model_failure_matches_legacy_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request_json(
        path: str,
        *,
        timeout_sec: int,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if path == "/api/tags":
            assert timeout_sec == 3
            return {}
        if path == "/api/chat":
            assert timeout_sec == 420
            assert method == "POST"
            assert payload is not None
            headers: Message[str, str] = Message()
            raise HTTPError(
                "http://localhost:11434/api/chat",
                404,
                "Not Found",
                headers,
                None,
            )
        msg = f"unexpected path {path}"
        raise AssertionError(msg)

    monkeypatch.setattr(warm, "request_json", fake_request_json)

    code, lines = warm.collect_warm_report(
        require_ollama=True,
        model="__codex_missing_model__",
        keep_alive="1m",
        num_ctx=16,
        server_attempts=1,
        server_sleep_sec=0,
    )

    assert code == 1
    assert lines == [
        "WARNING: [AI-Warm] failed to preload __codex_missing_model__: "
        "Response status code does not indicate success: 404 (Not Found)."
    ]


def test_warm_unreachable_ollama_is_optional_unless_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request_json(
        path: str,
        *,
        timeout_sec: int,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        assert path == "/api/tags"
        assert timeout_sec == 3
        assert method == "GET"
        assert payload is None
        raise URLError("connection refused")

    monkeypatch.setattr(warm, "request_json", fake_request_json)

    optional_code, optional_lines = warm.collect_warm_report(
        server_attempts=1,
        server_sleep_sec=0,
    )
    required_code, required_lines = warm.collect_warm_report(
        require_ollama=True,
        server_attempts=1,
        server_sleep_sec=0,
    )

    assert optional_code == 0
    assert required_code == 1
    assert optional_lines == required_lines == [
        "WARNING: [AI-Warm] Ollama server is not reachable; skipping preload."
    ]


def test_the_installed_compose_interpolation_resolves_like_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The product feeds DEFAULT_MODELS from runtime config instead of rewriting.

    Every DEFAULT_MODELS= reader must see what compose itself would use: the
    configured value when present, the file's default otherwise.
    """
    compose = (
        "    environment:\n"
        "      - DEFAULT_MODELS=${AFK_DEFAULT_MODEL:-qwen3.5:9b-32k}\n"
    )
    monkeypatch.delenv("AFK_DEFAULT_MODEL", raising=False)
    assert warm.read_default_model(compose) == "qwen3.5:9b-32k"

    monkeypatch.setenv("AFK_DEFAULT_MODEL", "qwen3.5:2b-8k")
    assert warm.read_default_model(compose) == "qwen3.5:2b-8k"
