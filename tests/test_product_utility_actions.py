"""Selection and context application may change product state only with evidence."""

import pytest

from localai import optimizer, product_config, product_utility_actions

TAG = "qwen:9b"
DIGEST = "a" * 64


def _layout(tmp_path):
    layout = product_config.resolve_layout(tmp_path / "program", tmp_path / "data")
    product_config.ensure_runtime_config(layout, model=TAG)
    return layout


def _fetch(path, body=None):
    assert path == "/api/tags"
    return {"models": [{"name": TAG, "digest": DIGEST, "size": 6 * optimizer.GIB}]}


def _model():
    return optimizer.ModelProfile(
        TAG,
        DIGEST,
        "qwen3",
        9_000_000_000,
        "Q4_K_M",
        6 * optimizer.GIB,
        32768,
        65536,
        {"block_count": 32},
    )


def _hardware():
    return optimizer.HardwareProfile(
        "Intel i9",
        32,
        32 * optimizer.GIB,
        24 * optimizer.GIB,
        (optimizer.GPU("RTX 4080", 12 * optimizer.GIB, 10 * optimizer.GIB),),
        "Windows 11",
    )


def test_use_refuses_vanished_model_without_changing_configuration(tmp_path):
    layout = _layout(tmp_path)
    old = layout.runtime_env.read_bytes()
    with pytest.raises(ValueError, match="not installed"):
        product_utility_actions.use_installed_model(
            layout, "other:latest", fetch=_fetch, seed=lambda *a: pytest.fail("seeded")
        )
    assert layout.runtime_env.read_bytes() == old


def test_use_seeds_owned_chat_before_atomic_config_change(tmp_path):
    layout = _layout(tmp_path)
    seen = []
    result = product_utility_actions.use_installed_model(
        layout,
        TAG,
        fetch=_fetch,
        seed=lambda model, context: seen.append((model, context)) or True,
    )
    assert result["configured_model"] == TAG
    assert seen == [(TAG, None)]
    assert product_config.configured_source_model(layout) == TAG


def test_failed_chat_seed_preserves_previous_selection(tmp_path):
    layout = _layout(tmp_path)
    old = layout.runtime_env.read_bytes()
    with pytest.raises(RuntimeError, match="chat"):
        product_utility_actions.use_installed_model(
            layout, TAG, fetch=_fetch, seed=lambda *a: False
        )
    assert layout.runtime_env.read_bytes() == old


def test_changed_model_digest_aborts_selection_without_config_change(tmp_path):
    layout = _layout(tmp_path)
    old = layout.runtime_env.read_bytes()
    calls = 0

    def changing_fetch(path, body=None):
        nonlocal calls
        calls += 1
        return {"models": [{"name": TAG, "digest": ("a" if calls == 1 else "b") * 64}]}

    with pytest.raises(RuntimeError, match="identity changed"):
        product_utility_actions.use_installed_model(
            layout, TAG, fetch=changing_fetch, seed=lambda *a: True
        )
    assert layout.runtime_env.read_bytes() == old


def test_context_creation_failure_does_not_change_configuration(tmp_path):
    layout = _layout(tmp_path)
    old = layout.runtime_env.read_bytes()
    with pytest.raises(RuntimeError, match="create"):
        product_utility_actions.apply_context(
            layout,
            TAG,
            4096,
            override=True,
            fetch=_fetch,
            profile=lambda _: (
                _model(),
                optimizer.RuntimeProfile("ollama", "0.34.4", None, {}),
            ),
            hardware=_hardware,
            create=lambda *a: (False, "failed"),
            seed=lambda *a: pytest.fail("seeded"),
        )
    assert layout.runtime_env.read_bytes() == old


def test_override_and_afk_recommendation_remain_distinct(tmp_path):
    layout = _layout(tmp_path)
    result = product_utility_actions.apply_context(
        layout,
        TAG,
        4096,
        override=True,
        fetch=_fetch,
        profile=lambda _: (
            _model(),
            optimizer.RuntimeProfile("ollama", "0.34.4", None, {}),
        ),
        hardware=_hardware,
        create=lambda *a: (True, "created"),
        seed=lambda *a: True,
    )
    assert result["selected_context"] == 4096
    assert result["recommended_context"] is None
    assert result["override_context"] == 4096
    assert product_config.read_utility_selection(layout)["override_context"] == 4096
    assert product_config.configured_model(layout) == result["configured_model"]


def test_unmeasured_context_cannot_be_applied_as_afk_recommendation(tmp_path):
    layout = _layout(tmp_path)
    old = layout.runtime_env.read_bytes()
    with pytest.raises(ValueError, match="evidenced"):
        product_utility_actions.apply_context(
            layout,
            TAG,
            4096,
            override=False,
            fetch=_fetch,
            profile=lambda _: (
                _model(),
                optimizer.RuntimeProfile("ollama", "0.34.4", None, {}),
            ),
            hardware=_hardware,
            create=lambda *a: pytest.fail("created"),
            seed=lambda *a: pytest.fail("seeded"),
        )
    assert layout.runtime_env.read_bytes() == old


@pytest.mark.parametrize("context", [0, 511, 3333, 1_048_576])
def test_invalid_context_is_rejected_before_creation(tmp_path, context):
    layout = _layout(tmp_path)
    old = layout.runtime_env.read_bytes()
    with pytest.raises(ValueError):
        product_utility_actions.apply_context(
            layout,
            TAG,
            context,
            override=True,
            fetch=_fetch,
            profile=lambda _: pytest.fail("profiled"),
            hardware=_hardware,
            create=lambda *a: pytest.fail("created"),
            seed=lambda *a: True,
        )
    assert layout.runtime_env.read_bytes() == old
