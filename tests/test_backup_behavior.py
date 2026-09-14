from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

import pytest

from localai import backup, compose
from localai.ops import CommandResult
from localai.paths import REPO_ROOT


def _ok(args: Sequence[str], stdout: str = "") -> CommandResult:
    return CommandResult(tuple(str(a) for a in args), 0, stdout, "")


def test_docker_backup_args_use_pinned_busybox_and_default_volume() -> None:
    dest = Path("C:/repo/backups")
    assert backup.docker_backup_args(dest, "open-webui-2026-06-21_211000.tar.gz") == [
        "docker",
        "run",
        "--rm",
        "-v",
        "afk-localai_open-webui:/data",
        "-v",
        f"{dest}:/backup",
        backup.BUSYBOX_IMAGE,
        "tar",
        "czf",
        "/backup/open-webui-2026-06-21_211000.tar.gz",
        "-C",
        "/data",
        ".",
    ]
    custom = backup.docker_backup_args(dest, "a.tar.gz", volume="vol-x")
    assert custom[4] == "vol-x:/data"


def test_docker_verify_args_run_tar_tzf_readonly() -> None:
    dest = Path("C:/repo/backups")
    assert backup.docker_verify_args(dest, "a.tar.gz") == [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{dest}:/backup:ro",
        backup.BUSYBOX_IMAGE,
        "tar",
        "tzf",
        "/backup/a.tar.gz",
    ]


def test_resolve_volume_prefers_compose_then_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compose, "service_volume_name", lambda *a, **k: "live-vol")
    assert backup.resolve_volume() == "live-vol"
    monkeypatch.setattr(compose, "service_volume_name", lambda *a, **k: None)
    # The fallback must name THIS project's volume. "localai_open-webui" belongs
    # to the compose project "localai", which a private engineering workbench on a
    # developer machine also claims - backing up, or restoring over, its data.
    assert backup.resolve_volume() == "afk-localai_open-webui"


def test_backup_docker_failure_matches_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dest = Path("C:/repo/backups")
    docker_text = "failed to connect to the docker API"

    def fake_run_command(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        assert list(args) == backup.docker_backup_args(
            dest, "open-webui-2026-06-21_211000.tar.gz"
        )
        assert cwd == REPO_ROOT
        assert env is not None and "PATH" in env
        assert timeout_sec == 30
        return CommandResult(tuple(str(a) for a in args), 1, docker_text, "")

    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(compose, "service_volume_name", lambda *a, **k: None)
    monkeypatch.setattr(backup, "run_command", fake_run_command)

    code, lines = backup.collect_backup_report(
        timeout_sec=30, now=datetime(2026, 6, 21, 21, 10, 0), backup_dir=dest
    )
    assert code == 1
    assert lines == [
        "[*] Backing up Open WebUI data volume...",
        "[!] Backup failed: docker exited with code 1.",
        docker_text,
    ]


def test_backup_success_verifies_checksums_and_prunes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dest = Path("C:/repo/backups")
    new_archive = dest / "open-webui-2026-06-21_211000.tar.gz"
    old_archives = [
        dest / f"open-webui-2026-06-{d:02d}_120000.tar.gz" for d in range(10, 18)
    ]
    all_archives = [new_archive, *old_archives]
    calls: list[list[str]] = []
    unlinked: list[Path] = []

    def fake_run_command(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        calls.append([str(a) for a in args])
        return _ok(args)

    def fake_stat(path: Path) -> os.stat_result:
        if path == new_archive:
            size, mtime = 2 * 1024 * 1024, 100.0
        else:
            size = 1024
            mtime = float(int(path.name.split("_", 1)[0].split("-")[-1]))
        return os.stat_result((0, 0, 0, 0, 0, 0, size, 0, mtime, 0))

    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(compose, "service_volume_name", lambda *a, **k: None)
    monkeypatch.setattr(backup, "run_command", fake_run_command)
    monkeypatch.setattr(backup, "write_checksum", lambda archive: "a" * 64)
    monkeypatch.setattr(Path, "exists", lambda path: path == new_archive)
    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(Path, "glob", lambda path, pattern: all_archives)
    monkeypatch.setattr(
        Path, "unlink", lambda path, missing_ok=False: unlinked.append(path)
    )

    code, lines = backup.collect_backup_report(
        now=datetime(2026, 6, 21, 21, 10, 0), backup_dir=dest
    )
    assert code == 0
    assert lines == [
        "[*] Backing up Open WebUI data volume...",
        f"{new_archive}  (2.0 MB)",
        "    integrity OK (tar tzf); sha256 aaaaaaaaaaaaaaaa...",
        "    trial restore + PRAGMA integrity_check: ok",
    ]
    # backup czf, verify tzf, then trial-restore + DB check
    assert calls[0][9] == "czf"
    assert calls[1][7] == "tzf"
    assert "integrity_check" in " ".join(calls[2])
    pruned_archives = [p for p in unlinked if p.name.endswith(".tar.gz")]
    assert pruned_archives == [
        dest / "open-webui-2026-06-11_120000.tar.gz",
        dest / "open-webui-2026-06-10_120000.tar.gz",
    ]


def test_backup_integrity_failure_removes_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dest = Path("C:/repo/backups")
    new_archive = dest / "open-webui-2026-06-21_211000.tar.gz"
    unlinked: list[Path] = []
    checksum_called = False
    glob_called = False

    def fake_run_command(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        if "tzf" in args:
            return CommandResult(tuple(str(a) for a in args), 2, "", "corrupt")
        return _ok(args)

    def fake_checksum(archive: Path) -> str:
        nonlocal checksum_called
        checksum_called = True
        return "x"

    def fake_glob(path: Path, pattern: str) -> list[Path]:
        nonlocal glob_called
        glob_called = True
        return []

    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(compose, "service_volume_name", lambda *a, **k: None)
    monkeypatch.setattr(backup, "run_command", fake_run_command)
    monkeypatch.setattr(backup, "write_checksum", fake_checksum)
    monkeypatch.setattr(Path, "exists", lambda path: path == new_archive)
    monkeypatch.setattr(
        Path, "stat", lambda path: os.stat_result((0, 0, 0, 0, 0, 0, 4096, 0, 1.0, 0))
    )
    monkeypatch.setattr(Path, "glob", fake_glob)
    monkeypatch.setattr(
        Path, "unlink", lambda path, missing_ok=False: unlinked.append(path)
    )

    code, lines = backup.collect_backup_report(
        now=datetime(2026, 6, 21, 21, 10, 0), backup_dir=dest
    )
    assert code == 1
    assert new_archive in unlinked
    assert "[!] Backup failed tar integrity check; removed the bad archive." in lines
    assert not checksum_called
    assert not glob_called


def test_docker_trial_restore_args_extract_then_pragma() -> None:
    dest = Path("C:/repo/backups")
    args = backup.docker_trial_restore_args(dest, "a.tar.gz")
    assert args[:5] == ["docker", "run", "--rm", "-v", f"{dest}:/backup:ro"]
    assert args[5] == backup.OPEN_WEBUI_IMAGE
    script = args[-1]
    assert "tar xzf /backup/a.tar.gz" in script
    assert "./webui.db" in script
    assert "integrity_check" in script


def test_backup_trial_restore_failure_quarantines_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dest = Path("C:/repo/backups")
    new_archive = dest / "open-webui-2026-06-21_211000.tar.gz"
    renamed: list[tuple[Path, Path]] = []
    checksum_called = False
    glob_called = False

    def fake_run_command(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        if "integrity_check" in " ".join(str(a) for a in args):
            return CommandResult(
                tuple(str(a) for a in args), 1, "database disk image is malformed", ""
            )
        return _ok(args)

    def fake_checksum(archive: Path) -> str:
        nonlocal checksum_called
        checksum_called = True
        return "x"

    def fake_glob(path: Path, pattern: str) -> list[Path]:
        nonlocal glob_called
        glob_called = True
        return []

    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(compose, "service_volume_name", lambda *a, **k: None)
    monkeypatch.setattr(backup, "run_command", fake_run_command)
    monkeypatch.setattr(backup, "write_checksum", fake_checksum)
    monkeypatch.setattr(Path, "exists", lambda path: path == new_archive)
    monkeypatch.setattr(
        Path, "stat", lambda path: os.stat_result((0, 0, 0, 0, 0, 0, 4096, 0, 1.0, 0))
    )
    monkeypatch.setattr(Path, "glob", fake_glob)
    monkeypatch.setattr(
        Path, "replace", lambda path, target: renamed.append((path, Path(target)))
    )

    code, lines = backup.collect_backup_report(
        now=datetime(2026, 6, 21, 21, 10, 0), backup_dir=dest
    )
    assert code == 1
    assert renamed == [(new_archive, dest / f"{new_archive.name}.bad")]
    assert any("quarantined" in line for line in lines)
    assert not checksum_called
    assert not glob_called


def test_quarantine_archive_renames_to_bad(tmp_path: Path) -> None:
    archive = tmp_path / "open-webui-x.tar.gz"
    archive.write_bytes(b"x")
    bad = backup.quarantine_archive(archive)
    assert not archive.exists()
    assert bad == tmp_path / "open-webui-x.tar.gz.bad"
    assert bad.exists()


def test_write_checksum_writes_sidecar(tmp_path: Path) -> None:
    archive = tmp_path / "open-webui-x.tar.gz"
    archive.write_bytes(b"hello backup")
    digest = backup.write_checksum(archive)
    sidecar = tmp_path / "open-webui-x.tar.gz.sha256"
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8") == f"{digest}  {archive.name}\n"
    assert len(digest) == 64


def test_old_backups_to_prune_keeps_newest_seven(tmp_path: Path) -> None:
    paths = []
    for day in range(1, 11):
        p = tmp_path / f"open-webui-2026-06-{day:02d}_120000.tar.gz"
        p.write_bytes(b"x")
        os.utime(p, (float(day), float(day)))
        paths.append(p)
    pruned = backup.old_backups_to_prune(paths)
    assert sorted(p.name for p in pruned) == [
        "open-webui-2026-06-01_120000.tar.gz",
        "open-webui-2026-06-02_120000.tar.gz",
        "open-webui-2026-06-03_120000.tar.gz",
    ]


def test_tiered_retention_keeps_weekly_and_monthly_representatives(
    tmp_path: Path,
) -> None:
    # 7 dailies (Jun 15-21) fill the recent tier. Jun 14 survives as the
    # newest of ISO week 24, May 20 / Mar 5 as their weeks' newest; Jun 8 is
    # W24 but older than Jun 14 and not a month representative -> pruned.
    days = [
        "2026-06-21", "2026-06-20", "2026-06-19", "2026-06-18",
        "2026-06-17", "2026-06-16", "2026-06-15",
        "2026-06-14", "2026-06-08", "2026-05-20", "2026-03-05",
    ]
    paths = []
    for day in days:
        p = tmp_path / f"open-webui-{day}_120000.tar.gz"
        p.write_bytes(b"x")
        paths.append(p)
    pruned = backup.old_backups_to_prune(paths)
    assert [p.name for p in pruned] == ["open-webui-2026-06-08_120000.tar.gz"]


def test_prune_never_touches_quarantined_bad_archives(tmp_path: Path) -> None:
    # flr.34 acceptance: a run of corrupt backups (quarantined as .bad) must
    # not evict previously-validated-good archives.
    good_days = [f"2026-06-{d:02d}" for d in range(13, 22)]  # Jun 13-21
    for day in good_days:
        (tmp_path / f"open-webui-{day}_120000.tar.gz").write_bytes(b"g")
    for day in ("2026-06-22", "2026-06-23", "2026-06-24"):  # newer, corrupt
        (tmp_path / f"open-webui-{day}_120000.tar.gz.bad").write_bytes(b"c")

    backup.prune_old_backups(tmp_path)

    names = sorted(p.name for p in tmp_path.iterdir())
    # Jun 13 falls out (not newest-7, not a week/month representative);
    # every other good backup and all .bad forensics files remain.
    assert "open-webui-2026-06-13_120000.tar.gz" not in names
    for day in good_days[1:]:
        assert f"open-webui-{day}_120000.tar.gz" in names
    assert sum(1 for n in names if n.endswith(".bad")) == 3


def test_archive_stamp_parses_filename_without_stat(tmp_path: Path) -> None:
    p = tmp_path / "open-webui-2026-06-21_211000.tar.gz"
    p.write_bytes(b"x")
    assert backup.archive_stamp(p) == datetime(2026, 6, 21, 21, 10, 0)


_ARCHIVE = Path("C:/b/open-webui-x.tar.gz")
_SIDECAR = Path("C:/b/open-webui-x.tar.gz.sha256")


def _fake_run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_sec: float | None = None,
) -> CommandResult:
    joined = " ".join(str(a) for a in args)
    if "integrity_check" in joined:
        return CommandResult(tuple(str(a) for a in args), 0, "ok\n", "")
    return CommandResult(tuple(str(a) for a in args), 0, "", "")


def test_restore_missing_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda path: False)
    code, lines = backup.collect_restore_report(_ARCHIVE, confirm=True)
    assert code == 1
    assert any("Archive not found" in line for line in lines)


def test_restore_requires_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda path: path == _ARCHIVE)
    code, lines = backup.collect_restore_report(_ARCHIVE, confirm=False)
    assert code == 2
    assert any("--confirm" in line for line in lines)


def test_restore_checksum_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda path: path in (_ARCHIVE, _SIDECAR))
    monkeypatch.setattr(
        Path, "read_text", lambda path, encoding="utf-8": "deadbeef  x\n"
    )
    monkeypatch.setattr(backup, "sha256_of", lambda archive: "different")
    code, lines = backup.collect_restore_report(_ARCHIVE, confirm=True)
    assert code == 1
    assert any("Checksum mismatch" in line for line in lines)


def test_restore_refuses_while_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda path: path == _ARCHIVE)
    monkeypatch.setattr(
        compose,
        "compose_service_status",
        lambda *a, **k: compose.ServiceStatus("c", "running", "Up", "healthy"),
    )
    code, lines = backup.collect_restore_report(_ARCHIVE, confirm=True)
    assert code == 2
    assert any("running" in line for line in lines)


def test_restore_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def record(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        calls.append([str(a) for a in args])
        return _fake_run(args, cwd=cwd, env=env, timeout_sec=timeout_sec)

    monkeypatch.setattr(Path, "exists", lambda path: path == _ARCHIVE)
    monkeypatch.setattr(compose, "compose_service_status", lambda *a, **k: None)
    monkeypatch.setattr(compose, "service_volume_name", lambda *a, **k: "vol-x")
    monkeypatch.setattr(backup, "run_command", record)
    code, lines = backup.collect_restore_report(_ARCHIVE, confirm=True, force=True)
    assert code == 0
    assert any("integrity_check: ok" in line for line in lines)
    assert any("Restore complete" in line for line in lines)
    assert "vol-x:/data" in calls[0]


def test_restore_integrity_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad_integrity(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        if "integrity_check" in " ".join(str(a) for a in args):
            return CommandResult(tuple(str(a) for a in args), 1, "malformed\n", "")
        return CommandResult(tuple(str(a) for a in args), 0, "", "")

    monkeypatch.setattr(Path, "exists", lambda path: path == _ARCHIVE)
    monkeypatch.setattr(compose, "compose_service_status", lambda *a, **k: None)
    monkeypatch.setattr(compose, "service_volume_name", lambda *a, **k: "vol-x")
    monkeypatch.setattr(backup, "run_command", bad_integrity)
    code, lines = backup.collect_restore_report(_ARCHIVE, confirm=True, force=True)
    assert code == 1
    assert any("integrity_check" in line for line in lines)
