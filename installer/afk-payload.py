"""Deterministic entry point for the installed AFK AI engine.

Everything the installed application asks Python to do goes through here, and
this script refuses to run anything it cannot prove belongs to THIS installation.
Two different ambient-resolution failures shipped before it existed:

1. ``py -m localai <command>``: ``localai`` is a single global module name. On a
   machine that also had the private engineering workbench installed editable,
   ``stop`` tore down the workbench's compose project and force-closed Docker
   Desktop and Ollama machine-wide.
2. ``py.exe -B afk-payload.py``: the right package, but whatever interpreter the
   Python launcher picked (the newest one on the PC), with that interpreter's
   site-packages, ``.pth`` files and ``PYTHON*`` environment in effect. Setup
   also ``pip install -e``'d the product into the user's global Python.

So before importing anything this script proves:

- the interpreter is AFK AI's own pinned CPython under
  ``<program root>\\runtime\\python`` (not ``py``, not PATH, not a venv);
- that interpreter is isolated - no ``site``, no user site-packages, ``PYTHON*``
  environment variables ignored (the embeddable distribution's ``._pth`` mode);
- its version is the one Setup staged (``afk-runtime.json``), so a half-applied
  update cannot run new code on an old interpreter or vice versa;
- the ``localai`` package that answered the import is this installation's own.

Refusal is deliberately asymmetric:

- ``stop`` refuses with exit 0. It runs from the uninstaller, and an uninstall
  must not fail because ownership could not be proven.
- every other command refuses with exit 3. ``status --json`` additionally prints
  a FAILED status line, so the shell shows "needs repair" instead of guessing.

Kept deliberately plain - no imports from the package at module scope - so that
an unexpected interpreter still parses this file and reaches the refusal.
"""

import json
import os
import platform
import sys

# Importing the payload would otherwise leave __pycache__/*.pyc inside the
# installation directory. Setup never installed those files, so its uninstaller
# never removes them - and the whole program directory survives the uninstall.
# This must be set before the payload is imported.
sys.dont_write_bytecode = True

REFUSAL = "Refusing to act on an installation that cannot be verified."
COMMANDS = (
    "status",
    "start",
    "stop",
    "runtime-info",
    "diagnostics",
    "configure",
    "pull-model",
    "seed-webui",
    "aliases",
)
REFUSAL_EXIT = 3
STOP_REFUSAL_EXIT = 0
RUNTIME_DIR = ("runtime", "python")
RUNTIME_IDENTITY = "afk-runtime.json"


def _refusal_status(message):
    return json.dumps(
        {
            "schema_version": 2,
            "mode": "qualify",
            "state": "Failed",
            "reason": "RUNTIME_UNVERIFIED",
            "message": "AFK AI's own runtime could not be verified. Reinstall "
            "AFK AI to repair it; your chats and settings are kept.",
            "next_action": "repair",
            "chat": {"ready": False, "url": None, "onboarding_required": None},
            "qualification": {"inference": "not_run", "detail": message[:200]},
            "model": None,
            "services": [],
        },
        sort_keys=True,
    )


def _refuse(command, message, wants_json):
    """Explain why nothing ran, and exit truthfully for this command."""
    sys.stderr.write(f"==== AFK AI {command} ====\n")
    sys.stderr.write(message + "\n")
    sys.stderr.write(REFUSAL + "\n")
    if command == "status" and wants_json:
        sys.stdout.write(_refusal_status(message) + "\n")
    return STOP_REFUSAL_EXIT if command == "stop" else REFUSAL_EXIT


def _option(argv, name):
    for index, argument in enumerate(argv):
        if argument == name and index + 1 < len(argv):
            return argv[index + 1]
        if argument.startswith(name + "="):
            return argument.split("=", 1)[1]
    return None


def _parse(argv, default_root):
    """Return (command, program_root, wants_json) without importing anything."""
    command = next((a for a in argv if a in COMMANDS), None)
    root = _option(argv, "--program-root") or default_root
    return command, os.path.abspath(root), "--json" in argv


def _same(left, right):
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
        os.path.realpath(right)
    )


def verify_interpreter(program_root, executable=None, flags=None, version=None):
    """Return None when this is AFK AI's own isolated runtime, else the reason."""
    executable = executable or sys.executable
    flags = flags or sys.flags
    version = version or platform.python_version()
    expected = os.path.join(program_root, *RUNTIME_DIR)
    if not executable or not _same(
        os.path.dirname(os.path.abspath(executable)), expected
    ):
        return (
            "This is not AFK AI's own Python runtime. AFK AI runs only on the "
            f"interpreter it installed under {expected}."
        )
    if not flags.isolated or not flags.no_site:
        return (
            "AFK AI's Python runtime is not isolated from this PC's Python "
            "settings (site-packages or PYTHON* variables would apply)."
        )
    try:
        # Beside the interpreter directory, not inside it: runtime/python stays
        # byte-for-byte the verified CPython distribution.
        identity_path = os.path.join(program_root, "runtime", RUNTIME_IDENTITY)
        with open(identity_path, encoding="utf-8") as stream:
            identity = json.load(stream)
    except (OSError, ValueError):
        return "AFK AI's Python runtime identity file is missing or unreadable."
    pinned = identity.get("version") if isinstance(identity, dict) else None
    if pinned != version:
        return (
            f"AFK AI's Python runtime is {version} but this installation expects "
            f"{pinned}. The installation is incomplete."
        )
    return None


def load_payload(program_root):
    """Import this installation's own localai package.

    Returns (module, None) once the import is *proved* to have come from
    program_root, or (None, reason) when it cannot be.
    """
    source_root = os.path.join(program_root, "src")
    package_root = os.path.join(source_root, "localai")
    if not os.path.isdir(package_root):
        return None, f"No AFK AI payload found at {package_root}."

    # Our own payload must win, and anything already imported under that name
    # must not be reused.
    sys.path.insert(0, source_root)
    for name in [
        module
        for module in list(sys.modules)
        if module == "localai" or module.startswith("localai.")
    ]:
        del sys.modules[name]

    try:
        import localai
    except ImportError as error:
        return None, f"Could not load the AFK AI payload: {error}"

    # Proof, not assumption: a .pth entry or a meta-path finder from another
    # installation could still have answered the import.
    resolved = os.path.abspath(getattr(localai, "__file__", "") or "")
    expected = os.path.join(package_root, "")
    if not resolved.lower().startswith(expected.lower()):
        answered = resolved or "<unknown>"
        return None, (
            f"Resolved 'localai' from {answered}, which is not this "
            f"installation ({package_root})."
        )
    return localai, None


def _utf8_streams():
    # Piped output on Windows defaults to the ANSI code page; a refusal message
    # naming a non-ASCII install path must not crash with UnicodeEncodeError
    # instead of refusing. The native shell reads UTF-8.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv):
    _utf8_streams()
    here = os.path.dirname(os.path.abspath(__file__))
    command, program_root, wants_json = _parse(argv, os.path.dirname(here))

    if command is None:
        sys.stderr.write(
            f"Usage: afk-payload.py --program-root <path> [--data-root <path>] "
            f"{{{'|'.join(COMMANDS)}}} [options]\n"
        )
        return 2

    problem = verify_interpreter(program_root)
    if problem is not None:
        return _refuse(command, problem, wants_json)

    _package, problem = load_payload(program_root)
    if problem is not None:
        return _refuse(command, problem, wants_json)

    from localai.product_cli import main as product_main

    return product_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
