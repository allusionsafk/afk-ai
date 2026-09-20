"""Diagnostic CLI for a local, bounded adaptive inference run.

Usage: python -m localai.optimizer_cli MODEL [--mode balanced] [--max-context 8192]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from localai.optimizer import MeasurementCache, optimize
from localai.optimizer_ollama import benchmark_ollama, profile_hardware, profile_ollama


def default_cache_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "AFK AI" / "optimizer" / "measurements-v1.json"
    return Path.home() / ".local" / "share" / "afk-ai" / "measurements-v1.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", help="exact installed Ollama model tag")
    parser.add_argument(
        "--mode",
        choices=("interactive", "balanced", "long-context"),
        default="balanced",
    )
    parser.add_argument("--max-context", type=int, default=None)
    parser.add_argument("--override-context", type=int, default=None)
    parser.add_argument("--cache", type=Path, default=default_cache_path())
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--cached-only", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=90)
    args = parser.parse_args(argv)
    if args.max_context is not None and args.max_context < 1024:
        parser.error("--max-context must be at least 1024")
    if args.timeout_sec < 1 or args.timeout_sec > 300:
        parser.error("--timeout-sec must be between 1 and 300")
    try:
        hardware = profile_hardware()
        model, runtime = profile_ollama(args.model)
        result = optimize(
            hardware,
            model,
            runtime,
            lambda tag, context: benchmark_ollama(
                tag, context, timeout_sec=args.timeout_sec
            ),
            MeasurementCache(args.cache),
            mode=args.mode,
            cap=args.max_context,
            override_context=args.override_context,
            use_cache=not args.no_cache,
            measure=not args.cached_only,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"schema_version": 1, "error": str(error)}))
        return 1
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.recommended_context is not None else 1


if __name__ == "__main__":
    sys.exit(main())
