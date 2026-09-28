"""Local Ollama inventory and installed product command boundary tests."""

import io
import json

import pytest

from localai import optimizer, product_cli, product_utility_ollama


def _pc():
    return optimizer.HardwareProfile(
        "Intel i9",
        32,
        32 * optimizer.GIB,
        18 * optimizer.GIB,
        (optimizer.GPU("RTX 4080", 12 * optimizer.GIB, 10 * optimizer.GIB),),
        "Windows 11",
    )


def _tag(name="qwen:9b", size=6 * optimizer.GIB):
    return {
        "name": name,
        "digest": "a" * 64,
        "size": size,
        "details": {
            "family": "qwen3",
            "parameter_size": "9B",
            "quantization_level": "Q4_K_M",
        },
    }


def test_quick_report_uses_three_local_calls_and_exact_installed_bytes():
    called = []

    def fetch(path, body=None):
        called.append(path)
        return {
            "/api/version": {"version": "0.34.4"},
            "/api/tags": {"models": [_tag()]},
            "/api/ps": {"models": [{"name": "qwen:9b", "digest": "a" * 64}]},
        }[path]

    report = product_utility_ollama.collect_local_report(
        "qwen:9b", context=8192, fetch=fetch, hardware_probe=_pc
    )
    assert called == ["/api/version", "/api/tags", "/api/ps"]
    assert report["runtime"]["version"] == "0.34.4"
    assert report["models"][0]["weights_bytes"] == 6 * optimizer.GIB
    assert report["models"][0]["loaded"] is True
    assert report["recommendation"]["model"] == "qwen:9b"


def test_partial_runtime_failure_preserves_installed_inventory_but_unknown_loaded():
    def fetch(path, body=None):
        if path == "/api/ps":
            raise RuntimeError("Ollama /api/ps unavailable")
        return {"models": [_tag()]} if path == "/api/tags" else {"version": "0.34.4"}

    report = product_utility_ollama.collect_local_report(
        None, fetch=fetch, hardware_probe=_pc
    )
    assert report["models"][0]["installed"] is True
    assert report["models"][0]["loaded"] is None
    assert report["runtime"]["loaded_state"] == "Unknown"


def test_malformed_tags_are_skipped_and_enrichment_is_bounded():
    called = []
    rows = [_tag(f"m{i}:latest") for i in range(20)]
    rows += [_tag("bad\nname"), _tag("oversize:latest", size=10**100)]

    def fetch(path, body=None):
        called.append(path)
        if path == "/api/tags":
            return {"models": rows}
        if path == "/api/ps":
            return {"models": []}
        if path == "/api/show":
            return {
                "model_info": {
                    "general.architecture": "llama",
                    "llama.context_length": 32768,
                    "llama.block_count": 32,
                    "llama.attention.head_count_kv": 8,
                    "llama.attention.key_length": 128,
                }
            }
        return {"version": "0.34.4"}

    report = product_utility_ollama.collect_local_report(
        None, enrich=True, fetch=fetch, hardware_probe=_pc
    )
    assert len(report["models"]) == 21
    assert called.count("/api/show") <= 8
    assert all("\n" not in row["model"] for row in report["models"])
    assert (
        next(row for row in report["models"] if row["model"] == "oversize:latest")[
            "verdict"
        ]
        == "Unknown"
    )


def test_http_reader_rejects_oversize_and_non_object_json(monkeypatch):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(
        product_utility_ollama,
        "urlopen",
        lambda *a, **k: Response(
            b"x" * (product_utility_ollama.MAX_RESPONSE_BYTES + 1)
        ),
    )
    with pytest.raises(RuntimeError, match="too large"):
        product_utility_ollama._json("/api/tags")
    monkeypatch.setattr(
        product_utility_ollama, "urlopen", lambda *a, **k: Response(b"[]")
    )
    with pytest.raises(RuntimeError, match="invalid"):
        product_utility_ollama._json("/api/tags")


def test_product_cli_report_is_versioned_json_and_never_downloads(
    monkeypatch, tmp_path, capsys
):
    report = {"schema_version": 1, "hardware": {}, "models": [], "recommendation": None}
    monkeypatch.setattr(
        product_utility_ollama, "collect_local_report", lambda *a, **k: report
    )
    code = product_cli.main(
        [
            "--program-root",
            str(tmp_path / "program"),
            "--data-root",
            str(tmp_path / "data"),
            "utility-report",
            "--json",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out) == report


def test_cached_only_optimizer_command_does_not_benchmark(
    monkeypatch, tmp_path, capsys
):
    model = optimizer.ModelProfile(
        "qwen:9b",
        "a" * 64,
        "qwen3",
        9_000_000_000,
        "Q4_K_M",
        6 * optimizer.GIB,
        32768,
        65536,
        {"block_count": 32},
    )
    runtime = optimizer.RuntimeProfile("ollama", "0.34.4", None, {})
    monkeypatch.setattr(product_utility_ollama, "profile_hardware", _pc)
    monkeypatch.setattr(
        product_utility_ollama, "profile_ollama", lambda tag: (model, runtime)
    )
    monkeypatch.setattr(
        product_utility_ollama,
        "benchmark_ollama",
        lambda *a, **k: pytest.fail("benchmark ran during cached-only command"),
    )
    code = product_cli.main(
        [
            "--program-root",
            str(tmp_path / "program"),
            "--data-root",
            str(tmp_path / "data"),
            "utility-optimize",
            "--model",
            "qwen:9b",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["schema_version"] == 1
    assert output["recommended_context"] is None
    assert output["measurements"] == []
    assert output["selected_context"] is None


def test_optimizer_rejects_invalid_model_before_runtime_call(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(
        product_utility_ollama,
        "profile_ollama",
        lambda tag: pytest.fail("runtime called for invalid tag"),
    )
    code = product_cli.main(
        [
            "--program-root",
            str(tmp_path / "program"),
            "--data-root",
            str(tmp_path / "data"),
            "utility-optimize",
            "--model",
            "bad\nname",
        ]
    )
    assert code == 2
    assert "invalid model" in capsys.readouterr().err.lower()
