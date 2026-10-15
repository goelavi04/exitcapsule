"""Progress event bus.

Why: the CLI (Rich) and the backend (SSE) both need live stage progress from the
same pipeline. Stages publish `ProgressEvent`s here; renderers subscribe.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["started", "progress", "done", "failed", "skipped", "info"]


class ProgressEvent(BaseModel):
    stage: str
    status: Status
    message: str = ""
    pct: float | None = None
    ts: float = Field(default_factory=time.time)


Subscriber = Callable[[ProgressEvent], None]


class EventBus:
    """Thread-safe fan-out of progress events; keeps history for late subscribers."""

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()
        self.history: list[ProgressEvent] = []

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subs.append(fn)

    def emit(
        self, stage: str, status: Status, message: str = "", pct: float | None = None
    ) -> ProgressEvent:
        ev = ProgressEvent(stage=stage, status=status, message=message, pct=pct)
        with self._lock:
            self.history.append(ev)
            subs = list(self._subs)
        for fn in subs:
            # a broken renderer must never break the pipeline
            with contextlib.suppress(Exception):
                fn(ev)
        return ev
