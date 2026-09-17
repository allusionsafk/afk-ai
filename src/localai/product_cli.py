"""The installed product's command surface.

Reached only through ``installer/afk-payload.py``, which first proves it is
running on AFK AI's own pinned interpreter and importing this installation's own
package. Everything here is standard library only: the owned runtime carries no
third-party packages, so nothing is resolved from an index at install time and
nothing ambient can be imported.

Machine-readable output contract (the native shell parses these):

- ``status --json``        exactly one JSON line (readiness schema 2)
- ``diagnostics``          one JSON document
- ``runtime-info``         one JSON line
- ``start`` / ``pull-model`` ``AFK-EVENT:`` lines while running; ``start`` ends
                            with one ``AFK-STATUS:`` line
- everything else          human-readable lines

Exit codes: 0 success, 1 the product is not usable / the step failed,
2 bad arguments, 3 refused (layout or ownership could not be proven).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from localai import readiness
from localai.afk_ownership import collect_afk_stop_report
from localai.installation import read_runtime_identity, verify_installation
from localai.product_config import (
    LayoutError,
    ProductLayout,
    configured_model,
    ensure_runtime_config,
    resolve_layout,
    valid_model_tag,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3


def _parser() -> argparse.ArgumentParser:
    # The roots are accepted before OR after the command. SUPPRESS keeps a
    # subparser's default from overwriting a value parsed before the command,
    # which plain argparse defaults silently do.
    roots = argparse.ArgumentParser(add_help=False)
    roots.add_argument("--program-root", default=argparse.SUPPRESS)
    roots.add_argument("--data-root", default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="afk-payload.py", parents=[roots])
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", parents=[roots])
    status.add_argument("--json", action="store_true")
    status.add_argument("--liveness", action="store_true")

    for name in ("start", "stop", "runtime-info", "aliases"):
        sub.add_parser(name, parents=[roots])

    diagnostics = sub.add_parser("diagnostics", parents=[roots])
    diagnostics.add_argument("--output", default="")

    configure = sub.add_parser("configure", parents=[roots])
    configure.add_argument("--model", default=None)

    pull = sub.add_parser("pull-model", parents=[roots])
    pull.add_argument("--source", required=True)
    pull.add_argument("--tag", required=True)
    pull.add_argument("--num-ctx", type=int, required=True)

    seed = sub.add_parser("seed-webui", parents=[roots])
    seed.add_argument("--model", required=True)
    seed.add_argument("--num-ctx", type=int, required=True)
    return parser


def _layout(args: argparse.Namespace) -> ProductLayout:
    data_root = getattr(args, "data_root", "")
    if not data_root:
        raise LayoutError("--data-root is required for this command.")
    return resolve_layout(args.program_root, data_root)


def _print(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main(argv: Sequence[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        # The native shell reads UTF-8; Windows pipes default to the ANSI code
        # page, where a non-ASCII path or model name would raise mid-report.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    try:
        args = _parser().parse_args(list(argv))
    except SystemExit as exit_request:
        return EXIT_USAGE if exit_request.code else EXIT_OK

    command: str = args.command
    if not getattr(args, "program_root", ""):
        sys.stderr.write("--program-root is required.\n")
        return EXIT_USAGE
    if command == "stop":
        # The uninstaller runs this. It must succeed whenever nothing provable
        # can be stopped, so it needs only the program root.
        code, lines = collect_afk_stop_report(program_root=args.program_root)
        for line in lines:
            _print(line)
        return code

    try:
        layout = _layout(args)
    except LayoutError as error:
        sys.stderr.write(f"{error}\n")
        if command == "status" and args.json:
            refused = readiness.ProductStatus()
            refused.state = readiness.UNKNOWN
            refused.reason = "STATUS_ERROR"
            refused.message = "AFK AI was started with an invalid data folder."
            _print(refused.to_json())
        return EXIT_REFUSED

    if command == "status":
        snapshot = readiness.collect_product_status(
            program_root=layout.program_root,
            configured_model=configured_model(layout),
            verify_inference=not args.liveness,
        )
        if args.json:
            _print(snapshot.to_json())
        else:
            _print(f"AFK AI: {snapshot.state} ({snapshot.reason})")
            _print(snapshot.message)
            for service in snapshot.services:
                _print(f"  {service.name:<14} {service.state:<11} {service.detail}")
        return (
            EXIT_OK
            if snapshot.state in (readiness.READY, readiness.LIVE)
            else EXIT_FAILED
        )

    if command == "runtime-info":
        identity = read_runtime_identity(layout.program_root)
        integrity = verify_installation(layout.program_root)
        import platform

        _print(
            json.dumps(
                {
                    "schema_version": 1,
                    "python_version": platform.python_version(),
                    "isolated": bool(sys.flags.isolated),
                    "site_disabled": bool(sys.flags.no_site),
                    "pinned_version": identity.version if identity else None,
                    "installation": integrity.to_dict(),
                },
                sort_keys=True,
            )
        )
        return EXIT_OK if integrity.intact else EXIT_FAILED

    if command == "diagnostics":
        from localai.diagnostics import collect_diagnostics

        target = Path(args.output) if args.output else None
        # Validate the destination before observing anything on this PC.
        if target is not None:
            if not target.is_absolute() or target.suffix.lower() != ".json":
                sys.stderr.write("--output must be an absolute .json path.\n")
                return EXIT_USAGE
            try:
                target.resolve().relative_to(layout.diagnostics_root.resolve())
            except ValueError:
                sys.stderr.write("--output must be inside the diagnostics folder.\n")
                return EXIT_REFUSED
        bundle = json.dumps(collect_diagnostics(layout), indent=2, sort_keys=True)
        if target is None:
            _print(bundle)
            return EXIT_OK
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(bundle + "\n", encoding="utf-8")
        return EXIT_OK

    if command == "configure":
        try:
            result = ensure_runtime_config(layout, model=args.model)
        except ValueError as error:
            sys.stderr.write(f"{error}\n")
            return EXIT_USAGE
        _print(
            "Runtime configuration "
            + ("updated." if result.changed else "already current.")
        )
        if result.removed_legacy_env:
            _print("Moved the old service secret out of the program folder.")
        return EXIT_OK

    from localai import product_runtime

    if command == "start":
        code, snapshot = product_runtime.start_product(layout)
        _print(product_runtime.STATUS_PREFIX + snapshot.to_json())
        return code

    if command == "pull-model":
        if not (valid_model_tag(args.source) and valid_model_tag(args.tag)):
            sys.stderr.write("Invalid model name.\n")
            return EXIT_USAGE
        ok, detail = product_runtime.pull_model(args.source)
        if not ok:
            sys.stderr.write(detail + "\n")
            return EXIT_FAILED
        created, detail = product_runtime.create_context_tag(
            args.source, args.tag, args.num_ctx
        )
        _print(detail)
        return EXIT_OK if created else EXIT_FAILED

    if command == "seed-webui":
        if not valid_model_tag(args.model):
            sys.stderr.write("Invalid model name.\n")
            return EXIT_USAGE
        from localai.webui_seed import collect_webui_seed_report

        code, lines = collect_webui_seed_report(
            model=args.model,
            num_ctx=args.num_ctx,
            default_model=args.model,
            exec_fn=product_runtime.owned_exec(layout),
        )
        for line in lines:
            _print(line)
        return code

    if command == "aliases":
        from localai.model_aliases import collect_model_aliases_report

        code, lines = collect_model_aliases_report(lenient=True)
        for line in lines:
            _print(line)
        return code

    return EXIT_USAGE
