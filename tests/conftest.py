from __future__ import annotations

from pathlib import Path

import pytest

from engine.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(home=tmp_path / ".exitcapsule", gemma_provider="fake", _env_file=None)  # type: ignore[call-arg]
