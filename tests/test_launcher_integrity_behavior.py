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
_DOWNLOAD = "Invoke-WebRequest -UseBasicParsing $env:BOOT_URL -OutFile $out;"


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
        "https://raw.githubusercontent.com/allusionsafk/afk-ai/"
        "%BOOTSTRAP_COMMIT%/installer/bootstrap.ps1"
    )
    assert "/master/" not in _text()
    assert "/main/" not in _text()


def _history_unavailable(reason: str) -> None:
    # CI checks out full history precisely so this runs there. A skip in CI
    # silently stopped proving the pin on a depth-1 checkout, so CI fails instead.
    if os.environ.get("CI"):
        pytest.fail(f"{reason}; CI must check out full history for this test")
    pytest.skip(reason)


def test_the_pinned_digest_matches_the_pinned_commit() -> None:
    """The pinned commit's bootstrap blob must hash to the pinned digest."""
    commit = _set_value("BOOTSTRAP_COMMIT")
    expected = _set_value("BOOTSTRAP_SHA256")
    assert re.fullmatch(r"[0-9A-F]{64}", expected)
    git = shutil.which("git")
    if git is None:
        _history_unavailable("git is not available")
        return
    shown = subprocess.run(
        [git, "-C", str(ROOT), "show", f"{commit}:installer/bootstrap.ps1"],
        capture_output=True,
        check=False,
    )
    if shown.returncode != 0:
        _history_unavailable("pinned commit is not in this checkout's history")
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
    assert script.count(_DOWNLOAD) == 1
    script = script.replace(
        _DOWNLOAD,
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


# ------------------------------------------------ the real launcher, end to end
#
# The checks above read the launcher's text, and the tampered-bytes test runs
# only its extracted -Command string. Adversarial review showed that is not
# enough: a launcher that ran the download BEFORE hashing it passed every one of
# them. These run the actual .cmd - control flow, errorlevels, the final run
# step - with exactly two asserted substitutions: the network fetch reads a
# local fixture, and the pin is that fixture's digest. The fixture bootstrap
# records its own path and SHA-256 if, and only if, something executes it.

_FIXTURE = (
    "$h = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash\r\n"
    "Set-Content -LiteralPath $env:AFK_TEST_EXECUTED"
    " -Value ($PSCommandPath + '|' + $h) -Encoding ascii\r\n"
    "exit 0\r\n"
)


def _launcher_run(
    tmp_path: Path, *, pinned: bytes, fetched: bytes, planted: bytes | None = None
) -> tuple[str, Path, Path]:
    """Run the real launcher; return (output, bootstrap path, execution record)."""
    text = LAUNCHER.read_bytes().decode("utf-8")
    pin_line = f'set "BOOTSTRAP_SHA256={_set_value("BOOTSTRAP_SHA256")}"'
    # Each substitution must hit exactly once: a launcher that no longer
    # fetches, or no longer pins, fails here rather than passing vacuously.
    assert text.count(_DOWNLOAD) == 1
    assert text.count(pin_line) == 1
    digest = hashlib.sha256(pinned).hexdigest().upper()
    text = text.replace(
        _DOWNLOAD,
        "Copy-Item -LiteralPath $env:AFK_TEST_FETCHED -Destination $out;",
    ).replace(pin_line, f'set "BOOTSTRAP_SHA256={digest}"')

    home = tmp_path / "launcher"
    temp = tmp_path / "temp"
    home.mkdir()
    temp.mkdir()
    launcher = home / "install.cmd"
    launcher.write_bytes(text.encode("utf-8"))
    source = tmp_path / "fetched.ps1"
    source.write_bytes(fetched)
    executed = tmp_path / "executed.txt"
    boot = temp / f"localai-bootstrap-{_set_value('BOOTSTRAP_COMMIT')}.ps1"
    if planted is not None:
        boot.write_bytes(planted)

    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"TEMP", "TMP", "PSMODULEPATH"}
    }
    env.update(
        TEMP=str(temp),
        TMP=str(temp),
        AFK_TEST_FETCHED=str(source),
        AFK_TEST_EXECUTED=str(executed),
    )
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(launcher)],
        input="\r\n",  # the launcher ends with "pause"
        capture_output=True,
        text=True,
        env=env,
        cwd=home,
        timeout=180,
        check=False,
    )
    return result.stdout + result.stderr, boot, executed


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher")
def test_verified_bootstrap_bytes_are_the_bytes_that_run(tmp_path: Path) -> None:
    good = _FIXTURE.encode("ascii")

    output, boot, executed = _launcher_run(tmp_path, pinned=good, fetched=good)

    assert "Finished." in output, output
    assert executed.is_file(), output
    ran_path, ran_digest = executed.read_text(encoding="ascii").strip().split("|")
    assert Path(ran_path) == boot
    assert ran_digest == hashlib.sha256(good).hexdigest().upper()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher")
def test_tampered_bootstrap_is_refused_deleted_and_never_run(tmp_path: Path) -> None:
    good = _FIXTURE.encode("ascii")
    tampered = good + b"# one changed byte is enough\r\n"

    output, boot, executed = _launcher_run(tmp_path, pinned=good, fetched=tampered)

    assert not executed.exists(), "tampered bootstrap was executed"
    assert not boot.exists()
    assert "Refusing to run the downloaded bootstrap" in output
    assert "Something went wrong" in output
    assert "Finished." not in output


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher")
def test_the_checked_file_is_the_downloaded_file(tmp_path: Path) -> None:
    """A valid file already at the bootstrap path must not vouch for a new fetch."""
    good = _FIXTURE.encode("ascii")
    tampered = good + b"# swapped after the check\r\n"

    output, boot, executed = _launcher_run(
        tmp_path, pinned=good, fetched=tampered, planted=good
    )

    assert not executed.exists(), "a file other than the verified one was executed"
    assert not boot.exists()
    assert "Refusing to run the downloaded bootstrap" in output
