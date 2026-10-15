"""Stage registry.

Why: importing this module registers every implemented stage with the pipeline
(`engine.pipeline.register`). Each stage reads the previous stage's saved
output from the run directory, so any stage can be re-run alone.
"""

from __future__ import annotations

from engine.ingest.ingest import ingest
from engine.models import Dependency, RepoInfo, ScanResult
from engine.pipeline import Run, register
from engine.scan.metadata import fetch_metadata
from engine.scan.scan import scan_repo


@register("ingest")
def ingest_stage(run: Run) -> RepoInfo:
    run.bus.emit("ingest", "progress", f"fetching {run.info.source}", 10)
    return ingest(run.info.source, run.dir / "repo", run.evidence)


@register("scan")
def scan_stage(run: Run) -> ScanResult:
    repo = run.load_stage("ingest", RepoInfo)
    replay = bool(run.info.options.get("replay"))

    def hook(deps: list[Dependency]) -> None:
        run.bus.emit("scan", "progress", f"fetching metadata for {len(deps)} packages", 40)
        fetch_metadata(deps, run.settings, run.evidence, replay=replay)

    result = scan_repo(repo, run.evidence, metadata_hook=hook)
    flagged = sum(d.flagged for d in result.dependencies)
    run.bus.emit("scan", "info", f"{len(result.dependencies)} dependencies, {flagged} flagged")
    return result
