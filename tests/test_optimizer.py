"""Adversarial checks for measurement identity and recommendation boundaries."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from localai import optimizer, optimizer_ollama


@pytest.fixture
def profiles():
    hardware = optimizer.HardwareProfile(
        "CPU",
        16,
        32 * optimizer.GIB,
        20 * optimizer.GIB,
        (optimizer.GPU("GPU", 12 * optimizer.GIB, 10 * optimizer.GIB),),
        "Windows x64",
    )
    model = optimizer.ModelProfile(
        "example:latest",
        "sha256:one",
        "llama",
        7_000_000_000,
        "Q4_K_M",
        4 * optimizer.GIB,
        16384,
        16_384,
        {"block_count": 32},
    )
    runtime = optimizer.RuntimeProfile("ollama", "0.34.0", None, {"temperature": 0})
    return hardware, model, runtime


def measured(ctx, *, speed=40.0, ttft=0.2, slow=False):
    times = (0.5, 0.5, 20.0) if slow else (0.5, 0.6, 0.55)
    runs = tuple(
        optimizer.Run(True, total, ttft, 20, 200.0, 20, speed) for total in times
    )
    return replace(optimizer.summarize(ctx, runs), effective_context=ctx)


def test_identity_changes_invalidate_cache(tmp_path, profiles):
    hardware, model, runtime = profiles
    cache = optimizer.MeasurementCache(tmp_path / "measurements.json")
    key = optimizer.measurement_key(hardware, model, runtime, 4096)
    cache.put(key, measured(4096))
    assert cache.get(key).source == "cache"
    changes = (
        (replace(hardware, cpu="other CPU"), model, runtime),
        (hardware, replace(model, digest="sha256:two"), runtime),
        (hardware, model, replace(runtime, version="0.35.0")),
        (hardware, model, replace(runtime, backend="cuda")),
        (hardware, model, replace(runtime, options={"temperature": 1})),
    )
    for changed in changes:
        other = optimizer.measurement_key(*changed, 4096)
        assert other != key
        assert cache.get(other) is None
    assert (
        optimizer.measurement_key(hardware, replace(model, digest=None), runtime, 4096)
        is None
    )


def test_cache_corrupt_stale_and_bounded(tmp_path, profiles):
    hardware, model, runtime = profiles
    cache = optimizer.MeasurementCache(tmp_path / "measurements.json")
    key = optimizer.measurement_key(hardware, model, runtime, 4096)
    cache.path.write_text("{broken", encoding="utf-8")
    assert cache.get(key) is None
    cache.put(key, measured(4096))
    assert cache.get(key) is not None
    later = datetime.now(UTC) + timedelta(days=optimizer.CACHE_AGE_DAYS + 1)
    assert cache.get(key, now=later) is None
    for index in range(optimizer.MAX_CACHE_ENTRIES + 5):
        cache.put(f"key{index}", measured(4096))
    assert len(cache._read()) == optimizer.MAX_CACHE_ENTRIES


def test_cache_revalidates_run_data_and_effective_context(tmp_path, profiles):
    import json

    hardware, model, runtime = profiles
    cache = optimizer.MeasurementCache(tmp_path / "measurements.json")
    key = optimizer.measurement_key(hardware, model, runtime, 4096)
    cache.put(key, measured(4096))
    data = json.loads(cache.path.read_text(encoding="utf-8"))
    data["entries"][key]["median_decode_tokens_per_second"] = 999999
    cache.path.write_text(json.dumps(data), encoding="utf-8")
    assert cache.get(key).median_decode_tokens_per_second == 40.0
    data["entries"][key]["effective_context"] = 2048
    cache.path.write_text(json.dumps(data), encoding="utf-8")
    assert cache.get(key) is None
    data["entries"][key]["effective_context"] = 4096
    data["entries"][key]["runs"][0]["decode_tokens_per_second"] = -1
    cache.path.write_text(json.dumps(data), encoding="utf-8")
    assert cache.get(key) is None


def test_unknown_probe_and_architecture_are_conservative(profiles):
    hardware, model, _ = profiles
    incomplete = replace(hardware, ram_total_bytes=None, gpus=())
    assert not optimizer.estimate_context(incomplete, model, 4096).safe
    no_available_ram = replace(hardware, ram_available_bytes=None)
    assert not optimizer.estimate_context(no_available_ram, model, 4096).safe
    unknown = replace(
        model,
        parameter_count=None,
        kv_bytes_per_token=None,
        max_context=None,
        quantization=None,
    )
    assert optimizer.context_ladder(unknown) == ()
    assert not optimizer.estimate_context(hardware, unknown, 8192).safe
    assert optimizer.context_ladder(replace(model, max_context=2048)) == (1024, 2048)


def test_benchmark_failure_blocks_higher_context_and_recommendation(tmp_path, profiles):
    hardware, model, runtime = profiles
    attempted = []

    def fail(tag, ctx):
        attempted.append(ctx)
        return optimizer.summarize(
            ctx, (optimizer.Run(False, 1.0, None, None, None, None, None, "OOM"),)
        )

    result = optimizer.optimize(
        hardware, model, runtime, fail, optimizer.MeasurementCache(tmp_path / "c")
    )
    assert attempted == [4096]
    assert result.recommended_context is None
    assert result.confidence == "insufficient evidence"


def test_fresh_benchmark_wrong_context_cannot_authorize_cache(tmp_path, profiles):
    hardware, model, runtime = profiles
    cache = optimizer.MeasurementCache(tmp_path / "c")
    attempted = []

    def wrong(tag, ctx):
        attempted.append(ctx)
        return measured(8192)

    result = optimizer.optimize(hardware, model, runtime, wrong, cache)
    assert attempted == [4096]
    assert result.recommended_context is None
    assert (
        result.measurements[0].error
        == "benchmark evidence or effective context mismatch"
    )
    key = optimizer.measurement_key(hardware, model, runtime, 4096)
    assert cache.get(key) is None


def test_modes_measurements_anomaly_and_override(tmp_path, profiles):
    hardware, model, runtime = profiles
    cache = optimizer.MeasurementCache(tmp_path / "c")

    def bench(tag, ctx):
        return measured(ctx, speed=15.0 if ctx == 16384 else 40.0, slow=True)

    balanced = optimizer.optimize(hardware, model, runtime, bench, cache)
    assert balanced.recommended_context == 8192
    assert balanced.measurements[0].median_total_seconds == 0.5
    long = optimizer.optimize(
        hardware, model, runtime, bench, cache, mode="long-context"
    )
    assert long.recommended_context == 16384
    assert all(m.source == "cache" for m in long.measurements)
    override = optimizer.optimize(
        hardware, model, runtime, bench, cache, override_context=4096
    )
    assert override.recommended_context == 8192
    assert override.selected_context == 4096
    with pytest.raises(ValueError, match="override"):
        optimizer.optimize(
            hardware, model, runtime, bench, cache, override_context=32768
        )


def test_memory_disagreement_and_model_max_are_enforced(tmp_path, profiles):
    hardware, model, runtime = profiles
    gpu_estimate = optimizer.estimate_context(hardware, model, 4096)
    assert gpu_estimate.gpu_fit_estimate is True
    assert gpu_estimate.gpu_budget_bytes == 12 * optimizer.GIB - int(
        optimizer.VRAM_OVERHEAD_GB * optimizer.GIB
    )
    assert not optimizer.estimate_context(hardware, model, 32768).safe
    assert optimizer.estimate_context(hardware, model, 32768).reason == "model maximum"
    tight = replace(hardware, ram_available_bytes=6 * optimizer.GIB)
    calls = []
    result = optimizer.optimize(
        tight,
        model,
        runtime,
        lambda tag, ctx: calls.append(ctx) or measured(ctx),
        optimizer.MeasurementCache(tmp_path / "c"),
    )
    assert not calls
    assert result.recommended_context is None


def test_adapter_parses_truthful_metadata_and_missing_fields(monkeypatch):
    def fake_json(path, **kwargs):
        if path == "/api/version":
            return {"version": "0.34.0"}
        if path == "/api/tags":
            return {"models": [{"name": "m", "digest": "sha256:abc", "size": 123456}]}
        return {
            "details": {"family": "llama", "parameter_size": "7.5B"},
            "model_info": {
                "general.architecture": "llama",
                "llama.context_length": 8192,
                "llama.block_count": 32,
                "llama.attention.head_count_kv": 8,
                "llama.attention.key_length": 128,
            },
        }

    monkeypatch.setattr(optimizer_ollama, "_json", fake_json)
    model, runtime = optimizer_ollama.profile_ollama("m")
    assert model.max_context == 8192
    assert model.kv_bytes_per_token == 2 * 32 * 8 * 256
    assert model.quantization is None
    assert runtime.backend is None
    with pytest.raises(ValueError, match="not found"):
        optimizer_ollama.profile_ollama("missing")


def test_adapter_refuses_unavailable_runtime_and_request_failure(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("Ollama unavailable")

    monkeypatch.setattr(optimizer_ollama, "_json", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        optimizer_ollama.profile_ollama("m")
    monkeypatch.setattr(
        optimizer_ollama,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timeout")),
    )
    result = optimizer_ollama.benchmark_ollama("m", 4096, timeout_sec=1)
    assert not result.successful
    assert result.error and "timeout" in result.error


def test_no_positive_cache_entry_after_failed_run(tmp_path, profiles):
    hardware, model, runtime = profiles
    cache = optimizer.MeasurementCache(tmp_path / "c")
    failure = optimizer.summarize(
        4096, (optimizer.Run(False, 1, None, None, None, None, None, "refused"),)
    )
    cache.put(optimizer.measurement_key(hardware, model, runtime, 4096), failure)
    assert cache.get(optimizer.measurement_key(hardware, model, runtime, 4096)) is None


def test_effective_context_mismatch_is_failure(monkeypatch):
    run = optimizer.Run(True, 0.5, 0.1, 20, 100.0, 20, 40.0)
    monkeypatch.setattr(optimizer_ollama, "_one_run", lambda *a, **k: run)
    monkeypatch.setattr(
        optimizer_ollama,
        "_json",
        lambda *a, **k: {
            "models": [{"name": "m", "context_length": 2048, "size_vram": 100}]
        },
    )
    result = optimizer_ollama.benchmark_ollama("m", 4096)
    assert not result.successful
    assert result.effective_context == 2048
    assert "did not match" in result.error


def test_runtime_residency_is_observation_not_backend(monkeypatch):
    run = optimizer.Run(True, 0.5, 0.1, 20, 100.0, 20, 40.0)
    monkeypatch.setattr(optimizer_ollama, "_one_run", lambda *a, **k: run)
    monkeypatch.setattr(
        optimizer_ollama,
        "_json",
        lambda *a, **k: {
            "models": [
                {"name": "m", "context_length": 4096, "size_vram": 100, "size": 200}
            ]
        },
    )
    result = optimizer_ollama.benchmark_ollama("m", 4096)
    assert result.successful
    assert result.effective_context == 4096
    assert result.resident_vram_bytes == 100
    assert result.resident_total_bytes == 200
