"""Uninstalling AFK LocalAI must only ever stop what it can prove it owns.

The defect these cover: the uninstaller used to run ``py -m localai stop``,
which resolves through the ambient ``localai`` name. On a machine that also has
the private engineering workbench installed editable that unloaded every loaded
Ollama model, tore down the workbench's compose project, and force-closed
Docker Desktop and Ollama for the whole machine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from localai import afk_ownership
from localai.ops import CommandResult

# Stands in for the private engineering workbench, or any other checkout sharing
# the machine. Deliberately synthetic: a reserved-for-documentation drive letter
# and a self-describing directory, so it can never be read as a real developer's
# home path. What the tests actually exercise is that this path is *different*
# from the installation's own compose file, so any absolute path serves - and a
# fake one keeps one contributor's machine layout out of a public repository.
WORKBENCH_COMPOSE = r"Q:\example-other-checkout\docker-compose.yml"

# Nothing the AFK uninstaller runs may contain any of these.
FORBIDDEN = (
    "taskkill",
    "ollama",
    "Docker Desktop.exe",
    "com.docker.backend.exe",
    "prune",
    "down",
    "rm",
    "--volumes",
    "-v",
)


class RecordingRunner:
    """Captures every command instead of running it."""

    def __init__(self, *responses: CommandResult) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        argv = tuple(str(arg) for arg in args)
        self.calls.append(argv)
        if self.responses:
            return self.responses.pop(0)
        return CommandResult(argv, 0, "", "")

    @property
    def flattened(self) -> str:
        return " ".join(" ".join(call) for call in self.calls)


def _install(tmp_path: Path) -> Path:
    """Create a program root that looks like a real AFK installation."""
    program_root = tmp_path / "Programs" / "AFK LocalAI"
    program_root.mkdir(parents=True)
    (program_root / "docker-compose.yml").write_text("services: {}\n")
    return program_root


def _ps_row(container: str, project: str, config_files: str) -> str:
    return f"{container}\t{project}\t{config_files}\n"


def test_stop_refuses_when_the_program_root_is_unknown() -> None:
    runner = RecordingRunner()

    code, lines = afk_ownership.collect_afk_stop_report(
        program_root=None, runner=runner
    )

    assert code == 0
    assert runner.calls == []
    assert any("no AFK-owned compose file" in line for line in lines)


def test_stop_refuses_when_the_program_root_is_relative(tmp_path: Path) -> None:
    runner = RecordingRunner()

    code, _ = afk_ownership.collect_afk_stop_report(
        program_root="relative/path", runner=runner
    )

    assert code == 0
    assert runner.calls == []


def test_stop_refuses_when_the_installation_has_no_compose_file(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()

    code, _ = afk_ownership.collect_afk_stop_report(
        program_root=tmp_path, runner=runner
    )

    assert code == 0
    assert runner.calls == []


def test_stop_does_nothing_when_no_owned_container_exists(tmp_path: Path) -> None:
    program_root = _install(tmp_path)
    runner = RecordingRunner(CommandResult((), 0, "", ""))

    code, lines = afk_ownership.collect_afk_stop_report(
        program_root=program_root, runner=runner
    )

    assert code == 0
    # Discovery only - nothing was stopped.
    assert len(runner.calls) == 1
    assert "ps" in runner.calls[0]
    assert any("nothing to stop" in line for line in lines)


def test_stop_addresses_exactly_the_proven_containers(tmp_path: Path) -> None:
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())
    listing = _ps_row("abc123", "afklocalai", compose)
    runner = RecordingRunner(
        CommandResult((), 0, listing, ""),
        CommandResult((), 0, "", ""),
    )

    code, lines = afk_ownership.collect_afk_stop_report(
        program_root=program_root, runner=runner
    )

    assert code == 0
    assert len(runner.calls) == 2
    stop_call = runner.calls[1]
    assert stop_call[1:] == ("stop", "abc123")
    assert any("AFK-owned containers stopped" in line for line in lines)


def test_stop_never_delegates_to_a_project_scoped_compose_command(
    tmp_path: Path,
) -> None:
    """Compose resolves targets by project + service name, not by the label.

    Verified against a live daemon: `docker compose --project-name P --file B
    stop` stopped a container labelled as belonging to file A. The shipped
    compose declares `name: localai` and so does the private workbench's, so
    proving ownership by label and then acting by project name would hand
    execution to a weaker key than the proof.
    """
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())
    runner = RecordingRunner(
        CommandResult((), 0, _ps_row("abc123", "localai", compose), ""),
        CommandResult((), 0, "", ""),
    )

    afk_ownership.collect_afk_stop_report(program_root=program_root, runner=runner)

    stop_call = runner.calls[1]
    assert "compose" not in stop_call
    assert "--project-name" not in stop_call
    assert "--file" not in stop_call


def test_stop_skips_a_foreign_container_sharing_the_project_name(
    tmp_path: Path,
) -> None:
    """The real collision: same project name, different owning checkout."""
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())
    # Both projects are called "localai" - only the config path differs.
    listing = (
        _ps_row("foreign99", "localai", WORKBENCH_COMPOSE)
        + _ps_row("owned01", "localai", compose)
    )
    runner = RecordingRunner(
        CommandResult((), 0, listing, ""),
        CommandResult((), 0, "", ""),
    )

    afk_ownership.collect_afk_stop_report(program_root=program_root, runner=runner)

    stop_call = runner.calls[1]
    assert "owned01" in stop_call
    assert "foreign99" not in stop_call
    assert "foreign99" not in runner.flattened


def test_stop_leaves_the_private_workbench_stack_alone(tmp_path: Path) -> None:
    """The private workbench must survive an AFK uninstall untouched."""
    program_root = _install(tmp_path)
    # Another checkout's compose project, shaped the way Docker reports one:
    # a project also called "localai", distinguished only by its config path.
    listing = (
        _ps_row("deadbeef01", "localai", WORKBENCH_COMPOSE)
        + _ps_row("deadbeef02", "localai", WORKBENCH_COMPOSE)
    )
    runner = RecordingRunner(CommandResult((), 0, listing, ""))

    code, lines = afk_ownership.collect_afk_stop_report(
        program_root=program_root, runner=runner
    )

    assert code == 0
    # Discovery happened; nothing was stopped.
    assert len(runner.calls) == 1
    assert any("nothing to stop" in line for line in lines)
    assert "deadbeef" not in runner.flattened


def test_stop_targets_only_owned_containers_in_a_mixed_listing(
    tmp_path: Path,
) -> None:
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())
    listing = (
        _ps_row("workbench1", "localai", WORKBENCH_COMPOSE)
        + _ps_row("afk1", "afklocalai", compose)
        + _ps_row("unrelated1", "", "")
    )
    runner = RecordingRunner(
        CommandResult((), 0, listing, ""),
        CommandResult((), 0, "", ""),
    )

    code, _ = afk_ownership.collect_afk_stop_report(
        program_root=program_root, runner=runner
    )

    assert code == 0
    stop_call = runner.calls[1]
    assert stop_call[1:] == ("stop", "afk1")
    # Nothing belonging to the workbench may reach a stop command.
    assert WORKBENCH_COMPOSE not in runner.flattened
    assert "workbench1" not in runner.flattened


def test_stop_refuses_when_owned_projects_conflict(tmp_path: Path) -> None:
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())
    listing = _ps_row("a1", "afklocalai", compose) + _ps_row("b2", "other", compose)
    runner = RecordingRunner(CommandResult((), 0, listing, ""))

    code, lines = afk_ownership.collect_afk_stop_report(
        program_root=program_root, runner=runner
    )

    assert code == 0
    assert len(runner.calls) == 1
    assert any("conflicting project names" in line for line in lines)


def test_stop_refuses_when_docker_is_unavailable(tmp_path: Path) -> None:
    program_root = _install(tmp_path)
    runner = RecordingRunner(CommandResult((), 1, "", "cannot connect"))

    code, lines = afk_ownership.collect_afk_stop_report(
        program_root=program_root, runner=runner
    )

    assert code == 0
    assert len(runner.calls) == 1
    assert any("ownership is undecidable" in line for line in lines)


def test_stop_refuses_when_docker_times_out(tmp_path: Path) -> None:
    program_root = _install(tmp_path)
    runner = RecordingRunner(CommandResult((), 124, "", ""))

    code, lines = afk_ownership.collect_afk_stop_report(
        program_root=program_root, runner=runner
    )

    assert code == 0
    assert len(runner.calls) == 1
    assert any("did not answer in time" in line for line in lines)


def test_stop_never_force_closes_docker_desktop_ollama_or_models(
    tmp_path: Path,
) -> None:
    """The whole point of the fix: no global process or model destruction."""
    program_root = _install(tmp_path)
    compose = str((program_root / "docker-compose.yml").resolve())
    runner = RecordingRunner(
        CommandResult((), 0, _ps_row("abc123", "afklocalai", compose), ""),
        CommandResult((), 0, "", ""),
    )

    afk_ownership.collect_afk_stop_report(program_root=program_root, runner=runner)

    issued = runner.flattened
    assert "taskkill" not in issued
    assert "ollama" not in issued.lower()
    assert "prune" not in issued
    assert "Docker Desktop.exe" not in issued
    # "docker compose stop", never "down" (which would remove containers).
    assert " down" not in issued
    assert "--volumes" not in issued


def test_module_source_contains_no_globally_destructive_commands() -> None:
    """Guards the invariant against future edits to the module itself."""
    source = Path(afk_ownership.__file__).read_text(encoding="utf-8")
    # Only look at code, not the docstring that explains what is forbidden.
    body = source.split('"""', 2)[-1]
    for token in ("taskkill", "system prune", "volume rm", "ollama stop"):
        assert token not in body, f"{token!r} must never appear in afk_ownership"
