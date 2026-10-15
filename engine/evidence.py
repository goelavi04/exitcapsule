"""Evidence store.

Why: every claim must point at raw evidence (PyPI JSON, install logs, junit XML,
Gemma prompts/responses...). Artifacts are saved under
`<run_dir>/evidence/` and referenced by a stable ID.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class EvidenceItem(BaseModel):
    id: str
    kind: str
    path: str  # relative to run dir
    description: str = ""
    sha256: str


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class EvidenceStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.dir = run_dir / "evidence"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.dir / "index.json"
        self._lock = threading.Lock()
        self.index: dict[str, EvidenceItem] = {}
        if self._index_path.exists():
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
            self.index = {k: EvidenceItem.model_validate(v) for k, v in raw.items()}

    def save(self, kind: str, name: str, content: str | bytes | Any, description: str = "") -> str:
        """Persist an artifact and return its evidence ID."""
        if isinstance(content, bytes):
            data = content
        elif isinstance(content, str):
            data = content.encode("utf-8")
        else:
            data = json.dumps(content, indent=2, default=str, ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        safe = _SAFE.sub("_", name)[:80]
        eid = f"{_SAFE.sub('_', kind)}-{safe}-{digest[:8]}"
        rel = Path("evidence") / f"{eid}"
        with self._lock:
            (self.run_dir / rel).write_bytes(data)
            self.index[eid] = EvidenceItem(
                id=eid, kind=kind, path=rel.as_posix(), description=description, sha256=digest
            )
            self._flush()
        return eid

    def path(self, eid: str) -> Path:
        return self.run_dir / self.index[eid].path

    def read_text(self, eid: str) -> str:
        return self.path(eid).read_text(encoding="utf-8", errors="replace")

    def _flush(self) -> None:
        self._index_path.write_text(
            json.dumps({k: v.model_dump() for k, v in self.index.items()}, indent=2),
            encoding="utf-8",
        )
