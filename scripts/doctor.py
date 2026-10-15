"""Standalone doctor: `uv run python scripts/doctor.py` (same as `exitcapsule doctor`)."""

from engine.cli import app

if __name__ == "__main__":
    app(["doctor"])
