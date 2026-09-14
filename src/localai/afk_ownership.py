"""Ownership-scoped shutdown for the AFK LocalAI product.

Uninstalling AFK LocalAI may stop only resources whose AFK ownership is
*proven*. The legacy ``localai stop`` path is unsafe for that job: it resolves
through the ambient ``localai`` package name, so on a machine that also has the
private engineering workbench installed editable it runs the *workbench's* code
against the *workbench's* ``REPO_ROOT`` - and then force-closes Docker Desktop
and Ollama for the whole machine.

Ownership here is established by PATH, not by name. A container is AFK-owned
only when Docker reports its ``com.docker.compose.project.config_files`` label
as exactly the ``docker-compose.yml`` that this installation shipped inside its
own program root. A project *name* is deliberately not sufficient: both the
product and the private workbench can present a project called ``localai``.

Anything this module cannot prove, it refuses to touch. It never force-closes
Docker Desktop or Ollama, never unloads or removes models, never prunes Docker,
and never removes volumes.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from localai.ops import CommandResult, run_command

COMPOSE_FILENAME = "docker-compose.yml"
CONFIG_FILES_LABEL = "com.docker.compose.project.config_files"
PROJECT_LABEL = "com.docker.compose.project"

_REFUSAL = "Refusing to stop shared, unrelated, or unproven resources."


class CommandRunner(Protocol):
    """The subset of :func:`localai.ops.run_command` this module depends on."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True)
class OwnedStack:
    """A compose project proven to belong to this AFK LocalAI installation."""

    project: str
    compose_file: Path
    container_ids: tuple[str, ...]


def docker_executable() -> str:
    """Return Docker Desktop's bundled docker.exe, else plain ``docker``."""
    program_files = os.environ.get("PROGRAMFILES", "")
    if program_files:
        bundled = (
            Path(program_files)
            / "Docker"
            / "Docker"
            / "resources"
            / "bin"
            / "docker.exe"
        )
        if bundled.exists():
            return str(bundled)
    return "docker"


def owned_compose_file(program_root: Path | str | None) -> Path | None:
    """Resolve the compose file this installation owns, or None when unproven.

    Ownership requires an absolute program root that actually contains the
    compose file the payload shipped. Anything else is unproven by definition.
    """
    if program_root is None:
        return None
    candidate = Path(program_root)
    if not candidate.is_absolute():
        return None
    compose = candidate / COMPOSE_FILENAME
    if not compose.is_file():
        return None
    return compose.resolve()


def _same_path(left: str, right: str) -> bool:
    """Compare two filesystem paths the way the host platform does."""
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(right)
    )


def _label_format() -> str:
    """Build the ``docker ps --format`` template for the ownership labels."""
    project = "{{.Label " + chr(34) + PROJECT_LABEL + chr(34) + "}}"
    config = "{{.Label " + chr(34) + CONFIG_FILES_LABEL + chr(34) + "}}"
    return "{{.ID}}\t" + project + "\t" + config


def discover_owned_stack(
    compose_file: Path,
    *,
    runner: CommandRunner = run_command,
    timeout_sec: int = 90,
) -> tuple[OwnedStack | None, str | None]:
    """Find containers labelled with exactly ``compose_file``.

    Returns ``(stack, None)`` when ownership is proven, ``(None, None)`` when
    nothing AFK-owned exists, and ``(None, reason)`` when ownership is
    ambiguous or undecidable - in which case the caller must not act.
    """
    # Every container is listed and matched here rather than with a daemon-side
    # "--filter label=..." query: that filter's matching rules vary between
    # engines, and the value is a Windows path full of backslashes and colons.
    # Deciding ownership locally keeps the rule exact and inspectable.
    result = runner(
        [
            docker_executable(),
            "ps",
            "--all",
            "--no-trunc",
            "--format",
            _label_format(),
        ],
        timeout_sec=timeout_sec,
    )
    if result.code == 124:
        return None, "Docker did not answer in time; ownership is undecidable."
    if result.code != 0:
        return None, "Docker is unavailable; ownership is undecidable."

    projects: set[str] = set()
    containers: list[str] = []
    for line in result.stdout.splitlines():
        # Only newline noise is trimmed: a container with no compose labels
        # emits trailing empty fields, and .strip() would eat those tabs and
        # make a perfectly readable row look malformed.
        row = line.rstrip("\r\n")
        if not row.strip():
            continue
        fields = row.split("\t")
        if len(fields) != 3:
            # A row we cannot read is a container we cannot rule in or out.
            return None, f"Unreadable Docker label row: {row!r}"
        container_id, project, config_files = (field.strip() for field in fields)
        if not container_id:
            return None, f"Incomplete Docker label row: {row!r}"
        if not config_files or not project:
            # Not a compose container at all, so demonstrably not ours.
            continue
        if not _same_path(config_files, str(compose_file)):
            # Someone else's stack - the private workbench, or any other
            # project sharing this machine. Left strictly alone.
            continue
        projects.add(project)
        containers.append(container_id)

    if not containers:
        return None, None
    if len(projects) != 1:
        return None, (
            f"AFK-owned containers report conflicting project names: "
            f"{sorted(projects)}."
        )
    return (
        OwnedStack(
            project=projects.pop(),
            compose_file=compose_file,
            container_ids=tuple(containers),
        ),
        None,
    )


def collect_afk_stop_report(
    *,
    program_root: Path | str | None,
    runner: CommandRunner = run_command,
    timeout_sec: int = 90,
) -> tuple[int, list[str]]:
    """Stop only this installation's own compose project.

    Always exits 0: an uninstall must not fail because a stack was already
    gone, Docker was absent, or ownership could not be proven. The report
    records exactly what was and was not touched.
    """
    lines = ["==== Stop AFK LocalAI ===="]

    compose_file = owned_compose_file(program_root)
    if compose_file is None:
        lines.append("[1/2] Ownership: no AFK-owned compose file could be resolved.")
        lines.append(_REFUSAL)
        return _finish(lines)

    lines.append(f"[1/2] Ownership: AFK-owned compose file {compose_file}")
    stack, problem = discover_owned_stack(
        compose_file, runner=runner, timeout_sec=timeout_sec
    )
    if problem is not None:
        lines.append(f"[2/2] {problem}")
        lines.append(_REFUSAL)
        return _finish(lines)
    if stack is None:
        lines.append("[2/2] No AFK-owned containers are present; nothing to stop.")
        return _finish(lines)

    lines.append(
        f"[2/2] Stopping {len(stack.container_ids)} AFK-owned container(s) "
        f"in project '{stack.project}'."
    )
    # Stop the exact containers whose ownership was proven, addressed by id.
    #
    # NOT "docker compose --project-name <p> --file <f> stop": compose resolves
    # targets from the project name and the service names in the file, and
    # ignores the config_files label entirely. Verified against a live daemon -
    # a compose stop scoped with one file stopped a container labelled as
    # belonging to a different file. Since the shipped compose declares
    # "name: localai", and the private workbench's compose declares the same,
    # that is not hypothetical: proving ownership by label and then acting by
    # project name would hand execution to a weaker key than the proof.
    result = runner(
        [docker_executable(), "stop", *stack.container_ids],
        timeout_sec=timeout_sec,
    )
    if result.code == 124:
        lines.append(f"WARNING: docker stop timed out after {timeout_sec}s.")
    elif result.code != 0:
        lines.append(
            f"WARNING: docker stop failed with exit {result.code}. "
            f"{result.text.strip()}"
        )
    else:
        lines.append("  AFK-owned containers stopped.")
    return _finish(lines)


def _finish(lines: list[str]) -> tuple[int, list[str]]:
    lines.append("")
    lines.append("Docker Desktop, Ollama, and models were left untouched.")
    lines.append("AFK LocalAI stop complete.")
    return 0, lines
