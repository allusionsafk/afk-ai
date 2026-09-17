"""The installed product's engine: roots, configuration, integrity, start, CLI.

Each test pins a failure that shipped or that the new owned-runtime layout exists
to prevent:

- configuration written INTO the program folder was replaced by every update and
  kept the folder alive after uninstall;
- Start was the engineering workbench's start (Tailscale, warm-up, engineering
  health) and opened the browser itself, bypassing any chat readiness gate;
- the product runtime silently depended on packages resolved from an index.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from localai import product_cli, product_runtime, readiness
from localai.installation import verify_installation
from localai.ops import CommandResult
from localai.product_config import (
    DEFAULT_MODEL_KEY,
    SEARXNG_SECRET_KEY,
    LayoutError,
    configured_model,
    ensure_runtime_config,
    read_env_file,
    resolve_layout,
    valid_model_tag,
    write_env_file,
)

MODEL = "qwen3.5:4b-16k"


def _layout(tmp_path: Path) -> Any:
    program = tmp_path / "Programs" / "AFK AI"
    program.mkdir(parents=True)
    (program / "docker-compose.yml").write_text("name: afk-localai\n", encoding="utf-8")
    return resolve_layout(program, tmp_path / "Data" / "AFK AI")


# ----------------------------------------------------------------------- roots


def test_roots_must_be_absolute_and_disjoint(tmp_path: Path) -> None:
    program = tmp_path / "Program"
    with pytest.raises(LayoutError):
        resolve_layout("relative/program", tmp_path / "data")
    with pytest.raises(LayoutError):
        resolve_layout(program, program / "Data")
    with pytest.raises(LayoutError):
        resolve_layout(tmp_path / "Data" / "Program", tmp_path / "Data")


def test_similar_prefixes_are_not_nesting(tmp_path: Path) -> None:
    layout = resolve_layout(tmp_path / "AFK", tmp_path / "AFK Data")

    assert layout.data_root.name == "AFK Data"


# ---------------------------------------------------------------- configuration


def test_configuration_lives_in_the_data_root_never_the_program_root(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    before = sorted(p.name for p in layout.program_root.iterdir())

    ensure_runtime_config(layout, model=MODEL)

    assert sorted(p.name for p in layout.program_root.iterdir()) == before
    values = read_env_file(layout.runtime_env)
    assert values[DEFAULT_MODEL_KEY] == MODEL
    assert len(values[SEARXNG_SECRET_KEY]) == 48


def test_the_generated_credential_survives_reconfiguration(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    ensure_runtime_config(layout, model=MODEL)
    first = read_env_file(layout.runtime_env)[SEARXNG_SECRET_KEY]

    result = ensure_runtime_config(layout, model="qwen3.5:2b-8k")

    assert result.changed
    assert read_env_file(layout.runtime_env)[SEARXNG_SECRET_KEY] == first


def test_the_legacy_program_folder_credential_is_migrated_then_removed(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    legacy = "A1B2C3D4" * 6  # secret-shaped, deliberately low-entropy
    layout.legacy_env.write_text(f"SEARXNG_SECRET={legacy}\n", encoding="utf-8")

    result = ensure_runtime_config(layout)

    assert result.migrated_legacy_secret
    assert result.removed_legacy_env
    assert not layout.legacy_env.exists()
    assert read_env_file(layout.runtime_env)[SEARXNG_SECRET_KEY] == legacy


def test_a_legacy_file_with_unknown_content_is_left_for_a_human(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    layout.legacy_env.write_text(
        f"{SEARXNG_SECRET_KEY}=" + "a" * 16 + "\nSOMETHING_ELSE=1\n", encoding="utf-8"
    )

    result = ensure_runtime_config(layout)

    assert not result.removed_legacy_env
    assert layout.legacy_env.exists()


@pytest.mark.parametrize(
    "value",
    ["qwen3.5:9b\nEVIL=1", "../../etc", "", "a b", "model:tag:extra", "X" * 300],
)
def test_invalid_model_tags_are_refused(tmp_path: Path, value: str) -> None:
    layout = _layout(tmp_path)

    assert not valid_model_tag(value)
    with pytest.raises(ValueError):
        ensure_runtime_config(layout, model=value)


def test_env_writer_refuses_injection(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_env_file(tmp_path / "x.env", {"GOOD": "value\nINJECTED=1"})
    with pytest.raises(ValueError):
        write_env_file(tmp_path / "x.env", {"lower": "value"})


def test_the_configured_model_falls_back_to_the_setup_checkpoint(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    layout.state_root.mkdir(parents=True)
    layout.installer_state.write_text(
        json.dumps({"models": {"chat": {"tag": MODEL}}}), encoding="utf-8"
    )

    assert configured_model(layout) == MODEL

    layout.installer_state.write_text(
        json.dumps({"models": {"chat": {"tag": "bad\ntag"}}}), encoding="utf-8"
    )
    assert configured_model(layout) is None


# -------------------------------------------------------------------- integrity


def _manifest(program: Path, files: dict[str, bytes]) -> None:
    rows = []
    for relative, content in files.items():
        path = program / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        rows.append(
            {
                "path": relative,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest().upper(),
            }
        )
    (program / "payload-manifest.json").write_text(
        json.dumps({"schema_version": 1, "source_commit": "c" * 40, "files": rows}),
        encoding="utf-8",
    )


def test_an_intact_installation_verifies(tmp_path: Path) -> None:
    program = tmp_path / "Program"
    _manifest(
        program,
        {
            "src/localai/__init__.py": b"",
            "runtime/python/python.exe": b"MZ",
            "runtime/afk-runtime.json": b"{}",
            "installer/afk-payload.py": b"#",
        },
    )

    assert verify_installation(program).intact


def test_corrupt_missing_and_stale_files_are_all_reported(tmp_path: Path) -> None:
    program = tmp_path / "Program"
    _manifest(
        program,
        {
            "src/localai/__init__.py": b"",
            "src/localai/readiness.py": b"current",
            "runtime/python/python314.dll": b"dll",
        },
    )
    (program / "src/localai/readiness.py").write_bytes(b"tampered")
    (program / "runtime/python/python314.dll").unlink()
    # A module an older version shipped and this one removed: still importable.
    (program / "src/localai/removed_in_this_version.py").write_text("x = 1\n")

    report = verify_installation(program)

    assert not report.intact
    assert report.mismatched == ["src/localai/readiness.py"]
    assert report.missing == ["runtime/python/python314.dll"]
    assert report.unexpected == ["src/localai/removed_in_this_version.py"]


def test_no_manifest_is_never_intact(tmp_path: Path) -> None:
    assert not verify_installation(tmp_path).intact


def test_manifest_paths_cannot_escape_the_installation(tmp_path: Path) -> None:
    program = tmp_path / "Program"
    program.mkdir()
    (tmp_path / "outside.txt").write_text("x")
    (program / "payload-manifest.json").write_text(
        json.dumps({"files": [{"path": "../outside.txt", "sha256": "00"}]})
    )

    report = verify_installation(program)

    assert report.checked == 0
    assert not report.intact


# ------------------------------------------------------------------------ start


class _FakeMachine:
    """Docker, Ollama and the backend as a small state machine."""

    def __init__(self, compose: str, *, foreign: bool = False) -> None:
        self.compose = compose
        self.foreign = foreign
        self.docker_up = False
        self.ollama_up = False
        self.services_up = False
        self.calls: list[list[str]] = []
        self.launched: list[str] = []

    def runner(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> CommandResult:
        argv = [str(a) for a in args]
        self.calls.append(argv)
        if not self.docker_up:
            return CommandResult(tuple(argv), 1, "", "pipe not found")
        if "info" in argv:
            return CommandResult(tuple(argv), 0, "id\n", "")
        if "exec" in argv:
            return CommandResult(tuple(argv), 0, "visible\n", "")
        rows = ""
        if self.services_up:
            rows += (
                f"{'a' * 64}\topen-webui\tw\trunning\tUp\t{self.compose}\tafk-localai\n"
            )
        if self.foreign:
            rows += (
                f"{'f' * 64}\topen-webui\tx\trunning\tUp\tQ:\\other.yml\tafk-localai\n"
            )
        return CommandResult(tuple(argv), 0, rows, "")

    def probe(self, url: str, *, timeout_sec: float) -> tuple[int, str]:
        if "11434" in url:
            if not self.ollama_up:
                raise ConnectionRefusedError
            return 200, json.dumps({"models": [{"name": MODEL}]})
        if not self.services_up:
            raise ConnectionRefusedError
        return 200, json.dumps({"version": "0.11.0"})

    def launch(self, path: Path, *, env: dict[str, str] | None = None) -> bool:
        self.launched.append(path.name)
        if "Docker" in path.name:
            self.docker_up = True
        if "ollama" in path.name.lower():
            self.ollama_up = True
        return True

    def stream(self, args: list[str], **_kwargs: Any) -> CommandResult:
        self.calls.append(args)
        self.services_up = True
        return CommandResult(tuple(args), 0, "", "")


def _deps(machine: _FakeMachine) -> product_runtime.StartDeps:
    def inference(*_args: Any, **_kwargs: Any) -> tuple[int, str]:
        return 200, json.dumps({"done": True, "eval_count": 3, "response": "ok"})

    clock = iter(float(i) for i in range(100000))
    return product_runtime.StartDeps(
        runner=machine.runner,
        probe=machine.probe,
        inference=inference,
        launch=machine.launch,
        stream=machine.stream,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
    )


def test_start_brings_up_only_this_installation_and_then_qualifies(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    ensure_runtime_config(layout, model=MODEL)
    machine = _FakeMachine(str(layout.compose_file.resolve()))
    events: list[tuple[str, str]] = []

    code, status = product_runtime.start_product(
        layout,
        emit=lambda event_type, phase, **kw: events.append((event_type, kw["code"])),
        deps=_deps(machine),
    )

    assert code == 0
    assert status.state == readiness.READY
    assert machine.launched == ["ollama app.exe", "Docker Desktop.exe"]
    up = next(c for c in machine.calls if "up" in c)
    assert up[up.index("--file") + 1] == str(layout.compose_file)
    assert up[up.index("--env-file") + 1] == str(layout.runtime_env)
    assert ("progress", "qualify") in events


def test_start_never_opens_a_browser_or_runs_workbench_steps() -> None:
    """Routing to chat is the shell's gated decision, never a side effect."""
    source = Path(product_runtime.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]

    for forbidden in (
        "webbrowser",
        "tailscale",
        "collect_health_report",
        "collect_warm_report",
        "anywhere",
        '"down"',
        "prune",
        "volume",
        "taskkill",
    ):
        assert forbidden not in body, forbidden


def test_start_refuses_when_a_foreign_stack_claims_the_project_name(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    ensure_runtime_config(layout, model=MODEL)
    machine = _FakeMachine(str(layout.compose_file.resolve()), foreign=True)
    machine.docker_up = machine.ollama_up = True

    code, status = product_runtime.start_product(
        layout, emit=lambda *a, **k: None, deps=_deps(machine)
    )

    assert code == 1
    assert status.reason == "FOREIGN_PROJECT_COLLISION"
    assert not any("up" in c for c in machine.calls)


def test_start_reports_honestly_when_docker_never_starts(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    machine = _FakeMachine(str(layout.compose_file.resolve()))
    machine.ollama_up = True
    deps = _deps(machine)
    deps.launch = lambda path, env=None: True  # Docker Desktop never comes up

    code, status = product_runtime.start_product(
        layout, emit=lambda *a, **k: None, deps=deps
    )

    assert code == 1
    assert status.state != readiness.READY
    assert status.reason == "DOCKER_UNREACHABLE"


# ------------------------------------------------------------------------- pull


def test_model_download_reports_real_progress(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []

    def stream(
        url: str, payload: dict[str, Any], *, timeout_sec: float
    ) -> Iterator[dict[str, Any]]:
        assert payload == {"model": "qwen3.5:4b", "stream": True}
        yield {"status": "pulling manifest"}
        for completed in (0, 250, 500, 1000):
            yield {"status": "downloading", "total": 1000, "completed": completed}
        yield {"status": "success"}

    ticks = iter(float(i * 5) for i in range(100))
    ok, _detail = product_runtime.pull_model(
        "qwen3.5:4b",
        emit=lambda et, ph, **kw: events.append(kw),
        stream=stream,
        clock=lambda: next(ticks),
    )

    assert ok
    percents = [e["data"]["percent"] for e in events if e.get("data")]
    assert percents == [0, 25, 50, 100]


def test_a_failed_download_is_not_success() -> None:
    def stream(*_a: Any, **_k: Any) -> Iterator[dict[str, Any]]:
        yield {"error": "pull model manifest: file does not exist"}

    ok, detail = product_runtime.pull_model(
        "qwen3.5:4b", emit=lambda *a, **k: None, stream=stream
    )

    assert not ok
    assert "failed" in detail


def test_a_download_that_ends_without_success_is_not_success() -> None:
    def stream(*_a: Any, **_k: Any) -> Iterator[dict[str, Any]]:
        yield {"status": "downloading", "total": 10, "completed": 5}

    ok, _detail = product_runtime.pull_model(
        "qwen3.5:4b", emit=lambda *a, **k: None, stream=stream
    )

    assert not ok


# ------------------------------------------------------------------ seed target


def test_open_webui_seeding_execs_into_the_proven_container_only(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    compose = str(layout.compose_file.resolve())
    calls: list[list[str]] = []

    def runner(args: Sequence[str], **_kwargs: Any) -> CommandResult:
        argv = [str(a) for a in args]
        calls.append(argv)
        if "exec" in argv:
            return CommandResult(tuple(argv), 0, "seeded", "")
        rows = (
            f"{'a' * 64}\topen-webui\tw\trunning\tUp\t{compose}\tafk-localai\n"
            f"{'b' * 64}\topen-webui\tx\trunning\tUp\tQ:\\other.yml\tlocalai\n"
        )
        return CommandResult(tuple(argv), 0, rows, "")

    exec_fn = product_runtime.owned_exec(layout, runner=runner)
    result = exec_fn("open-webui", ["python", "-c", "pass"], timeout_sec=5)

    assert result.code == 0
    execs = [c for c in calls if "exec" in c]
    assert len(execs) == 1 and "a" * 64 in execs[0] and "compose" not in execs[0]


# -------------------------------------------------------------------------- CLI


def test_cli_accepts_roots_before_or_after_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = _layout(tmp_path)
    roots = [
        "--program-root",
        str(layout.program_root),
        "--data-root",
        str(layout.data_root),
    ]

    for argv in (
        roots + ["configure", "--model", MODEL],
        ["configure", "--model", MODEL] + roots,
    ):
        assert product_cli.main(argv) == 0
    assert configured_model(layout) == MODEL


def test_cli_status_refuses_a_nested_data_root_with_a_parseable_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = _layout(tmp_path)

    code = product_cli.main(
        [
            "--program-root",
            str(layout.program_root),
            "--data-root",
            str(layout.program_root / "Data"),
            "status",
            "--json",
        ]
    )

    assert code == product_cli.EXIT_REFUSED
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["state"] == "Unknown"
    assert payload["chat"]["ready"] is False


def test_cli_diagnostics_output_must_stay_inside_the_diagnostics_folder(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    roots = [
        "--program-root",
        str(layout.program_root),
        "--data-root",
        str(layout.data_root),
    ]

    escaped = product_cli.main(
        roots + ["diagnostics", "--output", str(tmp_path / "anywhere.json")]
    )
    relative = product_cli.main(roots + ["diagnostics", "--output", "rel.json"])

    assert escaped == product_cli.EXIT_REFUSED
    assert relative == product_cli.EXIT_USAGE
    assert not (tmp_path / "anywhere.json").exists()


def test_cli_stop_needs_no_data_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "localai.afk_ownership.run_command",
        lambda *a, **k: CommandResult(("docker",), 1, "", "absent"),
    )

    assert product_cli.main(["--program-root", str(tmp_path), "stop"]) == 0


# ------------------------------------------------------- owned runtime invariant


PRODUCT_ENTRY_MODULES = ("product_cli",)


def _imports(module: str) -> tuple[set[str], set[str]]:
    source = (Path(product_cli.__file__).parent / f"{module}.py").read_text(
        encoding="utf-8"
    )
    local: set[str] = set()
    external: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "localai":
                local.update(alias.name for alias in node.names)
                continue
            names = [node.module]
        for name in names:
            top, _, rest = name.partition(".")
            if top == "localai":
                local.add(rest.split(".")[0])
            elif top not in sys.stdlib_module_names and top != "__future__":
                external.add(name)
    return local, external


def test_the_product_runtime_needs_no_third_party_packages() -> None:
    """The owned runtime ships NO site-packages; nothing is resolved from an index.

    Every module reachable from the product command surface must therefore be
    standard library only. A future import of, say, ``typer`` in this closure
    would work in a development venv and fail on every installed PC.
    """
    seen: set[str] = set()
    pending = list(PRODUCT_ENTRY_MODULES)
    offenders: dict[str, set[str]] = {}
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = Path(product_cli.__file__).parent / f"{module}.py"
        if not path.is_file():
            continue
        local, external = _imports(module)
        if external:
            offenders[module] = external
        pending.extend(local - seen)

    assert "readiness" in seen and "diagnostics" in seen and "webui_seed" in seen
    assert offenders == {}
