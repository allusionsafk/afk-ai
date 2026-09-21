"""Bounded llama.cpp server lifecycle and optimizer measurement adapter.

This module is intentionally an experimental runtime boundary. It owns every
llama.cpp command-line flag and always stops the process it starts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from localai.optimizer import (
    NUM_PREDICT,
    PROMPT,
    RUNS,
    WARMUPS,
    Measurement,
    ModelProfile,
    Run,
    RuntimeProfile,
    summarize,
)


@dataclass(frozen=True)
class LlamaCppConfig:
    executable: Path
    model_path: Path
    model_sha256: str | None = None
    host: str = "127.0.0.1"
    port: int = 11435
    startup_timeout_sec: float = 90
    request_timeout_sec: float = 90
    n_gpu_layers: str = "all"
    flash_attention: bool = True
    cache_type_k: str = "q8_0"
    cache_type_v: str = "q8_0"
    backend: str = "CUDA 12.4"
    log_path: Path | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _validate_config(config: LlamaCppConfig) -> None:
    if not config.executable.is_file():
        raise FileNotFoundError(f"llama.cpp executable not found: {config.executable}")
    if not config.model_path.is_file():
        raise FileNotFoundError(f"llama.cpp model not found: {config.model_path}")
    if not 1 <= config.port <= 65535:
        raise ValueError("llama.cpp port must be between 1 and 65535")
    if config.startup_timeout_sec <= 0 or config.request_timeout_sec <= 0:
        raise ValueError("llama.cpp timeouts must be positive")


def server_command(config: LlamaCppConfig, context: int) -> list[str]:
    """Build the complete pinned server command without leaking flags upstream."""
    if context < 1024:
        raise ValueError("llama.cpp context must be at least 1024")
    return [
        str(config.executable),
        "-m",
        str(config.model_path),
        "--ctx-size",
        str(context),
        "--n-gpu-layers",
        config.n_gpu_layers,
        "--flash-attn",
        "on" if config.flash_attention else "off",
        "--cache-type-k",
        config.cache_type_k,
        "--cache-type-v",
        config.cache_type_v,
        "--parallel",
        "1",
        "--seed",
        "1",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--no-webui",
        "--metrics",
    ]


def _parse_version(output: str) -> str:
    match = re.search(
        r"version:\s+(\S+).*?build\s+(\d+),\s+commit\s+([0-9A-Za-z]+)",
        output,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise RuntimeError("llama.cpp version probe returned unrecognized output")
    version, build, commit = match.groups()
    return f"{version}+build.{build}.commit.{commit}"


def profile_llamacpp(
    config: LlamaCppConfig, reference_model: ModelProfile
) -> tuple[ModelProfile, RuntimeProfile]:
    """Identify the pinned binary while retaining exact shared model identity."""
    _validate_config(config)
    try:
        completed = subprocess.run(
            [str(config.executable), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"llama.cpp version probe failed: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            f"llama.cpp version probe failed ({completed.returncode}): {detail}"
        )
    runtime = RuntimeProfile(
        name="llama.cpp",
        version=_parse_version(completed.stdout + "\n" + completed.stderr),
        backend=config.backend,
        options={
            "n_gpu_layers": config.n_gpu_layers,
            "flash_attention": config.flash_attention,
            "cache_type_k": config.cache_type_k,
            "cache_type_v": config.cache_type_v,
            "parallel": 1,
            "temperature": 0,
            "seed": 1,
            "num_predict": NUM_PREDICT,
            "stream": True,
            "cache_prompt": False,
        },
    )
    digest = (
        "sha256:" + config.model_sha256.lower()
        if config.model_sha256
        else reference_model.digest
    )
    model = replace(
        reference_model,
        digest=digest,
        artifact_bytes=config.model_path.stat().st_size,
    )
    return model, runtime


def _json_request(url: str, *, timeout_sec: float) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout_sec) as response:
            value = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"llama.cpp request failed: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("llama.cpp returned a non-object response")
    return value


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        converted = int(value)
    except ValueError:
        return None
    return converted if converted > 0 else None


def _positive_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if converted > 0 else None


def _failed_run(start: float, first: float | None, error: object) -> Run:
    return Run(
        False,
        max(time.perf_counter() - start, 1e-9),
        first,
        None,
        None,
        None,
        None,
        str(error),
    )


def _one_run(
    base_url: str,
    prompt: str,
    num_predict: int,
    *,
    timeout_sec: float,
    process_alive: Callable[[], bool] = lambda: True,
) -> Run:
    """Run one streaming chat completion and normalize llama.cpp timings."""
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": num_predict,
        "temperature": 0,
        "seed": 1,
        "stream": True,
        "stream_options": {"include_usage": True},
        # Force each measured request to evaluate the full prompt.
        "cache_prompt": False,
    }
    request = Request(
        base_url + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    first: float | None = None
    final: dict[str, Any] | None = None
    try:
        with urlopen(request, timeout=timeout_sec) as response:
            for raw_line in response:
                elapsed = time.perf_counter() - start
                if elapsed > timeout_sec:
                    raise TimeoutError(f"benchmark exceeded {timeout_sec:g}s")
                if not process_alive():
                    raise RuntimeError("llama.cpp exited during generation")
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                event = json.loads(payload)
                if not isinstance(event, dict):
                    raise ValueError("invalid stream event")
                error = event.get("error")
                if error:
                    raise RuntimeError(str(error))
                choices = event.get("choices")
                if isinstance(choices, list) and choices:
                    choice = choices[0]
                    delta = choice.get("delta") if isinstance(choice, dict) else None
                    if isinstance(delta, dict):
                        content = delta.get("content") or delta.get("reasoning_content")
                        if first is None and isinstance(content, str) and content:
                            first = elapsed
                if isinstance(event.get("timings"), dict) or (
                    isinstance(choices, list) and not choices and "usage" in event
                ):
                    final = event
    except (
        HTTPError,
        URLError,
        OSError,
        TimeoutError,
        ValueError,
        RuntimeError,
    ) as error:
        return _failed_run(start, first, error)

    elapsed = max(time.perf_counter() - start, 1e-9)
    if final is None or first is None:
        return Run(
            False,
            elapsed,
            first,
            None,
            None,
            None,
            None,
            "missing final event" if final is None else "missing first token",
        )
    timings = final.get("timings")
    usage = final.get("usage")
    timings = timings if isinstance(timings, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = _positive_int(usage.get("prompt_tokens")) or _positive_int(
        timings.get("prompt_n")
    )
    decode_tokens = _positive_int(usage.get("completion_tokens")) or _positive_int(
        timings.get("predicted_n")
    )
    prompt_rate = _positive_float(timings.get("prompt_per_second"))
    decode_rate = _positive_float(timings.get("predicted_per_second"))
    if not prompt_tokens or not decode_tokens or not prompt_rate or not decode_rate:
        return Run(
            False,
            elapsed,
            first,
            prompt_tokens,
            prompt_rate,
            decode_tokens,
            decode_rate,
            "missing or malformed token timing",
        )
    return Run(
        True,
        elapsed,
        first,
        prompt_tokens,
        prompt_rate,
        decode_tokens,
        decode_rate,
    )


class LlamaCppServer:
    """Own one local server process for one configured context."""

    def __init__(self, config: LlamaCppConfig):
        self.config = config
        self.process: subprocess.Popen[bytes] | None = None
        self.effective_context: int | None = None
        self._log: BinaryIO | None = None
        self._temporary_log: Path | None = None
        self.gpu_used_before_bytes: int | None = None

    def _open_log(self) -> BinaryIO:
        if self.config.log_path is not None:
            self.config.log_path.parent.mkdir(parents=True, exist_ok=True)
            return self.config.log_path.open("wb")
        descriptor, name = tempfile.mkstemp(prefix="afk-llamacpp-", suffix=".log")
        os.close(descriptor)
        self._temporary_log = Path(name)
        return self._temporary_log.open("wb")

    def _log_tail(self) -> str:
        path = self.config.log_path or self._temporary_log
        if self._log is not None:
            self._log.flush()
        if path is None:
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")[-2000:].strip()
        except OSError:
            return ""

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, context: int) -> float:
        _validate_config(self.config)
        started = time.perf_counter()
        self.gpu_used_before_bytes = _total_vram_used_bytes()
        self._log = self._open_log()
        try:
            self.process = subprocess.Popen(
                server_command(self.config, context),
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise RuntimeError(f"could not start process: {error}") from error
        while time.perf_counter() - started <= self.config.startup_timeout_sec:
            if self.process.poll() is not None:
                detail = self._log_tail()
                suffix = f": {detail}" if detail else ""
                code = self.process.returncode
                raise RuntimeError(
                    f"llama.cpp exited during startup ({code}){suffix}"
                )
            try:
                health = _json_request(
                    self.config.base_url + "/health", timeout_sec=1
                )
                if health.get("status") == "ok":
                    break
            except RuntimeError:
                pass
            time.sleep(0.1)
        else:
            timeout = self.config.startup_timeout_sec
            raise RuntimeError(
                f"llama.cpp startup timed out after {timeout:g}s"
            )
        props = _json_request(self.config.base_url + "/props", timeout_sec=5)
        settings = props.get("default_generation_settings")
        settings = settings if isinstance(settings, dict) else {}
        effective = _positive_int(settings.get("n_ctx"))
        self.effective_context = effective
        if effective != context:
            raise RuntimeError(
                f"effective context {effective} did not match requested {context}"
            )
        served_path = props.get("model_path")
        if not isinstance(served_path, str) or not _same_file(
            Path(served_path), self.config.model_path
        ):
            raise RuntimeError("llama.cpp served model did not match requested model")
        return time.perf_counter() - started

    def stop(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._log is not None:
            self._log.close()
            self._log = None
        if self._temporary_log is not None:
            with suppress(OSError):
                self._temporary_log.unlink()
            self._temporary_log = None


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(
            str(right.resolve())
        )


def _resident_vram_bytes(pid: int) -> int | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        fields = [field.strip() for field in line.split(",", 1)]
        if len(fields) == 2 and fields[0] == str(pid):
            try:
                mib = _positive_int(float(fields[1]))
            except ValueError:
                continue
            return mib * 1024**2 if mib else None
    return None


def _total_vram_used_bytes() -> int | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    values = [
        value
        for line in completed.stdout.splitlines()
        if (value := _positive_int(line.strip())) is not None
    ]
    return sum(values) * 1024**2 if values else None


def _resident_ram_bytes(pid: int) -> int | None:
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"(Get-Process -Id {pid} -ErrorAction Stop).WorkingSet64",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return _positive_int(completed.stdout.strip())


def benchmark_llamacpp(
    config: LlamaCppConfig,
    context: int,
    *,
    prompt: str = PROMPT,
    num_predict: int = NUM_PREDICT,
    prompt_variants: tuple[str, ...] | None = None,
) -> Measurement:
    """Start, measure, observe, and stop one llama.cpp context configuration."""
    if prompt_variants is not None and len(prompt_variants) != WARMUPS + RUNS:
        raise ValueError("prompt_variants must contain warmup plus measured runs")
    server = LlamaCppServer(config)
    startup: float | None = None
    warmup_seconds: float | None = None
    try:
        startup = server.start(context)
        for index in range(WARMUPS):
            warmup = _one_run(
                config.base_url,
                prompt_variants[index] if prompt_variants else prompt,
                num_predict,
                timeout_sec=config.request_timeout_sec,
                process_alive=server.is_alive,
            )
            warmup_seconds = warmup.total_seconds
            if not warmup.success:
                return replace(
                    summarize(context, (warmup,)),
                    effective_context=server.effective_context,
                    startup_seconds=startup,
                    warmup_seconds=warmup_seconds,
                )
        runs: list[Run] = []
        for index in range(RUNS):
            run = _one_run(
                config.base_url,
                prompt_variants[WARMUPS + index] if prompt_variants else prompt,
                num_predict,
                timeout_sec=config.request_timeout_sec,
                process_alive=server.is_alive,
            )
            runs.append(run)
            if not run.success:
                break
        measurement = summarize(context, tuple(runs))
        if not measurement.successful or server.process is None:
            return replace(
                measurement,
                effective_context=server.effective_context,
                startup_seconds=startup,
                warmup_seconds=warmup_seconds,
            )
        pid = server.process.pid
        observed_vram = _resident_vram_bytes(pid)
        if observed_vram is None:
            after = _total_vram_used_bytes()
            before = server.gpu_used_before_bytes
            if before is not None and after is not None and after > before:
                observed_vram = after - before
        return replace(
            measurement,
            effective_context=server.effective_context,
            resident_vram_bytes=observed_vram,
            resident_ram_bytes=_resident_ram_bytes(pid),
            startup_seconds=startup,
            warmup_seconds=warmup_seconds,
        )
    except (OSError, RuntimeError, ValueError) as error:
        failed = Run(
            False,
            max(startup or 1e-9, 1e-9),
            None,
            None,
            None,
            None,
            None,
            str(error),
        )
        return replace(
            summarize(context, (failed,)),
            effective_context=server.effective_context,
            startup_seconds=startup,
            warmup_seconds=warmup_seconds,
        )
    finally:
        server.stop()
