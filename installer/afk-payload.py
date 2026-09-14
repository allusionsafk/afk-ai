"""Deterministic entry point for the installed AFK LocalAI payload.

Everything the installed application asks Python to do goes through here, because
`py -m localai <command>` is NOT safe: `localai` is a single global module name on
a Python installation. On a machine that also has the private engineering
workbench installed editable, that command runs the *workbench's* code against the
*workbench's* repository root. For `stop` that was destructive - it tore down the
workbench's compose project and force-closed Docker Desktop and Ollama for the
whole machine. For `start` and `health` it is quieter but just as wrong: the
installed product would operate, and report on, somebody else's stack.

So this script never trusts the ambient name. It loads the `localai` package out
of the payload sitting next to it, then *verifies* that is what it got before
running anything. Importing the payload also fixes the package's own REPO_ROOT,
which is derived from the package location - so every path the payload resolves
afterwards points inside this installation.

Refusal is deliberately asymmetric:

- `stop` refuses with exit 0. It runs from the uninstaller, and an uninstall must
  not fail because a stack was already gone or ownership could not be proven.
- `start` and `health` refuse with a non-zero exit. Reporting success for work
  that never happened would be a lie, and the shell surfaces stderr to the user.

Kept deliberately plain - no imports from the package at module scope, no typing
syntax newer than f-strings - so that an interpreter too old to run the product
still parses this file and reaches the version gate, rather than dying with a
SyntaxError.
"""

import os
import sys

# Importing the payload would otherwise leave __pycache__/*.pyc inside the
# installation directory. Setup never installed those files, so its uninstaller
# never removes them - and the whole program directory survives the uninstall.
# This must be set before the payload is imported.
sys.dont_write_bytecode = True

MINIMUM_PYTHON = (3, 12)
REFUSAL = "Refusing to act on an installation that cannot be verified."
COMMANDS = ("stop", "start", "health")

# An uninstall must never fail; a Start or Health that did nothing must never
# report success.
REFUSAL_EXIT = {"stop": 0, "start": 2, "health": 2}


def _refuse(command, message):
    """Explain why nothing ran, and exit truthfully for this command."""
    sys.stderr.write(f"==== AFK LocalAI {command} ====\n")
    sys.stderr.write(message + "\n")
    sys.stderr.write(REFUSAL + "\n")
    return REFUSAL_EXIT.get(command, 2)


def _parse(argv, default_root):
    """Return (command, program_root) from the command line."""
    command = None
    program_root = default_root
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--program-root" and index + 1 < len(argv):
            program_root = os.path.abspath(argv[index + 1])
            index += 2
            continue
        if argument.startswith("--program-root="):
            program_root = os.path.abspath(argument.split("=", 1)[1])
            index += 1
            continue
        if command is None and not argument.startswith("-"):
            command = argument
        index += 1
    return command, program_root


def _load_payload(program_root):
    """Import this installation's own localai package.

    Returns (module, None) once the import is *proved* to have come from
    program_root, or (None, reason) when it cannot be.
    """
    source_root = os.path.join(program_root, "src")
    package_root = os.path.join(source_root, "localai")
    if not os.path.isdir(package_root):
        return None, f"No AFK LocalAI payload found at {package_root}."

    # Our own payload must win over any ambient install of the same name, and
    # anything already imported under that name must not be reused.
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
        return None, f"Could not load the AFK LocalAI payload: {error}"

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


def _run(command, program_root):
    """Dispatch to the payload's own collector for this command."""
    if command == "stop":
        from localai.afk_ownership import collect_afk_stop_report

        return collect_afk_stop_report(program_root=program_root)
    if command == "start":
        from localai.start import collect_start_report

        return collect_start_report()
    from localai.health import collect_health_report

    return collect_health_report()


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    command, program_root = _parse(argv, os.path.dirname(here))

    if command not in COMMANDS:
        sys.stderr.write(
            f"Usage: afk-payload.py {{{'|'.join(COMMANDS)}}} "
            f"[--program-root <path>]\n"
        )
        return 2

    if sys.version_info < MINIMUM_PYTHON:
        found = f"{sys.version_info[0]}.{sys.version_info[1]}"
        needed = f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}"
        return _refuse(
            command,
            f"AFK LocalAI needs Python {needed} or newer; found {found}.",
        )

    package, problem = _load_payload(program_root)
    if problem is not None:
        return _refuse(command, problem)

    code, lines = _run(command, program_root)
    for line in lines:
        sys.stdout.write(line + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
