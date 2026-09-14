"""The installed payload entry point must run THIS installation's own code.

`py -m localai <command>` resolves through the ambient `localai` module name, so
on a machine that also has the private engineering workbench installed editable
it runs the workbench's code against the workbench's repository root. These tests
drive installer/afk-payload.py as a real subprocess and prove it loads the
payload it was pointed at - every one of them fails under ambient-name
resolution, because the marker below only exists in the root under test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ENTRY = Path(__file__).resolve().parents[1] / "installer" / "afk-payload.py"

MARKER = "STUB-PAYLOAD-MARKER"

COMMANDS = ("stop", "start", "health")

# An uninstall must never fail; Start/Health must never claim success for work
# that did not happen.
REFUSAL_EXIT = {"stop": 0, "start": 2, "health": 2}

_STUB_MODULES = {
    "afk_ownership.py": (
        "def collect_afk_stop_report(*, program_root, **kwargs):\n"
        f"    return 0, ['{MARKER} stop']\n"
    ),
    "start.py": (
        "def collect_start_report(**kwargs):\n"
        f"    return 0, ['{MARKER} start']\n"
    ),
    "health.py": (
        "def collect_health_report(**kwargs):\n"
        f"    return 0, ['{MARKER} health']\n"
    ),
}


def _stub_program_root(tmp_path: Path) -> Path:
    """Build a program root whose payload is uniquely identifiable."""
    program_root = tmp_path / "Programs" / "AFK LocalAI"
    package = program_root / "src" / "localai"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name, body in _STUB_MODULES.items():
        (package / name).write_text(body, encoding="utf-8")
    (program_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    return program_root


def _run(command: str, program_root: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(ENTRY),
            command,
            "--program-root",
            str(program_root),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.mark.parametrize("command", COMMANDS)
def test_entry_point_runs_the_payload_it_was_pointed_at(
    command: str, tmp_path: Path
) -> None:
    """The decisive check: ambient resolution cannot produce this marker."""
    program_root = _stub_program_root(tmp_path)

    result = _run(command, program_root)

    assert result.returncode == 0, result.stderr
    assert f"{MARKER} {command}" in result.stdout


@pytest.mark.parametrize("command", COMMANDS)
def test_entry_point_refuses_when_the_payload_is_missing(
    command: str, tmp_path: Path
) -> None:
    empty = tmp_path / "no-payload"
    empty.mkdir()

    result = _run(command, empty)

    assert result.returncode == REFUSAL_EXIT[command], result.stdout
    assert "No AFK LocalAI payload found" in result.stderr
    assert "Refusing to act" in result.stderr
    assert MARKER not in result.stdout


def test_a_refused_uninstall_still_succeeds(tmp_path: Path) -> None:
    """An uninstall must not fail because the payload was already gone."""
    empty = tmp_path / "no-payload"
    empty.mkdir()

    assert _run("stop", empty).returncode == 0


@pytest.mark.parametrize("command", ("start", "health"))
def test_a_refused_start_or_health_reports_failure(
    command: str, tmp_path: Path
) -> None:
    """Reporting success for work that never happened would be a lie."""
    empty = tmp_path / "no-payload"
    empty.mkdir()

    assert _run(command, empty).returncode != 0


def test_unknown_commands_fail_closed(tmp_path: Path) -> None:
    program_root = _stub_program_root(tmp_path)

    result = _run("purge", program_root)

    assert result.returncode != 0
    assert "Usage:" in result.stderr
    assert MARKER not in result.stdout


def test_entry_point_writes_no_bytecode_into_the_installation(
    tmp_path: Path,
) -> None:
    """__pycache__ Setup never installed would survive the uninstall."""
    program_root = _stub_program_root(tmp_path)

    _run("stop", program_root)

    assert not list(program_root.rglob("__pycache__"))
    assert not list(program_root.rglob("*.pyc"))
