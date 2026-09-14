"""Command line entry point for the Python localai orchestrator."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from localai import __version__
from localai.anywhere import collect_anywhere_report
from localai.backup import collect_backup_report, collect_restore_report
from localai.dashboard import serve_dashboard
from localai.firewall import collect_firewall_report
from localai.game_mode import collect_game_mode_report
from localai.health import collect_health_report
from localai.installer_vet import collect_vet_report
from localai.model_aliases import collect_model_aliases_report
from localai.model_scout import collect_model_scout_report
from localai.paths import REPO_ROOT
from localai.perf import collect_perf_report
from localai.power import collect_power_report
from localai.public_audit import collect_public_audit_report
from localai.readiness import READY, collect_product_status
from localai.start import collect_start_report
from localai.stop import collect_stop_report
from localai.terminal_check import collect_terminal_check_report
from localai.update import collect_update_report
from localai.warm import collect_set_default_model_report, collect_warm_report
from localai.webui_seed import collect_webui_seed_report

# Windows consoles often use cp1252, which cannot encode characters like the
# '→' that show up in report lines; without this every command can crash with
# UnicodeEncodeError mid-output. Replace unencodable characters instead.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(errors="replace")

app = typer.Typer(
    add_completion=False,
    help="Python orchestrator for the localai stack.",
    invoke_without_command=True,
    no_args_is_help=True,
)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the localai package version and exit.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Manage the localai stack."""
    if version:
        typer.echo(f"localai {__version__}")
        raise typer.Exit()


@app.command()
def backup(
    timeout_sec: Annotated[
        int,
        typer.Option(
            "--timeout-sec",
            help="Seconds to wait for the Docker backup container.",
            min=30,
            max=7200,
        ),
    ] = 900,
) -> None:
    """Back up the Open WebUI data volume."""
    code, lines = collect_backup_report(timeout_sec=timeout_sec)
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def restore(
    archive: Annotated[
        Path,
        typer.Argument(help="Path to the open-webui-*.tar.gz backup to restore."),
    ],
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Confirm this destructive volume overwrite."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Restore even if the container is running."),
    ] = False,
    timeout_sec: Annotated[
        int,
        typer.Option(
            "--timeout-sec",
            help="Seconds to wait for the Docker restore container.",
            min=30,
            max=7200,
        ),
    ] = 900,
) -> None:
    """Restore the Open WebUI data volume from a verified backup archive."""
    code, lines = collect_restore_report(
        archive, confirm=confirm, force=force, timeout_sec=timeout_sec
    )
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def anywhere(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Publish Open WebUI to the tailnet with Tailscale Serve.",
        ),
    ] = False,
    install_tailscale: Annotated[
        bool,
        typer.Option(
            "--install-tailscale",
            help="Install Tailscale with winget before checking access.",
        ),
    ] = False,
    open_url: Annotated[
        bool,
        typer.Option("--open", help="Open the tailnet URL when one is known."),
    ] = False,
    port: Annotated[
        int,
        typer.Option(
            "--port",
            help="Local Open WebUI port to check and publish.",
            min=1,
            max=65535,
        ),
    ] = 3000,
) -> None:
    """Check or repair secure Tailscale access to localai."""
    code, lines = collect_anywhere_report(
        apply=apply,
        install_tailscale=install_tailscale,
        open_url=open_url,
        port=port,
    )
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def dashboard(
    host: Annotated[
        str,
        typer.Option("--host", help="Dashboard bind address."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", help="Dashboard port.", min=1, max=65535),
    ] = 8765,
    open_browser: Annotated[
        bool,
        typer.Option("--open", help="Open the dashboard in the default browser."),
    ] = False,
    web: Annotated[
        bool,
        typer.Option(
            "--web/--window",
            help="Serve in the browser instead of a native desktop window.",
        ),
    ] = False,
) -> None:
    """Launch the localai dashboard (native window by default)."""
    serve_dashboard(host=host, port=port, open_browser=open_browser, window=not web)


@app.command()
def firewall(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Repair localai-owned firewall rules and recreate the block rule.",
        ),
    ] = False,
    no_self_elevate: Annotated[
        bool,
        typer.Option(
            "--no-self-elevate",
            help=(
                "Accepted for legacy parity; Python reports admin failure when needed."
            ),
        ),
    ] = False,
) -> None:
    """Audit LocalAI firewall posture."""
    code, lines = collect_firewall_report(
        apply=apply,
        no_self_elevate=no_self_elevate,
    )
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def game_mode(
    keep_docker: Annotated[
        bool,
        typer.Option("--keep-docker", help="Skip stopping localai Docker containers."),
    ] = False,
    keep_wsl: Annotated[
        bool,
        typer.Option("--keep-wsl", help="Skip WSL shutdown."),
    ] = False,
    disable_warm_task: Annotated[
        bool,
        typer.Option("--disable-warm-task", help="Disable the AI-Warm scheduled task."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show what would be stopped without changing state.",
        ),
    ] = False,
    ollama_timeout_sec: Annotated[
        int,
        typer.Option(
            "--ollama-timeout-sec",
            help="Seconds to wait for Ollama commands.",
            min=3,
            max=120,
        ),
    ] = 20,
) -> None:
    """Free GPU/RAM resources before gaming."""
    code, lines = collect_game_mode_report(
        keep_docker=keep_docker,
        keep_wsl=keep_wsl,
        disable_warm_task=disable_warm_task,
        dry_run=dry_run,
        ollama_timeout_sec=ollama_timeout_sec,
    )
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def status(
    program_root: Annotated[
        str,
        typer.Option("--program-root", help="This installation's own directory."),
    ] = "",
    model: Annotated[
        str,
        typer.Option("--model", help="Model the product is configured to use."),
    ] = "",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable status object."),
    ] = False,
    skip_inference: Annotated[
        bool,
        typer.Option("--skip-inference", help="Do not run the tiny test generation."),
    ] = False,
) -> None:
    """Report whether the local AI is actually usable right now.

    This is the product's own readiness answer, not a prerequisite check: it
    asks the Open WebUI BACKEND, Ollama, and the model itself. A machine whose
    Windows, WSL and Docker checks are all green is still not READY here if
    chat cannot work.
    """
    root = program_root or str(REPO_ROOT)
    snapshot = collect_product_status(
        program_root=root,
        configured_model=model or None,
        verify_inference=not skip_inference,
    )
    if json_output:
        typer.echo(snapshot.to_json())
    else:
        typer.echo(f"==== AFK LocalAI status ====  {snapshot.state}")
        typer.echo(snapshot.message)
        typer.echo("")
        for service in snapshot.services:
            typer.echo(f"  {service.name:<12} {service.state:<12} {service.detail}")
        if snapshot.human_signup_complete is False:
            typer.echo("")
            typer.echo("  Open WebUI still needs its first local account.")
    raise typer.Exit(code=0 if snapshot.state == READY else 1)


@app.command()
def health() -> None:
    """Run the read-only localai stack health check."""
    code, lines = collect_health_report()
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def model_aliases(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview aliases without running ollama cp."),
    ] = False,
    wait_attempts: Annotated[
        int,
        typer.Option(
            "--wait-attempts",
            help="Ollama API reachability attempts before failing.",
            min=1,
            max=30,
        ),
    ] = 30,
    lenient: Annotated[
        bool,
        typer.Option(
            "--lenient",
            help="Treat missing source models as non-fatal (skip them). For clean "
            "/ tier-B boxes that lack this machine's full model zoo.",
        ),
    ] = False,
) -> None:
    """Refresh purpose-based Ollama aliases for model dropdowns."""
    code, lines = collect_model_aliases_report(
        dry_run=dry_run,
        wait_attempts=wait_attempts,
        lenient=lenient,
    )
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def model_scout(
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="Scout (report only) or Prepare (pull a category's top pick). "
            "Promote remains gated (manual).",
        ),
    ] = "Scout",
    top_n: Annotated[
        int,
        typer.Option("--top-n", help="Number of candidates to print.", min=1, max=50),
    ] = 8,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Accepted for legacy parity; no notifications."),
    ] = False,
    probe_timeout_sec: Annotated[
        int,
        typer.Option(
            "--probe-timeout-sec",
            help="Seconds to wait for local hardware probes.",
            min=5,
            max=600,
        ),
    ] = 30,
    no_pull: Annotated[
        bool,
        typer.Option(
            "--no-pull",
            help="Prepare only: do everything except the model download.",
        ),
    ] = False,
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            help="Prepare only: which task category's top pick to prepare "
            "(chat/coding/vision/web-nav/embedding/voice). Default: chat.",
        ),
    ] = None,
    vram_gb: Annotated[
        float | None,
        typer.Option(
            "--vram-gb",
            help="Override the VRAM budget (GB) instead of probing nvidia-smi. "
            "The installer passes the vetted tier budget so non-NVIDIA boxes get "
            "honest picks.",
        ),
    ] = None,
) -> None:
    """Discover, score, and optionally prepare recent GGUF model candidates."""
    # Lines stream via echo as they are produced - Prepare pulls for many
    # minutes, so the console must show progress, not a final dump.
    code, _ = collect_model_scout_report(
        mode=mode,
        top_n=top_n,
        quiet=quiet,
        probe_timeout_sec=probe_timeout_sec,
        no_pull=no_pull,
        category=category,
        vram_gb=vram_gb,
        echo=typer.echo,
    )
    raise typer.Exit(code=code)


# The same command under the shorter name the installer and docs also use.
app.command(name="scout")(model_scout)


@app.command()
def vet(
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit one JSON line (hardware + tier) for the installer to parse.",
        ),
    ] = False,
    timeout_sec: Annotated[
        int,
        typer.Option(
            "--timeout-sec", help="Seconds for hardware probes.", min=5, max=600
        ),
    ] = 30,
) -> None:
    """Vet this machine's hardware and print its localai capability tier."""
    code, lines = collect_vet_report(json_output=json_output, timeout_sec=timeout_sec)
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def webui_seed(
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="Chat model id to seed with Qwen thinking params "
            "(think=false, presence_penalty=1.5, num_ctx).",
        ),
    ] = None,
    num_ctx: Annotated[
        int,
        typer.Option("--num-ctx", help="num_ctx to seed for --model.", min=1024),
    ] = 32768,
    default_model: Annotated[
        str | None,
        typer.Option("--default-model", help="Set the Open WebUI default model."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Print the seed snippet without writing the DB."
        ),
    ] = False,
    timeout_sec: Annotated[
        int,
        typer.Option(
            "--timeout-sec", help="Seconds for the docker exec.", min=5, max=600
        ),
    ] = 30,
) -> None:
    """Seed Open WebUI SQLite config (per-model params + defaults) for the installer."""
    code, lines = collect_webui_seed_report(
        model=model,
        num_ctx=num_ctx,
        default_model=default_model,
        dry_run=dry_run,
        timeout_sec=timeout_sec,
    )
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def set_default_model(
    model: Annotated[
        str,
        typer.Option(
            "--model",
            help="Tag to write as docker-compose.yml DEFAULT_MODELS "
            "(the installer's per-tier pick).",
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print what would change without writing."),
    ] = False,
) -> None:
    """Rewrite docker-compose.yml DEFAULT_MODELS so every reader sees the pick."""
    code, lines = collect_set_default_model_report(model, dry_run=dry_run)
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def public_audit(
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Fail when private markers are found."),
    ] = False,
    context: Annotated[
        int,
        typer.Option("--context", help="Accepted for legacy CLI parity."),
    ] = 0,
    extra_pattern: Annotated[
        list[str] | None,
        typer.Option("--extra-pattern", help="Additional regex to scan for."),
    ] = None,
) -> None:
    """Scan tracked files for public-repo readiness markers."""
    code, lines = collect_public_audit_report(
        strict=strict,
        context=context,
        extra_patterns=tuple(extra_pattern or ()),
    )
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def start(
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Preview the preserved start sequence without changing state.",
        ),
    ] = False,
    no_open: Annotated[
        bool,
        typer.Option("--no-open", help="Skip browser launch after live start."),
    ] = False,
) -> None:
    """Start the localai stack (Ollama, Docker, and the compose services)."""
    code, lines = collect_start_report(dry_run=dry_run, no_open=no_open)
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def perf(
    max_daily_context: Annotated[
        int,
        typer.Option(
            "--max-daily-context",
            help="Daily Ollama context ceiling.",
            min=1024,
            max=32768,
        ),
    ] = 8192,
    max_think_light_context: Annotated[
        int,
        typer.Option(
            "--max-think-light-context",
            help="Expected Qwen3.6 think-light context.",
            min=1024,
            max=32768,
        ),
    ] = 4096,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Return a failure code when warnings exist."),
    ] = False,
) -> None:
    """Read-only performance and context guard."""
    code, lines = collect_perf_report(
        max_daily_context=max_daily_context,
        max_think_light_context=max_think_light_context,
        strict=strict,
    )
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def terminal_check(
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Return a failure code when warnings exist."),
    ] = False,
) -> None:
    """Read-only terminal AI readiness check."""
    code, lines = collect_terminal_check_report(strict=strict)
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def update(
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="Update mode. Check and Apply are ported; Auto remains gated.",
        ),
    ] = "Check",
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Accepted for legacy parity; no notifications."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview Apply mode without changing state."),
    ] = False,
) -> None:
    """Check for localai stack updates."""
    code, lines = collect_update_report(mode=mode, quiet=quiet, dry_run=dry_run)
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def stop(
    keep_containers: Annotated[
        bool,
        typer.Option(
            "--keep-containers",
            help="Skip stopping localai Docker containers.",
        ),
    ] = False,
    keep_models: Annotated[
        bool,
        typer.Option("--keep-models", help="Skip unloading Ollama models."),
    ] = False,
    keep_apps: Annotated[
        bool,
        typer.Option(
            "--keep-apps",
            help="Leave Docker Desktop and the Ollama tray app running.",
        ),
    ] = False,
    timeout_sec: Annotated[
        int,
        typer.Option(
            "--timeout-sec",
            help="Seconds to wait for native stop commands.",
            min=5,
            max=600,
        ),
    ] = 90,
) -> None:
    """Stop localai containers, unload models, and close Docker + Ollama."""
    code, lines = collect_stop_report(
        keep_containers=keep_containers,
        keep_models=keep_models,
        keep_apps=keep_apps,
        timeout_sec=timeout_sec,
    )
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def warm(
    require_ollama: Annotated[
        bool,
        typer.Option(
            "--require-ollama",
            help="Fail when the Ollama server is not reachable.",
        ),
    ] = False,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Model to preload instead of DEFAULT_MODELS."),
    ] = None,
    keep_alive: Annotated[
        str,
        typer.Option("--keep-alive", help="Ollama keep_alive value for warmup."),
    ] = "30m",
    num_ctx: Annotated[
        int,
        typer.Option("--num-ctx", help="Warmup context length; 0 picks the default."),
    ] = 0,
    unload_others: Annotated[
        bool,
        typer.Option(
            "--unload-others",
            help="Unload other loaded Ollama models first.",
        ),
    ] = False,
    skip_if_any_loaded: Annotated[
        bool,
        typer.Option(
            "--skip-if-any-loaded",
            help="Exit successfully when any model is already loaded.",
        ),
    ] = False,
) -> None:
    """Preload the default Ollama model."""
    code, lines = collect_warm_report(
        require_ollama=require_ollama,
        model=model,
        keep_alive=keep_alive,
        num_ctx=num_ctx,
        unload_others=unload_others,
        skip_if_any_loaded=skip_if_any_loaded,
    )
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)


@app.command()
def power(
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Return a failure code when warnings exist."),
    ] = False,
    timeout_sec: Annotated[
        int,
        typer.Option(
            "--timeout-sec",
            help="Seconds to wait for native power-check commands.",
            min=5,
            max=120,
        ),
    ] = 8,
) -> None:
    """Read-only battery and LocalAI power guard."""
    code, lines = collect_power_report(strict=strict, timeout_sec=timeout_sec)
    for line in lines:
        typer.echo(line)
    raise typer.Exit(code=code)
