"""Reproducible bounded Ollama versus llama.cpp evidence runner.

This CLI is for the competition spike. It does not select or package a runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from experiments.llamacpp_competition.adapter import (
    LlamaCppConfig,
    benchmark_llamacpp,
    profile_llamacpp,
)
from localai.optimizer import NUM_PREDICT, Measurement, estimate_context
from localai.optimizer_ollama import (
    benchmark_ollama,
    profile_hardware,
    profile_ollama,
    unload_ollama,
)

CONTEXTS = (4096, 8192, 16384)


def deterministic_long_prompt(records: int = 200) -> str:
    """Return a non-private, stable payload intended to tokenize near 5K."""
    lines = [
        "Read the following synthetic equipment log and summarize recurring "
        "states, checksum patterns, and any inconsistent record in plain language."
    ]
    for index in range(1, records + 1):
        checksum = (index * 17) % 997
        lines.append(
            f"Record {index:04d}: amber valve, copper wheel, cedar frame, and "
            f"quartz gauge remain stable; checksum {checksum:04d}."
        )
    lines.append("Give a compact answer based only on these synthetic records.")
    return "\n".join(lines)


def _measurement(value: Measurement) -> dict[str, Any]:
    return asdict(value)


def run(
    *,
    model_tag: str,
    executable: Path,
    model_path: Path,
    model_sha256: str,
    output: Path,
    log_dir: Path,
) -> dict[str, Any]:
    hardware = profile_hardware()
    model, ollama_runtime = profile_ollama(model_tag)
    expected_blob_name = "sha256-" + model_sha256.lower()
    if model_path.name.lower() != expected_blob_name:
        raise RuntimeError(
            f"GGUF blob name {model_path.name!r} did not match {expected_blob_name}"
        )
    config = LlamaCppConfig(executable, model_path, model_sha256=model_sha256)
    llama_model, llama_runtime = profile_llamacpp(config, model)
    estimates = [estimate_context(hardware, model, context) for context in CONTEXTS]
    safe_contexts = tuple(
        estimate.context for estimate in estimates if estimate.safe
    )
    if not safe_contexts:
        raise RuntimeError("optimizer policy rejected every requested context rung")

    short: dict[str, dict[str, Any]] = {"ollama": {}, "llama.cpp": {}}
    for context in safe_contexts:
        short["ollama"][str(context)] = _measurement(
            benchmark_ollama(model_tag, context, timeout_sec=180)
        )
        unload_ollama(model_tag)
    for context in safe_contexts:
        context_config = replace(
            config, log_path=log_dir / f"llama-{context}.log"
        )
        short["llama.cpp"][str(context)] = _measurement(
            benchmark_llamacpp(context_config, context)
        )

    prompt = deterministic_long_prompt()
    prompt_variants = tuple(
        f"Synthetic benchmark pass {index}; ignore this marker.\n{prompt}"
        for index in range(1, 5)
    )
    long_context = safe_contexts[-1]
    long_ollama = benchmark_ollama(
        model_tag,
        long_context,
        timeout_sec=300,
        prompt=prompt,
        num_predict=NUM_PREDICT,
        prompt_variants=prompt_variants,
    )
    unload_ollama(model_tag)
    long_llama = benchmark_llamacpp(
        replace(config, request_timeout_sec=300, log_path=log_dir / "llama-long.log"),
        long_context,
        prompt=prompt,
        num_predict=NUM_PREDICT,
        prompt_variants=prompt_variants,
    )
    result = {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(),
        "contract": {
            "contexts": list(CONTEXTS),
            "measured_contexts": list(safe_contexts),
            "policy_excluded_contexts": [
                estimate.context for estimate in estimates if not estimate.safe
            ],
            "generated_tokens": NUM_PREDICT,
            "warmups": 1,
            "measured_runs": 3,
            "aggregation": "median",
            "long_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "long_prompt_variant_sha256": [
                hashlib.sha256(variant.encode()).hexdigest()
                for variant in prompt_variants
            ],
        },
        "hardware": asdict(hardware),
        "model": asdict(llama_model),
        "comparison_identity": {
            "ollama_manifest_digest": model.digest,
            "shared_gguf_layer_sha256": model_sha256.lower(),
            "same_underlying_gguf": True,
        },
        "runtimes": {
            "ollama": asdict(ollama_runtime),
            "llama.cpp": asdict(llama_runtime),
        },
        "estimates": [asdict(estimate) for estimate in estimates],
        "short_prompt": short,
        "long_prompt": {
            "configured_context": long_context,
            "characters": len(prompt),
            "ollama": _measurement(long_ollama),
            "llama.cpp": _measurement(long_llama),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-tag", default="qwen2.5:14b")
    parser.add_argument("--llama-server", required=True, type=Path)
    parser.add_argument("--model-gguf", required=True, type=Path)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--log-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        run(
            model_tag=args.model_tag,
            executable=args.llama_server,
            model_path=args.model_gguf,
            model_sha256=args.model_sha256,
            output=args.output,
            log_dir=args.log_dir,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"schema_version": 1, "error": str(error)}))
        return 1
    print(str(args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
