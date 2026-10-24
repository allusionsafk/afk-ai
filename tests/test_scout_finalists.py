"""Final recommendations must reflect bounded metadata, never runtime claims."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from localai import model_scout as scout
from localai.scout_finalists import collect_final_scout_groups

GIB = 1024**3


@pytest.fixture(autouse=True)
def no_live_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout, "fetch_hf_tree", lambda repo: [])
    monkeypatch.setattr(scout, "fetch_hf_config", lambda repo: None)


def _candidate(repo: str, downloads: int = 1000) -> scout.Candidate:
    return replace(scout.parse_model(repo), downloads=downloads, age_days=1)


def test_report_rejects_a_name_based_winner_with_oversized_actual_weights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        _candidate("Qwen/Large-9B-GGUF", 200000),
        _candidate("Qwen/Small-8B-GGUF", 1000),
    ]
    monkeypatch.setattr(scout, "get_budget", lambda **kw: scout.Budget(32, 12, 100))
    monkeypatch.setattr(scout, "discover_candidates", lambda **kw: candidates)
    monkeypatch.setattr(scout, "load_state", lambda: {"prepared": [], "seen": []})
    monkeypatch.setattr(scout, "save_state", lambda state: None)
    monkeypatch.setattr(scout, "write_model_scout_log", lambda **kw: None)
    monkeypatch.setattr(
        scout, "fetch_hf_tree", lambda repo: [{
            "type": "file", "path": "model-Q4_K_M.gguf",
            "size": (40 if "Large" in repo else 4) * GIB,
        }],
    )
    monkeypatch.setattr(scout, "fetch_hf_config", lambda repo: None)
    captured: dict[str, scout.CategoryResult] = {}
    monkeypatch.setattr(
        scout, "write_scout_groups", lambda groups, **kw: captured.update(groups)
    )

    code, _ = scout.collect_model_scout_report(now=datetime(2026, 10, 24, 12))

    assert code == 0
    top = captured["chat"].top
    assert top is not None
    assert top.id == "Qwen/Small-8B-GGUF"
    assert "Large-9B" in dict(captured["chat"].dropped)


def test_cache_cannot_publish_provisional_name_only_estimates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(scout, "repo_path", lambda *parts: tmp_path.joinpath(*parts))
    groups = scout.collect_scout_groups(scout.Budget(32, 12, 100), [])

    with pytest.raises(ValueError, match="final"):
        scout.write_scout_groups(groups, now=datetime(2026, 10, 24))

    assert not (tmp_path / "logs" / "model-scout-groups.json").exists()


def test_discovery_keeps_same_named_repositories_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scout, "AUTHORS", ("owner1", "owner2"))
    monkeypatch.setattr(scout, "fetch_hf_models", lambda author: [{
        "id": f"{author}/Model-9B-GGUF", "downloads": 1000,
        "lastModified": "2026-10-23T00:00:00Z",
    }])
    candidates = scout.discover_candidates(
        budget=scout.Budget(32, 12, 100), notes=[], now=datetime(2026, 10, 24)
    )

    assert {candidate.id for candidate in candidates} == {
        "owner1/Model-9B-GGUF", "owner2/Model-9B-GGUF"
    }


def test_shortlist_caps_remote_repositories_and_reuses_across_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def tree(repo: str) -> list[object]:
        calls.append(("tree", repo))
        return [{"path": "model-Q4_K_M.gguf", "size": 4 * GIB, "type": "file"}]

    def config(repo: str) -> None:
        calls.append(("config", repo))

    monkeypatch.setattr(scout, "fetch_hf_tree", tree)
    monkeypatch.setattr(scout, "fetch_hf_config", config)
    candidates = [_candidate(f"Qwen/Model{i}-8B-GGUF") for i in range(20)]
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), candidates)

    assert len(calls) == 6  # same three shared by chat, coding, web-nav
    assert len(set(calls)) == 6
    assert groups["chat"].top is not None
    assert groups["chat"].top.id == "Qwen/Model0-8B-GGUF"
    # Input order never changes tie selection or produces another fetch budget.
    calls.clear()
    reversed_groups = collect_final_scout_groups(
        scout.Budget(32, 12, 100), list(reversed(candidates))
    )
    assert reversed_groups == groups
    assert len(calls) == 6


def test_global_request_cap_fairly_admits_categories_before_second_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def tree(repo: str) -> list[object]:
        calls.append(repo)
        return []

    monkeypatch.setattr(scout, "fetch_hf_tree", tree)
    candidates = [
        _candidate(f"Qwen/{kind}{i}-{size}B-GGUF", 200000)
        for i in range(5)
        for kind, size in (("General", 8), ("Coder", 8), ("Vision", 8),
                           ("Embed", 1), ("Voice", 1))
    ]
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), candidates)

    assert len(calls) == 6
    assert set(calls[:5]) == {
        "Qwen/General0-8B-GGUF", "Qwen/Coder0-8B-GGUF",
        "Qwen/Vision0-8B-GGUF", "Qwen/Embed0-1B-GGUF", "Qwen/Voice0-1B-GGUF",
    }
    assert calls[5] == "Qwen/General1-8B-GGUF"
    unchecked = [c for c in (groups["vision"].top, *groups["vision"].runners_up)
                 if c and c.id == "Qwen/Vision1-8B-GGUF"]
    assert len(unchecked) == 1
    assert unchecked[0].metadata_status == "not-checked"
    assert "Metadata not checked" in unchecked[0].fit_why


def test_expired_admission_budget_starts_no_more_repository_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moment = [0.0]
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "localai.scout_finalists.monotonic", lambda: moment[0], raising=False
    )

    def tree(repo: str) -> list[object]:
        calls.append(("tree", repo))
        moment[0] = 20  # the in-flight request may outlast the admission window
        return []

    def config(repo: str) -> None:
        calls.append(("config", repo))

    monkeypatch.setattr(scout, "fetch_hf_tree", tree)
    monkeypatch.setattr(scout, "fetch_hf_config", config)
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [
        _candidate("Qwen/General-8B-GGUF", 200000),
        _candidate("Qwen/Coder-8B-GGUF", 200000),
    ])
    assert calls == [
        ("tree", "Qwen/General-8B-GGUF"), ("config", "Qwen/General-8B-GGUF")
    ]
    top = groups["coding"].top
    assert top is not None and top.id == "Qwen/Coder-8B-GGUF"
    assert top.metadata_status == "not-checked"
    assert top.weight_source == "parameter-estimate"


def test_same_name_mirror_cannot_borrow_other_repository_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scout, "fetch_hf_tree", lambda repo: [{
        "path": "model-Q4_K_M.gguf", "type": "file",
        "size": (40 if repo.startswith("large/") else 4) * GIB,
    }])
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [
        _candidate("large/Model-9B-GGUF", 200000),
        _candidate("small/Model-9B-GGUF"),
    ])
    top = groups["chat"].top
    assert top is not None and top.id == "small/Model-9B-GGUF"
    assert top.weight_gb == 4.0
    assert top.weight_source == "repository-bytes"


def test_standard_architecture_f16_overrides_client_environment_and_rejects_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_KV_CACHE_TYPE", "q4_0")
    monkeypatch.setattr(scout, "fetch_hf_tree", lambda repo: [{
        "path": "model-Q4_K_M.gguf", "type": "file", "size": 8 * GIB,
    }])
    monkeypatch.setattr(scout, "fetch_hf_config", lambda repo: {
        "model_type": "llama", "num_hidden_layers": 32,
        "num_attention_heads": 32, "num_key_value_heads": 32, "hidden_size": 4096,
    })
    groups = collect_final_scout_groups(
        scout.Budget(16, 12, 100), [_candidate("Qwen/Model-9B-GGUF")]
    )
    # 8GiB weights + 8GiB f16 KV@16k exceed 11GiB host budget.
    assert "Model-9B" in dict(groups["chat"].dropped)
    assert "config-f16-estimate" in dict(groups["chat"].dropped)["Model-9B"]


def test_unsupported_attention_stays_parameter_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scout, "fetch_hf_config", lambda repo: {
        "model_type": "deepseek_v3", "num_hidden_layers": 1,
        "num_attention_heads": 1, "hidden_size": 1,
    })
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [
        _candidate("Qwen/Model-9B-GGUF", 200000),
    ])
    top = groups["chat"].top
    assert top is not None and top.id == "Qwen/Model-9B-GGUF"
    assert top.kv_gb == 2.56
    assert top.kv_source == "parameter-f16-estimate"
    assert "unverified" in top.fit_why


def test_partial_shards_keep_conservative_quant_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scout, "fetch_hf_tree", lambda repo: [{
        "path": "model-Q8_0-00001-of-00002.gguf", "type": "file", "size": GIB,
    }])
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [
        _candidate("Qwen/Model-9B-GGUF", 200000),
    ])
    finalists = (groups["chat"].top, *groups["chat"].runners_up)
    candidate = next(c for c in finalists if c and c.id == "Qwen/Model-9B-GGUF")
    assert candidate.weight_source == "parameter-estimate"
    assert candidate.weight_gb is not None and candidate.weight_gb > 8.9
    assert candidate.quant == "Q8_0"


def test_metadata_failure_keeps_estimate_without_runtime_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(repo: str) -> list[object]:
        raise OSError("offline")

    monkeypatch.setattr(scout, "fetch_hf_tree", unavailable)
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [
        _candidate("Qwen/Model-9B-GGUF", 200000),
    ])
    top = groups["chat"].top
    assert top is not None and top.id == "Qwen/Model-9B-GGUF"
    assert top.weight_source == "parameter-estimate"
    assert top.weight_gb == 5.4
    assert "runtime support, allocation and speed unverified" in top.fit_why


def test_moe_does_not_fit_only_active_expert_weights() -> None:
    groups = collect_final_scout_groups(scout.Budget(24, 12, 100), [
        _candidate("Qwen/Model-35B-A3B-GGUF", 200000),
    ])
    assert "Model-35B-A3B" in dict(groups["chat"].dropped)


def test_curated_unknown_size_is_never_assumed_to_fit() -> None:
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [])
    top = groups["embedding"].top
    assert top is not None and top.verdict == "Unknown"
    assert top.size_gb is None
    assert "Unverified curated" in top.fit_why


def test_native_context_limit_is_visible_without_claiming_runtime_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scout, "fetch_hf_config", lambda repo: {
        "model_type": "llama", "num_hidden_layers": 32,
        "num_attention_heads": 32, "num_key_value_heads": 8,
        "hidden_size": 4096, "max_position_embeddings": 8192,
    })
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [
        _candidate("Qwen/Model-9B-GGUF", 200000),
    ], parallel=2)
    top = groups["chat"].top
    assert top is not None and top.id == "Qwen/Model-9B-GGUF"
    assert top.kv_gb == 4.0
    assert "requested context exceeds native 8192" in top.fit_why
    assert "unverified" in top.fit_why


@pytest.mark.parametrize("parallel", [0, -1, True, 257, 1.5])
def test_invalid_parallel_rejected_before_metadata(
    monkeypatch: pytest.MonkeyPatch, parallel: int,
) -> None:
    def unexpected(repo: str) -> list[object]:
        pytest.fail("invalid fit inputs reached metadata")

    monkeypatch.setattr(scout, "fetch_hf_tree", unexpected)
    with pytest.raises(ValueError, match="parallel"):
        collect_final_scout_groups(scout.Budget(32, 12, 100), [], parallel=parallel)


def test_invalid_repository_does_not_reach_any_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(repo: str) -> list[object]:
        pytest.fail("invalid repository reached metadata")

    monkeypatch.setattr(scout, "fetch_hf_tree", unexpected)
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [
        _candidate("owner/../elsewhere-9B-GGUF"),
    ])
    assert groups["chat"].top is not None
    assert groups["chat"].top.author == "curated"


def _write_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(scout, "repo_path", lambda *parts: tmp_path.joinpath(*parts))
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [])
    scout.write_scout_groups(
        groups, now=datetime(2026, 10, 24), budget=scout.Budget(32, 12, 100)
    )
    return tmp_path / "logs" / "model-scout-groups.json"


def test_cache_is_dated_snapshot_with_fit_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _write_cache(monkeypatch, tmp_path)
    payload = scout.read_scout_groups()
    assert payload is not None
    assert payload["evidenceStatus"] == "cached-estimate"
    assert payload["scope"] == "bounded-shortlist"
    assert payload["fitContext"]["ramGb"] == 32
    assert payload["fitContext"]["kvDtype"] == "f16"
    assert len(payload["fitFingerprint"]) == 64
    assert payload["groups"]["chat"]["top"]["runtimeStatus"] == "unverified"


@pytest.mark.parametrize("mutation", [
    "stage", "runtime", "fingerprint", "hardware", "quant", "provenance",
    "verdict", "size", "weights", "context", "duplicate", "partial-groups",
    "float-schema", "float-context", "nonfinite-display", "surrogate-display",
])
def test_cache_rejects_contradictory_or_incomplete_snapshots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str,
) -> None:
    path = _write_cache(monkeypatch, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    top = payload["groups"]["chat"]["top"]
    if mutation == "stage":
        top["fitStage"] = "provisional"
    elif mutation == "runtime":
        top["runtimeStatus"] = "Ready"
    elif mutation == "fingerprint":
        payload["fitFingerprint"] = "0" * 64
    elif mutation == "hardware":
        payload["fitContext"]["vramGb"] = 100
    elif mutation == "quant":
        top["quant"] = "Q4; execute"
    elif mutation == "provenance":
        top["weightSource"] = "repository-bytes"
    elif mutation == "verdict":
        top["verdict"] = "Poor"
    elif mutation == "size":
        top["sizeGb"] = 0
    elif mutation == "weights":
        top["weightGb"] = True
    elif mutation == "context":
        payload["fitContext"]["categoryContexts"]["chat"] = 1
    elif mutation == "duplicate":
        payload["groups"]["chat"]["runnersUp"] = [top]
    elif mutation == "float-schema":
        payload["schema"] = 2.0
    elif mutation == "float-context":
        payload["fitContext"]["categoryContexts"]["chat"] = 16384.0
    elif mutation == "nonfinite-display":
        top["downloads"] = float("nan")
    elif mutation == "surrogate-display":
        top["name"] = "\ud800"
    else:
        del payload["groups"]["voice"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert scout.read_scout_groups() is None


def test_changed_fit_inputs_change_cache_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    path = _write_cache(monkeypatch, tmp_path)
    original = json.loads(path.read_text(encoding="utf-8"))["fitFingerprint"]
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [], parallel=2)
    scout.write_scout_groups(
        groups, now=datetime(2026, 10, 24), budget=scout.Budget(32, 12, 100),
        parallel=2,
    )
    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["fitFingerprint"] != original
    assert scout.read_scout_groups() is not None


@pytest.mark.parametrize("raw", [
    b'{"generated":"2026-10-24 12:00","groups":{}}',
    b'{"schema":2,"schema":2}', b'[]', b'null', b'"text"',
    b'[' * 2000, b' ' * (512 * 1024 + 1), b'\xff',
], ids=["legacy", "duplicate-key", "array", "null", "string", "deep", "large", "utf8"])
def test_cache_read_is_bounded_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: bytes,
) -> None:
    monkeypatch.setattr(scout, "repo_path", lambda *parts: tmp_path.joinpath(*parts))
    path = tmp_path / "logs" / "model-scout-groups.json"
    path.parent.mkdir()
    path.write_bytes(raw)
    assert scout.read_scout_groups() is None


def test_failed_atomic_replace_preserves_existing_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from localai import scout_cache

    path = _write_cache(monkeypatch, tmp_path)
    original = path.read_bytes()

    def denied(source: str, destination: Path) -> None:
        raise OSError("simulated sharing violation")

    monkeypatch.setattr(scout_cache.os, "replace", denied)
    groups = collect_final_scout_groups(scout.Budget(32, 12, 100), [])
    with pytest.raises(OSError, match="sharing"):
        scout.write_scout_groups(
            groups, now=datetime(2026, 10, 24, 1), budget=scout.Budget(32, 12, 100)
        )
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]
