"""Pipeline orchestration.

Why: one place that chains the stages, persists each stage's output to
`.exitcapsule/runs/<run_id>/<stage>.json`, times it and emits progress. The CLI
and the backend both call this, so there is no duplicated logic and any stage
can be re-run alone from the previous stage's saved output.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from engine.config import Settings, get_settings
from engine.events import EventBus
from engine.evidence import EvidenceStore
from engine.models import RunInfo, StageTiming

STAGES: list[str] = ["ingest", "scan", "baseline", "plan", "migrate", "compare", "capsule"]

M = TypeVar("M", bound=BaseModel)


class StageError(RuntimeError):
    pass


class Run:
    """A single analysis run: its directory, evidence store, event bus and options."""

    def __init__(
        self,
        run_dir: Path,
        info: RunInfo,
        settings: Settings | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.dir = run_dir
        self.info = info
        self.settings = settings or get_settings()
        self.bus = bus or EventBus()
        self.evidence = EvidenceStore(run_dir)
        self.cancelled = False

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(
        cls,
        source: str,
        options: dict[str, Any] | None = None,
        settings: Settings | None = None,
        bus: EventBus | None = None,
    ) -> Run:
        settings = settings or get_settings()
        now = datetime.now(UTC)
        run_id = now.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        run_dir = settings.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        info = RunInfo(
            run_id=run_id, created_at=now.isoformat(), source=source, options=options or {}
        )
        run = cls(run_dir, info, settings, bus)
        run.save_info()
        return run

    @classmethod
    def load(
        cls, run_id: str, settings: Settings | None = None, bus: EventBus | None = None
    ) -> Run:
        settings = settings or get_settings()
        run_dir = settings.runs_dir / run_id
        if run_id == "latest":
            runs = sorted(p for p in settings.runs_dir.glob("*") if (p / "run.json").exists())
            if not runs:
                raise StageError("no runs found")
            run_dir = runs[-1]
        path = run_dir / "run.json"
        if not path.exists():
            raise StageError(f"run not found: {run_id}")
        info = RunInfo.model_validate_json(path.read_text(encoding="utf-8"))
        return cls(run_dir, info, settings, bus)

    def save_info(self) -> None:
        (self.dir / "run.json").write_text(self.info.model_dump_json(indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ stage io
    def stage_path(self, stage: str) -> Path:
        return self.dir / f"{stage}.json"

    def save_stage(self, stage: str, model: BaseModel) -> None:
        self.stage_path(stage).write_text(model.model_dump_json(indent=2), encoding="utf-8")

    def load_stage(self, stage: str, model_cls: type[M]) -> M:
        p = self.stage_path(stage)
        if not p.exists():
            raise StageError(f"stage '{stage}' has no saved output in run {self.info.run_id}")
        return model_cls.model_validate_json(p.read_text(encoding="utf-8"))

    def has_stage(self, stage: str) -> bool:
        return self.stage_path(stage).exists()

    def write_json(self, name: str, data: Any) -> Path:
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return p

    # ------------------------------------------------------------------ execution
    def run_stage(self, stage: str, fn: Callable[[Run], BaseModel]) -> BaseModel:
        if self.cancelled:
            raise StageError("run cancelled")
        self.bus.emit(stage, "started", f"{stage} started", 0)
        t0 = time.perf_counter()
        try:
            out = fn(self)
        except Exception as exc:
            dt = time.perf_counter() - t0
            self._record_timing(stage, dt, "failed")
            self.bus.emit(stage, "failed", f"{type(exc).__name__}: {exc}")
            raise
        dt = time.perf_counter() - t0
        self.save_stage(stage, out)
        self._record_timing(stage, dt, "done")
        if stage not in self.info.stages_done:
            self.info.stages_done.append(stage)
        self.save_info()
        self.bus.emit(stage, "done", f"{stage} finished in {dt:.1f}s", 100)
        return out

    def _record_timing(self, stage: str, seconds: float, status: str) -> None:
        self.info.timings = [t for t in self.info.timings if t.stage != stage]
        self.info.timings.append(StageTiming(stage=stage, seconds=round(seconds, 2), status=status))
        self.save_info()


StageFn = Callable[[Run], BaseModel]
_REGISTRY: dict[str, StageFn] = {}


def register(stage: str) -> Callable[[StageFn], StageFn]:
    def deco(fn: StageFn) -> StageFn:
        _REGISTRY[stage] = fn
        return fn

    return deco


def stage_fn(stage: str) -> StageFn:
    _load_stages()
    if stage not in _REGISTRY:
        raise StageError(f"stage '{stage}' is not implemented yet")
    return _REGISTRY[stage]


def _load_stages() -> None:
    """Import stage modules so their @register decorators run."""
    import importlib

    for mod in ("engine.stages",):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as exc:
            if exc.name != mod:
                raise


def run_pipeline(run: Run, stages: list[str] | None = None) -> None:
    """Run the given stages (default: all) in order, skipping ones not implemented."""
    for stage in stages or STAGES:
        run.run_stage(stage, stage_fn(stage))
