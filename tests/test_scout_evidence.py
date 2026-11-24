"""Selected download evidence must not underprice malformed remote metadata."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from io import BytesIO

import pytest

from localai import model_scout, scout_evidence

GIB = 1024**3


def _file(path: str, size: object) -> dict[str, object]:
    return {"type": "file", "path": path, "size": size}


def _config(**changes: object) -> dict[str, object]:
    return {
        "model_type": "llama",
        "num_hidden_layers": 32,
        "num_attention_heads": 32,
        "num_key_value_heads": 8,
        "hidden_size": 4096,
        "max_position_embeddings": 32768,
        **changes,
    }


def test_selected_quant_uses_all_shards_in_binary_budget_units() -> None:
    tree = [
        _file("model-Q8_0.gguf", 12 * GIB),
        _file("Q4_K_M/model-Q4_K_M-00002-of-00002.gguf", 2 * GIB),
        _file("Q4_K_M/model-Q4_K_M-00001-of-00002.gguf", 4 * GIB),
        _file("mmproj-Q4_K_M.gguf", GIB),
    ]
    artifact = scout_evidence.select_artefact(tree, model_scout.QUANT_PREFERENCE)
    assert artifact is not None
    assert artifact.quant == "Q4_K_M"
    assert artifact.size_bytes == 6_442_450_944


@pytest.mark.parametrize("size", [None, True, False, 0, -1, 1.5, "42", 2**53])
def test_invalid_shard_size_never_prices_only_the_other_shard(size: object) -> None:
    artifact = scout_evidence.select_artefact(
        [
            _file("m-Q4_K_M-00001-of-00002.gguf", GIB),
            _file("m-Q4_K_M-00002-of-00002.gguf", size),
        ],
        model_scout.QUANT_PREFERENCE,
    )
    assert artifact is not None
    assert artifact.size_bytes is None


@pytest.mark.parametrize(
    "paths",
    [
        ["m-Q4_K_M-00001-of-00002.gguf"],
        ["m-Q4_K_M-00001-of-00002.gguf", "m-Q4_K_M-00001-of-00002.gguf"],
        ["m-Q4_K_M-00001-of-00002.gguf", "m-Q4_K_M-00002-of-00003.gguf"],
        ["m-Q4_K_M-00000-of-00001.gguf"],
        ["m-Q4_K_M-00002-of-00001.gguf"],
        ["m-Q4_K_M.gguf", "other-Q4_K_M.gguf"],
        ["m-Q4_K_M.gguf", "m-Q4_K_M-00001-of-00001.gguf"],
        ["m-Q4_K_M.gguf", "M-q4_k_m.GGUF"],
    ],
)
def test_ambiguous_or_incomplete_quant_has_no_exact_total(paths: list[str]) -> None:
    artifact = scout_evidence.select_artefact(
        [_file(path, GIB) for path in paths], model_scout.QUANT_PREFERENCE
    )
    assert artifact is not None
    assert artifact.quant == "Q4_K_M"
    assert artifact.size_bytes is None


def test_quant_normalization_does_not_split_shard_identity() -> None:
    artifact = scout_evidence.select_artefact(
        [
            _file("m-q4_k_m-00001-of-00002.gguf", GIB),
            _file("m-q4_k_m-00002-of-00002.gguf", GIB),
        ],
        model_scout.QUANT_PREFERENCE,
    )
    assert artifact is not None
    assert (artifact.quant, artifact.size_bytes) == ("Q4_K_M", 2_147_483_648)


def test_shard_total_overflow_is_not_a_valid_byte_count() -> None:
    artifact = scout_evidence.select_artefact(
        [
            _file("m-Q4_K_M-00001-of-00002.gguf", 2**53 - 1),
            _file("m-Q4_K_M-00002-of-00002.gguf", 1),
        ],
        model_scout.QUANT_PREFERENCE,
    )
    assert artifact is not None
    assert artifact.size_bytes is None


def test_a_tree_row_with_missing_identity_cannot_prove_a_complete_total() -> None:
    artifact = scout_evidence.select_artefact(
        [_file("m-Q4_K_M.gguf", GIB), {"size": GIB}],
        model_scout.QUANT_PREFERENCE,
    )
    assert artifact is not None
    assert artifact.size_bytes is None


def test_standard_gqa_estimate_counts_layers_keys_values_context_and_slots() -> None:
    arch = scout_evidence.parse_architecture(_config())
    assert arch is not None
    assert scout_evidence.architecture_kv_gb(arch, ctx=32768, parallel=1) == 4.0
    assert scout_evidence.architecture_kv_gb(arch, ctx=16384, parallel=2) == 4.0
    assert scout_evidence.architecture_kv_gb(arch, ctx=8192, parallel=1) == 1.0


def test_explicit_head_dimension_and_multihead_defaults_are_supported() -> None:
    config = _config(head_dim=128, hidden_size=2560)
    config.pop("num_key_value_heads")
    arch = scout_evidence.parse_architecture(config)
    assert arch is not None
    assert scout_evidence.architecture_kv_gb(arch, ctx=8192, parallel=1) == 4.0


@pytest.mark.parametrize(
    "changes",
    [
        {"num_hidden_layers": True},
        {"num_hidden_layers": 1025},
        {"num_attention_heads": 0},
        {"num_key_value_heads": False},
        {"num_key_value_heads": 33},
        {"num_key_value_heads": 7},
        {"head_dim": -1},
        {"head_dim": 8193},
        {"hidden_size": 4095},
        {"model_type": "qwen3_next"},
        {"model_type": "deepseek_v3"},
        {"model_type": "unknown"},
        {"layer_types": ["linear_attention"]},
        {"kv_lora_rank": 512},
        {"text_config": {}},
        {"max_position_embeddings": "32768"},
        {"model_type": []},
    ],
)
def test_unsupported_or_malformed_architecture_stays_estimated(
    changes: dict[str, object],
) -> None:
    assert scout_evidence.parse_architecture(_config(**changes)) is None


@pytest.mark.parametrize("args", [(True, 8, 128), (0, 8, 128), (32, 4097, 128)])
def test_public_architecture_constructor_rejects_invalid_counts(args: tuple) -> None:
    with pytest.raises(ValueError):
        scout_evidence.ArchitectureInfo(*args)


@pytest.mark.parametrize("ctx,parallel", [(0, 1), (True, 1), (8192, 0), (8192, 257)])
def test_kv_estimate_refuses_invalid_requested_context_or_parallel(
    ctx, parallel
) -> None:
    arch = scout_evidence.ArchitectureInfo(32, 8, 128)
    with pytest.raises(ValueError):
        scout_evidence.architecture_kv_gb(arch, ctx=ctx, parallel=parallel)


def _prepare(monkeypatch, *, size_bytes, config=None, budget=None, ctx=32768):
    monkeypatch.setattr(
        model_scout,
        "fetch_hf_tree",
        lambda repo: [
            _file("m-Q4_K_M.gguf", size_bytes),
        ],
    )
    monkeypatch.setattr(model_scout, "fetch_hf_config", lambda repo: config)
    monkeypatch.setattr(
        model_scout, "run_ollama", lambda *args, **kw: pytest.fail("pull started")
    )
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "1")
    said, logged = [], []
    code = model_scout.prepare_pick(
        replace(model_scout.parse_model("owner/Model-9B-GGUF"), size_gb=5.4),
        budget=budget or model_scout.Budget(32, 12, 100),
        state={"prepared": [], "seen": []},
        say=said.append,
        log=logged,
        no_pull=True,
        stream=False,
        now=datetime(2026, 10, 22),
        probe_timeout_sec=5,
        num_ctx=ctx,
    )
    return code, said, logged


def test_prepare_stops_when_selected_file_is_larger_than_name_estimate(
    monkeypatch,
) -> None:
    code, said, logged = _prepare(monkeypatch, size_bytes=28 * GIB, config=_config())
    assert code == 0
    assert any("Skipping pull" in line and "resident" in line for line in said)
    assert any("32GB" in line for line in said)
    assert "SKIPPED pull" in logged[0]


def test_prepare_includes_architecture_kv_even_when_weights_alone_fit(
    monkeypatch,
) -> None:
    # 24 GiB weights + 4 GiB KV exceeds 27 GiB (32 minus existing 5 GiB reserve).
    code, said, logged = _prepare(monkeypatch, size_bytes=24 * GIB, config=_config())
    assert code == 0
    assert any("28GB" in line and "Skipping pull" in line for line in said)
    assert any("config architecture" in line for line in said)
    assert logged


def test_prepare_uses_conservative_f16_despite_unverified_cache_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OLLAMA_KV_CACHE_TYPE", "q4_0")
    code, said, logged = _prepare(monkeypatch, size_bytes=24 * GIB, config=_config())
    assert code == 0 and logged
    assert any("f16" in line and "4GB" in line for line in said)


def test_prepare_uses_selected_bytes_for_second_disk_guard(monkeypatch) -> None:
    code, said, logged = _prepare(
        monkeypatch, size_bytes=20 * GIB, budget=model_scout.Budget(64, 24, 31)
    )
    assert code == 0
    assert any("Low disk" in line and "32GB" in line for line in said)
    assert "low disk" in logged[0]


def test_missing_evidence_remains_explicit_parameter_estimate(monkeypatch) -> None:
    code, said, logged = _prepare(monkeypatch, size_bytes=None)
    assert code == 0 and not logged
    assert any("estimated weights" in line for line in said)
    assert any("parameter estimate" in line for line in said)
    assert any("--no-pull" in line for line in said)


def test_requested_context_is_not_silently_clamped_to_native_context(
    monkeypatch,
) -> None:
    _, said, logged = _prepare(
        monkeypatch, size_bytes=24 * GIB, config=_config(max_position_embeddings=8192)
    )
    assert logged
    assert any("above" in line and "native" in line for line in said)


def test_byte_boundary_is_not_rounded_down_before_residency_guard(monkeypatch) -> None:
    _, _, logged = _prepare(monkeypatch, size_bytes=23 * GIB + 1, config=_config())
    assert logged  # 23 GiB + one byte + 4 GiB KV exceeds 27 GiB.


def test_hf_selected_metadata_reads_are_bounded_and_paginated_tree_is_not_exact(
    monkeypatch,
) -> None:
    class Response(BytesIO):
        headers = {"Link": '<https://huggingface.co/next>; rel="next"'}

    monkeypatch.setattr(model_scout, "urlopen", lambda *args, **kw: Response(b"[]"))
    with pytest.raises(ValueError):
        model_scout.fetch_hf_tree("owner/model")
    monkeypatch.setattr(
        model_scout, "urlopen", lambda *args, **kw: BytesIO(b" " * (4 * 1024**2 + 1))
    )
    assert model_scout.fetch_hf_config("owner/model") is None


@pytest.mark.parametrize(
    "repo", ["owner/../other", "owner/model?x=1", "../model", "owner/model#x"]
)
def test_remote_metadata_rejects_repository_identity_injection(
    monkeypatch, repo
) -> None:
    monkeypatch.setattr(
        model_scout, "urlopen", lambda *args, **kw: pytest.fail("request sent")
    )
    assert model_scout.select_quant_artefact(repo) is None
    assert model_scout.fetch_hf_config(repo) is None


def test_malformed_config_json_remains_estimated(monkeypatch) -> None:
    for raw in [b'{"num_hidden_layers":32,"num_hidden_layers":1}', b"[" * 2000]:
        monkeypatch.setattr(
            model_scout, "urlopen", lambda *a, raw=raw, **kw: BytesIO(raw)
        )
        assert model_scout.fetch_hf_config("owner/model") is None


def test_prepare_pulls_the_quant_that_supplied_its_byte_evidence(monkeypatch) -> None:
    from localai.ops import CommandResult

    monkeypatch.setattr(
        model_scout,
        "fetch_hf_tree",
        lambda repo: [
            _file("m-UD-Q4_K_XL.gguf", 6 * GIB),
        ],
    )
    monkeypatch.setattr(model_scout, "fetch_hf_config", lambda repo: _config())
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "1")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return CommandResult(tuple(args), 1, "", "fixture download blocked")

    monkeypatch.setattr(model_scout, "run_ollama", run)
    said, logged = [], []
    code = model_scout.prepare_pick(
        model_scout.parse_model("owner/Model-9B-GGUF"),
        budget=model_scout.Budget(32, 12, 100),
        state={"prepared": [], "seen": []},
        say=said.append,
        log=logged,
        no_pull=False,
        stream=False,
        now=datetime(2026, 10, 22),
        probe_timeout_sec=5,
    )
    assert code == 1
    assert calls == [["pull", "hf.co/owner/Model-9B-GGUF:UD-Q4_K_XL"]] * 4
    assert any("selected artefact: 6GB" in line for line in said)
    assert not any("SKIPPED" in line for line in logged)


def test_prepare_invalid_repo_cannot_enter_download_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        model_scout, "urlopen", lambda *a, **kw: pytest.fail("request sent")
    )
    monkeypatch.setattr(
        model_scout, "run_ollama", lambda *a, **kw: pytest.fail("pull started")
    )
    code = model_scout.prepare_pick(
        replace(model_scout.parse_model("owner/Model-9B-GGUF"), id="owner/model?x=1"),
        budget=model_scout.Budget(32, 12, 100),
        state={"prepared": [], "seen": []},
        say=lambda line: None,
        log=[],
        no_pull=False,
        stream=False,
        now=datetime(2026, 10, 22),
        probe_timeout_sec=5,
    )
    assert code == 2


def test_missing_bytes_for_q8_uses_a_quant_floor_instead_of_q4_size(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        model_scout,
        "fetch_hf_tree",
        lambda repo: [
            _file("m-Q8_0.gguf", None),
        ],
    )
    monkeypatch.setattr(model_scout, "fetch_hf_config", lambda repo: None)
    monkeypatch.setattr(
        model_scout, "run_ollama", lambda *a, **kw: pytest.fail("pull started")
    )
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "1")
    said, logged = [], []
    code = model_scout.prepare_pick(
        model_scout.parse_model("owner/Model-30B-GGUF"),
        budget=model_scout.Budget(32, 12, 100),
        state={"prepared": [], "seen": []},
        say=said.append,
        log=logged,
        no_pull=True,
        stream=False,
        now=datetime(2026, 10, 22),
        probe_timeout_sec=5,
    )
    assert code == 0
    assert any("Skipping pull" in line for line in said)
    assert any("resident memory" in line for line in logged)
