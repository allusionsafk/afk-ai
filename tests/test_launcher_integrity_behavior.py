"""The downloadable launcher must not execute anything outside the verified chain.

The website serves hashed launcher bytes, and bootstrap.ps1 verifies the release
it installs against a pinned commit and zip digest. The hop between them was
open: the launcher fetched bootstrap.ps1 from ``master`` and ran it unverified,
so any change to master reached every launcher already downloaded. These tests
pin the launcher to an immutable commit and prove the digest check runs before
PowerShell is allowed to execute the download - including by running the real
verification command against tampered bytes.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "Install Local AI.cmd"


def _text() -> str:
    return LAUNCHER.read_text(encoding="utf-8").replace("\r\n", "\n")


def _set_value(name: str) -> str:
    match = re.search(rf'^set "{re.escape(name)}=([^"]+)"$', _text(), re.MULTILINE)
    assert match, f"missing launcher constant {name}"
    return match.group(1)


def test_remote_bootstrap_comes_from_an_immutable_commit() -> None:
    commit = _set_value("BOOTSTRAP_COMMIT")
    url = _set_value("BOOT_URL")

    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    assert url == (
        "https://raw.githubusercontent.com/allusionsafk/localai-windows-starter/"
        "%BOOTSTRAP_COMMIT%/installer/bootstrap.ps1"
    )
    assert "/master/" not in _text()
    assert "/main/" not in _text()


def test_the_pinned_digest_matches_the_pinned_commit_when_history_exists() -> None:
    """Where the commit is in local history, its bootstrap blob must hash to the pin."""
    commit = _set_value("BOOTSTRAP_COMMIT")
    expected = _set_value("BOOTSTRAP_SHA256")
    assert re.fullmatch(r"[0-9A-F]{64}", expected)
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    shown = subprocess.run(
        [git, "-C", str(ROOT), "show", f"{commit}:installer/bootstrap.ps1"],
        capture_output=True,
        check=False,
    )
    if shown.returncode != 0:
        pytest.skip("pinned commit is not in this (shallow) checkout")
    assert hashlib.sha256(shown.stdout).hexdigest().upper() == expected


def test_downloaded_bootstrap_is_verified_before_execution() -> None:
    text = _text()
    # Search the commands, not the explanatory comments above them.
    code = text.index("echo   Downloading the installer...")

    download = text.index("Invoke-WebRequest", code)
    verify = text.index("Get-FileHash", code)
    run_label = text.index("\n:run\n")
    execute = text.index('powershell -NoProfile -ExecutionPolicy Bypass -File "%BOOT%"')

    assert download < verify < run_label < execute
    assert "if errorlevel 1 goto :failed" in text[verify:run_label]
    assert 'if not exist "%BOOT%" goto :failed' in text[verify:run_label]
    assert "Refusing to run the downloaded bootstrap." in text


def test_the_launcher_isolates_windows_powershell_from_powershell_7() -> None:
    """Reproduced: pwsh -> cmd -> powershell.exe cannot resolve Get-FileHash.

    cmd inherits PowerShell 7's PSModulePath, Windows PowerShell then fails to
    load its own Microsoft.PowerShell.Utility, and the verification (and the
    bootstrap's downloads) could never run for anyone starting from a pwsh
    terminal.
    """
    text = _text()

    assert text.index("setlocal") < text.index('set "PSModulePath="')
    assert text.index('set "PSModulePath="') < text.index("powershell -NoProfile")


def test_remote_bootstrap_temp_path_is_commit_scoped() -> None:
    values = re.findall(r'^set "BOOT=([^"]+)"$', _text(), re.MULTILINE)
    remote = [value for value in values if value.startswith("%TEMP%")]

    assert remote == ["%TEMP%\\localai-bootstrap-%BOOTSTRAP_COMMIT%.ps1"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell only")
def test_tampered_bootstrap_bytes_are_deleted_and_refused(tmp_path: Path) -> None:
    """Run the launcher's own verification command against tampered bytes."""
    command = re.search(
        r'^powershell -NoProfile -ExecutionPolicy Bypass -Command "(.+)"$',
        _text(),
        re.MULTILINE,
    )
    assert command
    script = command.group(1)
    # Replace only the download with a local tampered file; everything after it
    # - the digest comparison, deletion and refusal - is the launcher's own.
    script = script.replace(
        "Invoke-WebRequest -UseBasicParsing $env:BOOT_URL -OutFile $out;",
        "Set-Content -LiteralPath $out -Value 'Write-Host tampered' -Encoding ascii;",
    )
    target = tmp_path / "bootstrap.ps1"
    env = dict(
        os.environ,
        BOOT=str(target),
        BOOT_URL="unused",
        BOOTSTRAP_SHA256=_set_value("BOOTSTRAP_SHA256"),
    )
    # The launcher clears this before any PowerShell runs (see the test above).
    # os.environ keys are upper-cased on Windows, so match case-insensitively.
    for key in [k for k in env if k.upper() == "PSMODULEPATH"]:
        del env[key]

    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )

    assert result.returncode != 0
    assert "Refusing to run the downloaded bootstrap" in result.stderr + result.stdout
    assert not target.exists()
