"""Rich risk table for `exitcapsule scan`.

Why: the scan command's human-facing output. Every row is observed data.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from engine.models import ScanResult
from engine.pipeline import Run


def print_risk_table(run: Run, console: Console) -> None:
    if not run.has_stage("scan"):
        console.print("[yellow]no scan output in this run[/]")
        return
    scan = run.load_stage("scan", ScanResult)
    t = Table(title=f"Dependency risk — {scan.repo.name} [OBSERVED]")
    for col in ("dependency", "declared", "latest", "score", "signals", "usage sites", "flagged"):
        t.add_column(col)
    for d in sorted(scan.dependencies, key=lambda d: -d.score):
        declared = ", ".join(sorted({x.specifier or "*" for x in d.declared})) or "(undeclared)"
        latest = d.metadata.latest_version if d.metadata else None
        sigs = ", ".join(f"{s.kind}:{s.severity}" for s in d.signals)
        t.add_row(
            d.name,
            declared,
            latest or "?",
            str(d.score),
            sigs,
            str(len(d.usage)),
            "yes" if d.flagged else "",
        )
    console.print(t)
