"""Failure-boundary checks for the experimental llama.cpp adapter."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from experiments.llamacpp_competition import adapter as optimizer_llamacpp

from localai import optimizer, optimizer_ollama


class FakeResponse:
    def __init__(self, lines: list[bytes]):
        self.lines = lines

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)


@pytest.fixture
def reference_model() -> optimizer.ModelProfile:
    return optimizer.ModelProfile(
        "qwen2.5:14b",
        "sha256:model-one",
        "qwen2",
        14_700_000_000,
        "Q4_K_M",
        8_988_110_688,
        32768,
        163_840,
        {"block_count": 48},
    )


def config(tmp_path: Path) -> optimizer_llamacpp.LlamaCppConfig:
    executable = tmp_path / "llama-server.exe"
    executable.write_bytes(b"exe")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    return optimizer_llamacpp.LlamaCppConfig(executable, model)


def test_unavailable_executable_and_missing_model_are_rejected(
    tmp_path: Path, reference_model: optimizer.ModelProfile
) -> None:
    missing_exe = optimizer_llamacpp.LlamaCppConfig(
        tmp_path / "missing.exe", tmp_path / "missing.gguf"
    )
    with pytest.raises(FileNotFoundError, match="executable"):
        optimizer_llamacpp.profile_llamacpp(missing_exe, reference_model)

    cfg = config(tmp_path)
    cfg.model_path.unlink()
    with pytest.raises(FileNotFoundError, match="model"):
        optimizer_llamacpp.profile_llamacpp(cfg, reference_model)


def test_invalid_executable_is_reported(
    tmp_path: Path,
    reference_model: optimizer.ModelProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        optimizer_llamacpp.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bad image")),
    )
    with pytest.raises(RuntimeError, match="version probe.*bad image"):
        optimizer_llamacpp.profile_llamacpp(config(tmp_path), reference_model)


def test_runtime_and_model_identity_changes_separate_cache_keys(
    tmp_path: Path,
    reference_model: optimizer.ModelProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter(
        (
            "version: 0.4.1-dev (build 10964, commit b29c606e2)",
            "version: 0.4.2-dev (build 11000, commit feedface0)",
        )
    )

    class Result:
        returncode = 0
        stderr = ""

        @property
        def stdout(self) -> str:
            return next(outputs)

    monkeypatch.setattr(optimizer_llamacpp.subprocess, "run", lambda *a, **k: Result())
    cfg = config(tmp_path)
    _, first_runtime = optimizer_llamacpp.profile_llamacpp(cfg, reference_model)
    _, second_runtime = optimizer_llamacpp.profile_llamacpp(cfg, reference_model)
    hardware = optimizer.HardwareProfile("cpu", 8, 32, 16, (), "Windows")
    first = optimizer.measurement_key(hardware, reference_model, first_runtime, 4096)
    assert first != optimizer.measurement_key(
        hardware, reference_model, second_runtime, 4096
    )
    assert first != optimizer.measurement_key(
        hardware,
        replace(reference_model, digest="sha256:model-two"),
        first_runtime,
        4096,
    )


def test_cache_never_reuses_ollama_evidence_for_llamacpp(
    tmp_path: Path, reference_model: optimizer.ModelProfile
) -> None:
    hardware = optimizer.HardwareProfile("cpu", 8, 32, 16, (), "Windows")
    ollama = optimizer.RuntimeProfile("ollama", "0.12", None, {})
    llama = optimizer.RuntimeProfile("llama.cpp", "b10964-b29c606e2", "CUDA", {})
    ollama_key = optimizer.measurement_key(hardware, reference_model, ollama, 4096)
    llama_key = optimizer.measurement_key(hardware, reference_model, llama, 4096)
    assert ollama_key != llama_key
    cache = optimizer.MeasurementCache(tmp_path / "cache.json")
    runs = tuple(
        optimizer.Run(True, 1.0, 0.1, 58, 500.0, 48, 30.0)
        for _ in range(optimizer.RUNS)
    )
    cache.put(
        ollama_key,
        replace(optimizer.summarize(4096, runs), effective_context=4096),
    )
    assert cache.get(llama_key) is None


def _stream_event(value: str) -> bytes:
    return f"data: {value}\n".encode()


def test_malformed_timing_output_is_a_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        [
            _stream_event('{"choices":[{"delta":{"content":"token"}}]}'),
            _stream_event('{"choices":[],"usage":{"prompt_tokens":58}}'),
            _stream_event("[DONE]"),
        ]
    )
    monkeypatch.setattr(optimizer_llamacpp, "urlopen", lambda *a, **k: response)
    result = optimizer_llamacpp._one_run(
        "http://127.0.0.1:11435", "prompt", 48, timeout_sec=10
    )
    assert not result.success
    assert result.error == "missing or malformed token timing"


def test_request_timeout_is_a_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        optimizer_llamacpp,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timed out")),
    )
    result = optimizer_llamacpp._one_run(
        "http://127.0.0.1:11435", "prompt", 48, timeout_sec=1
    )
    assert not result.success
    assert result.error and "timed out" in result.error


def test_process_exit_during_generation_is_a_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        [_stream_event('{"choices":[{"delta":{"content":"token"}}]}')]
    )
    monkeypatch.setattr(optimizer_llamacpp, "urlopen", lambda *a, **k: response)
    result = optimizer_llamacpp._one_run(
        "http://127.0.0.1:11435",
        "prompt",
        48,
        timeout_sec=10,
        process_alive=lambda: False,
    )
    assert not result.success
    assert result.error == "llama.cpp exited during generation"


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("could not start process", "could not start process"),
        ("effective context 2048 did not match requested 4096", "did not match"),
    ),
)
def test_startup_failure_and_context_mismatch_become_measurement_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    expected: str,
) -> None:
    cfg = config(tmp_path)

    def fail(_self: Any, _context: int) -> float:
        raise RuntimeError(message)

    stopped: list[bool] = []
    monkeypatch.setattr(optimizer_llamacpp.LlamaCppServer, "start", fail)
    monkeypatch.setattr(
        optimizer_llamacpp.LlamaCppServer,
        "stop",
        lambda _self: stopped.append(True),
    )
    result = optimizer_llamacpp.benchmark_llamacpp(cfg, 4096)
    assert not result.successful
    assert result.error and expected in result.error
    assert stopped == [True]


def test_server_command_keeps_runtime_flags_inside_adapter(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    command = optimizer_llamacpp.server_command(cfg, 8192)
    assert command[0] == str(cfg.executable)
    assert command[command.index("--ctx-size") + 1] == "8192"
    assert command[command.index("--cache-type-k") + 1] == "q8_0"
    assert "--no-webui" in command


def test_vram_observer_skips_unavailable_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        returncode = 0
        stdout = "35716, [N/A]\n4242, 9123\n"

    monkeypatch.setattr(
        optimizer_llamacpp.subprocess, "run", lambda *a, **k: Result()
    )
    assert optimizer_llamacpp._resident_vram_bytes(4242) == 9123 * 1024**2
    assert optimizer_llamacpp._resident_vram_bytes(35716) is None


def test_ram_observer_parses_powershell_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        returncode = 0
        stdout = "78962688\r\n"

    monkeypatch.setattr(
        optimizer_llamacpp.subprocess, "run", lambda *a, **k: Result()
    )
    assert optimizer_llamacpp._resident_ram_bytes(4242) == 78_962_688


def test_prompt_variants_must_cover_warmup_and_measured_runs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="warmup plus measured"):
        optimizer_llamacpp.benchmark_llamacpp(
            config(tmp_path), 4096, prompt_variants=("only one",)
        )
    with pytest.raises(ValueError, match="warmup plus measured"):
        optimizer_ollama.benchmark_ollama(
            "model", 4096, prompt_variants=("only one",)
        )
