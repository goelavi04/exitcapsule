"""Command-line interface.

Why: every stage must be runnable alone (`exitcapsule <stage> --run <id>`) and
`analyze` chains them. This is a thin layer over `engine.pipeline`.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from engine import __version__
from engine.config import get_settings
from engine.events import EventBus, ProgressEvent
from engine.pipeline import STAGES, Run, StageError, run_pipeline, stage_fn

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")

app = typer.Typer(
    name="exitcapsule",
    help="ExitCapsule: AI-powered dependency resilience engine (Gemma 4).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_ICONS = {
    "started": "[cyan]>[/]",
    "done": "[green]OK[/]",
    "failed": "[red]FAIL[/]",
    "skipped": "[yellow]skip[/]",
    "info": "[dim]-[/]",
    "progress": "[dim].[/]",
}


def _render(ev: ProgressEvent) -> None:
    pct = f" {ev.pct:.0f}%" if ev.pct is not None and ev.status == "progress" else ""
    console.print(f"{_ICONS.get(ev.status, '')} [bold]{ev.stage}[/]{pct} {ev.message}")


def _bus() -> EventBus:
    bus = EventBus()
    bus.subscribe(_render)
    return bus


def _timings(run: Run) -> None:
    t = Table(title=f"Run {run.info.run_id} — stage timings")
    t.add_column("stage")
    t.add_column("status")
    t.add_column("seconds", justify="right")
    total = 0.0
    for st in run.info.timings:
        t.add_row(st.stage, st.status, f"{st.seconds:.1f}")
        total += st.seconds
    t.add_row("[bold]total[/]", "", f"[bold]{total:.1f}[/]")
    console.print(t)


def _run_stages(run: Run, stages: list[str]) -> None:
    try:
        run_pipeline(run, stages)
    except StageError as exc:
        console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(2) from exc
    finally:
        _timings(run)
    console.print(f"run dir: {run.dir}")


RunOpt = Annotated[str, typer.Option("--run", "-r", help="Run ID (or 'latest').")]


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"exitcapsule {__version__}")


@app.command()
def analyze(
    source: Annotated[str, typer.Argument(help="Git URL or local path of a Python repo.")],
    deps: Annotated[
        list[str] | None, typer.Option("--dep", help="Restrict to these dependencies.")
    ] = None,
    python: Annotated[
        list[str] | None, typer.Option("--python", help="Python versions (3.12/3.13).")
    ] = None,
    replay: Annotated[bool, typer.Option(help="Use only cached Gemma/metadata responses.")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass the Gemma cache.")] = False,
    sandbox: Annotated[str, typer.Option(help="local | docker")] = "local",
    until: Annotated[str | None, typer.Option(help="Stop after this stage.")] = None,
) -> None:
    """Run the full pipeline: ingest → scan → baseline → plan → migrate → compare → capsule."""
    opts = {
        "deps": deps or [],
        "python_versions": python or get_settings().python_versions,
        "replay": replay,
        "no_cache": no_cache,
        "sandbox": sandbox,
    }
    run = Run.create(source, opts, bus=_bus())
    stages = STAGES if until is None else STAGES[: STAGES.index(until) + 1]
    _run_stages(run, stages)


def _stage_command(stage: str) -> None:
    def cmd(run_id: RunOpt = "latest") -> None:
        run = Run.load(run_id, bus=_bus())
        try:
            stage_fn(stage)
        except StageError as exc:
            console.print(f"[red]error:[/] {exc}")
            raise typer.Exit(2) from exc
        _run_stages(run, [stage])

    cmd.__doc__ = f"Re-run the '{stage}' stage of an existing run from saved outputs."
    app.command(name=stage)(cmd)


for _s in ("baseline", "plan", "migrate", "compare", "capsule"):
    _stage_command(_s)


@app.command()
def scan(
    source: Annotated[str | None, typer.Argument(help="Git URL or local path.")] = None,
    run_id: Annotated[str | None, typer.Option("--run", "-r")] = None,
) -> None:
    """Ingest + scan a repo (no AI) and print the risk table."""
    if run_id:
        run = Run.load(run_id, bus=_bus())
        _run_stages(run, ["scan"])
    elif source:
        run = Run.create(source, {}, bus=_bus())
        _run_stages(run, ["ingest", "scan"])
    else:
        console.print("[red]give a SOURCE or --run[/]")
        raise typer.Exit(2)
    from engine.scan.report import print_risk_table

    print_risk_table(run, console)


@app.command()
def replay(run_id: RunOpt = "latest") -> None:
    """Re-run a previous run's source fully offline from caches (alias for analyze --replay)."""
    prev = Run.load(run_id)
    opts = dict(prev.info.options)
    opts["replay"] = True
    run = Run.create(prev.info.source, opts, bus=_bus())
    _run_stages(run, STAGES)


@app.command()
def doctor(
    no_gemma: Annotated[bool, typer.Option("--no-gemma", help="Skip the Gemma check.")] = False,
) -> None:
    """Check the environment and print fix hints."""
    from engine.doctor import all_required_ok, run_checks

    checks = run_checks(include_gemma=not no_gemma)
    t = Table(title="ExitCapsule doctor")
    t.add_column("")
    t.add_column("check")
    t.add_column("detail")
    t.add_column("fix")
    for c in checks:
        mark = "✅" if c.ok else ("❌" if c.required else "⚠️ ")
        t.add_row(
            mark, c.name + ("" if c.required else " (optional)"), c.detail, "" if c.ok else c.hint
        )
    console.print(t)
    if not all_required_ok(checks):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
