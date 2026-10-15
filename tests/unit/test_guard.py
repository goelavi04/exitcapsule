"""Guard: the engine must contain no target-repo-specific answers (brief §1, §11)."""

from __future__ import annotations

from pathlib import Path

ENGINE = Path(__file__).resolve().parents[2] / "engine"

# Names specific to the hackathon target repo. If any appear in engine code the
# engine would be carrying pre-written answers.
FORBIDDEN = ["linkwatch", "rishrek"]


def test_engine_has_no_target_specific_strings() -> None:
    offenders = []
    for p in ENGINE.rglob("*"):
        if p.is_file() and p.suffix in {".py", ".md", ".json", ".j2", ".html", ".txt"}:
            text = p.read_text(encoding="utf-8", errors="replace").lower()
            offenders += [f"{p.name}: {w}" for w in FORBIDDEN if w in text]
    assert not offenders, offenders
