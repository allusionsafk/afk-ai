"""The installed engine entry point must run THIS installation's code on AFK's runtime.

Two ambient-resolution failures shipped before these tests existed:

- ``py -m localai <command>`` resolved the global ``localai`` name, so on a machine
  with the private engineering workbench installed editable, Stop tore down the
  workbench's stack and force-closed Docker Desktop and Ollama machine-wide.
- ``py.exe -B afk-payload.py`` loaded the right package on whatever interpreter the
  Python launcher chose, with that interpreter's site-packages and PYTHON*
  environment in effect.

These tests drive installer/afk-payload.py as a real subprocess. The fixture's
``runtime/python`` is a directory link to the test interpreter's own directory, run
with ``-I -S`` - the same isolation the embeddable CPython's ``._pth`` mode gives
the shipped runtime - so no bypass switch exists or is needed.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

ENTRY = Path(__file__).resolve().parents[1] / "installer" / "afk-payload.py"
MARKER = "STUB-PAYLOAD-MARKER"
INTERPRETER_DIR = Path(sys.executable).resolve().parent
COMMANDS = ("status", "start", "stop", "diagnostics")

_STUB_CLI = (
    "COMMANDS = " + repr(COMMANDS) + "\n"
    "def main(argv):\n"
    "    command = next(a for a in argv if a in COMMANDS)\n"
    f"    print('{MARKER} ' + command)\n"
    "    return 0\n"
)


def _link_directory(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _program_root(
    tmp_path: Path,
    *,
    runtime_target: Path | None = INTERPRETER_DIR,
    pinned_version: str | None = None,
    payload: bool = True,
) -> Path:
    program_root = tmp_path / "Programs" / "AFK AI"
    program_root.mkdir(parents=True)
    if payload:
        package = program_root / "src" / "localai"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "product_cli.py").write_text(_STUB_CLI, encoding="utf-8")
    if runtime_target is not None:
        _link_directory(program_root / "runtime" / "python", runtime_target)
    if pinned_version != "":
        identity = {
            "implementation": "CPython",
            "version": pinned_version or platform.python_version(),
            "sha256": "0" * 64,
            "source_url": "https://www.python.org/ftp/python/",
        }
        (program_root / "runtime").mkdir(parents=True, exist_ok=True)
        (program_root / "runtime" / "afk-runtime.json").write_text(
            json.dumps(identity), encoding="utf-8"
        )
    return program_root


def _run(
    program_root: Path,
    *args: str,
    isolated: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    flags = ["-I", "-S", "-B"] if isolated else ["-B"]
    return subprocess.run(
        [
            sys.executable,
            *flags,
            str(ENTRY),
            "--program-root",
            str(program_root),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )


@pytest.mark.parametrize("command", COMMANDS)
def test_entry_point_runs_the_payload_it_was_pointed_at(
    command: str, tmp_path: Path
) -> None:
    """The decisive check: ambient resolution cannot produce this marker."""
    program_root = _program_root(tmp_path)

    result = _run(program_root, command)

    assert result.returncode == 0, result.stderr
    assert f"{MARKER} {command}" in result.stdout


def test_an_interpreter_outside_the_installation_is_refused(tmp_path: Path) -> None:
    """``py.exe``, PATH Python or a venv: all are "not AFK's runtime"."""
    elsewhere = tmp_path / "some-other-python"
    elsewhere.mkdir()
    program_root = _program_root(tmp_path, runtime_target=elsewhere)

    result = _run(program_root, "status", "--json")

    assert result.returncode == 3
    assert MARKER not in result.stdout
    assert "not AFK AI's own Python runtime" in result.stderr
    status = json.loads(result.stdout.strip().splitlines()[-1])
    assert status["state"] == "Failed"
    assert status["reason"] == "RUNTIME_UNVERIFIED"
    assert status["chat"] == {"ready": False, "url": None, "onboarding_required": None}
    assert status["next_action"] == "repair"


def test_the_runtime_must_be_isolated_from_the_pc_s_python_settings(
    tmp_path: Path,
) -> None:
    program_root = _program_root(tmp_path)

    result = _run(program_root, "status", isolated=False)

    assert result.returncode == 3
    assert MARKER not in result.stdout
    assert "not isolated" in result.stderr


def test_a_runtime_version_mismatch_is_an_incomplete_installation(
    tmp_path: Path,
) -> None:
    """A half-applied update must not run new code on an old interpreter."""
    program_root = _program_root(tmp_path, pinned_version="3.0.0")

    result = _run(program_root, "start")

    assert result.returncode == 3
    assert MARKER not in result.stdout
    assert "incomplete" in result.stderr


def test_a_missing_runtime_identity_is_refused(tmp_path: Path) -> None:
    program_root = _program_root(tmp_path, pinned_version="")

    result = _run(program_root, "status")

    assert result.returncode == 3
    assert "identity" in result.stderr


def test_entry_point_refuses_when_the_payload_is_missing(tmp_path: Path) -> None:
    program_root = _program_root(tmp_path, payload=False)

    result = _run(program_root, "start")

    assert result.returncode == 3
    assert "No AFK AI payload found" in result.stderr
    assert "Refusing to act" in result.stderr


def test_a_refused_uninstall_still_succeeds(tmp_path: Path) -> None:
    """An uninstall must not fail because the runtime or payload was already gone."""
    elsewhere = tmp_path / "gone"
    elsewhere.mkdir()
    program_root = _program_root(tmp_path, runtime_target=elsewhere, payload=False)

    result = _run(program_root, "stop")

    assert result.returncode == 0
    assert "Refusing to act" in result.stderr


def test_hostile_ambient_python_state_cannot_answer_the_import(tmp_path: Path) -> None:
    """A ``localai`` on PYTHONPATH (e.g. an engineering checkout) is never loaded."""
    program_root = _program_root(tmp_path)
    hostile = tmp_path / "hostile"
    (hostile / "localai").mkdir(parents=True)
    (hostile / "localai" / "__init__.py").write_text(
        "raise SystemExit('HOSTILE localai imported')\n", encoding="utf-8"
    )
    (hostile / "localai" / "product_cli.py").write_text(
        "def main(argv):\n    print('HOSTILE')\n    return 0\n", encoding="utf-8"
    )
    env = dict(os.environ, PYTHONPATH=str(hostile), PYTHONSTARTUP=str(hostile))

    result = _run(program_root, "status", env=env)

    assert result.returncode == 0, result.stderr
    assert f"{MARKER} status" in result.stdout
    assert "HOSTILE" not in result.stdout + result.stderr


def test_unknown_commands_fail_closed(tmp_path: Path) -> None:
    program_root = _program_root(tmp_path)

    result = _run(program_root, "purge")

    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert MARKER not in result.stdout


def test_entry_point_writes_no_bytecode_into_the_installation(
    tmp_path: Path,
) -> None:
    """__pycache__ Setup never installed would survive the uninstall."""
    program_root = _program_root(tmp_path)

    _run(program_root, "stop")

    assert not list((program_root / "src").rglob("__pycache__"))
    assert not list((program_root / "src").rglob("*.pyc"))
