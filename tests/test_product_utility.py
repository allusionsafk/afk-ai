"""Product-facing fit boundaries: exact local bytes, honest estimates and identity."""

from dataclasses import replace

from localai.optimizer import GIB, GPU, HardwareProfile, ModelProfile
from localai.product_utility import artifact_size_from_files, build_report


def _pc() -> HardwareProfile:
    return HardwareProfile(
        cpu="Intel i9",
        logical_cores=32,
        ram_total_bytes=32 * GIB,
        ram_available_bytes=18 * GIB,
        gpus=(GPU("Example 12 GiB GPU", 12 * GIB, 10 * GIB),),
        platform="Windows 11",
    )


def _model(name: str = "chat:latest") -> ModelProfile:
    return ModelProfile(
        identifier=name,
        digest="a" * 64,
        family="qwen3",
        parameter_count=9_000_000_000,
        quantization="Q4_K_M",
        artifact_bytes=6 * GIB,
        max_context=131_072,
        kv_bytes_per_token=131_072,
        architecture={"block_count": 32},
    )


def test_exact_local_size_overrides_parameter_heuristic_and_context_changes_fit():
    model = _model()
    at_8k = build_report(_pc(), [model], None, {}, context=8192)
    row = at_8k["models"][0]
    assert row["weights_bytes"] == 6 * GIB
    assert row["weights_source"] == "ollama exact model size"
    assert row["kv_bytes"] == 131_072 * 8192
    assert row["kv_source"] == "architecture estimate"
    assert row["verdict"] == "Fits"
    at_64k = build_report(_pc(), [model], None, {}, context=65_536)
    assert at_64k["models"][0]["verdict"] == "RAM-assisted"


def test_moe_full_weights_must_fit_even_with_only_3b_active():
    moe = replace(
        _model("qwen3.6-35b-a3b:latest"),
        parameter_count=35_000_000_000,
        artifact_bytes=21 * GIB,
    )
    row = build_report(_pc(), [moe], None, {}, context=8192)["models"][0]
    assert row["weights_bytes"] == 21 * GIB
    assert row["verdict"] == "RAM-assisted"
    assert "VRAM" in row["reason"]


def test_missing_or_malformed_metadata_stays_unknown():
    valid = _model()
    missing = replace(valid, artifact_bytes=None)
    huge = replace(valid, identifier="huge:latest", artifact_bytes=10**100)
    invalid = replace(valid, identifier="bad\nname")
    report = build_report(_pc(), [missing, huge, invalid], None, {}, context=8192)
    assert [row["verdict"] for row in report["models"]] == ["Unknown", "Unknown"]
    assert report["recommendation"] is None


def test_configured_and_loaded_are_separate_from_installed_and_recommended():
    configured = _model("chat:configured")
    other = replace(_model("chat:other"), artifact_bytes=7 * GIB)
    report = build_report(
        _pc(), [configured, other], "chat:configured", {"chat:configured": "b" * 64},
        context=8192,
    )
    rows = {row["model"]: row for row in report["models"]}
    assert rows["chat:configured"]["installed"] is True
    assert rows["chat:configured"]["configured"] is True
    assert rows["chat:configured"]["loaded"] is False
    assert rows["chat:other"]["configured"] is False
    assert report["recommendation"]["model"] == "chat:configured"
    assert report["recommendation"]["basis"] == "configured model fits in VRAM"


def test_sharded_artifact_requires_one_complete_unique_set():
    files = [
        ("model-Q4_K_M-00001-of-00002.gguf", 3 * GIB),
        ("model-Q4_K_M-00002-of-00002.gguf", 2 * GIB),
        ("model-Q8_0.gguf", 8 * GIB),
    ]
    exact = artifact_size_from_files(files, "Q4_K_M")
    assert exact is not None and exact.bytes == 5 * GIB
    assert len(exact.files) == 2
    assert artifact_size_from_files(files[:1], "Q4_K_M") is None
    assert artifact_size_from_files(files + [files[0]], "Q4_K_M") is None
    assert (
        artifact_size_from_files(files + [("model-Q4_K_M.gguf", 5 * GIB)], "Q4_K_M")
        is None
    )
    assert artifact_size_from_files([(files[0][0], -1), files[1]], "Q4_K_M") is None


def test_quant_selection_never_mistakes_ud_variant_for_plain_quant():
    files = [("model-UD-Q4_K_M.gguf", 5 * GIB)]
    assert artifact_size_from_files(files, "Q4_K_M") is None
    assert artifact_size_from_files(files, "UD-Q4_K_M") is not None
