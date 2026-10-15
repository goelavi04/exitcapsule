from __future__ import annotations

import httpx
import pytest
import respx

from engine import doctor
from engine.config import Settings


@respx.mock
def test_run_checks_offline_parts(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get("https://pypi.org/pypi/pip/json").mock(return_value=httpx.Response(200, json={}))
    monkeypatch.setattr(doctor, "_cmd_version", lambda cmd, timeout=15: "v1")
    checks = doctor.run_checks(settings)
    names = {c.name: c for c in checks}
    assert names["PyPI reachable"].ok
    assert names["python deps importable"].ok
    # fake provider resolves a Gemma 4 id
    assert names["gemma model reachable"].ok
    assert "gemma-4" in names["gemma model reachable"].detail


@respx.mock
def test_failures_have_hints(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get("https://pypi.org/pypi/pip/json").mock(side_effect=httpx.ConnectError("down"))
    monkeypatch.setattr(doctor, "_cmd_version", lambda cmd, timeout=15: None)
    s = settings.model_copy(update={"gemma_provider": "google", "gemini_api_key": None})
    checks = doctor.run_checks(s)
    failed = [c for c in checks if not c.ok]
    assert failed and all(c.hint for c in failed)
    assert not doctor.all_required_ok(checks)


def test_cmd_version_missing_tool() -> None:
    assert doctor._cmd_version(["definitely-not-a-real-tool-xyz"]) is None
    assert doctor._cmd_version(["git", "--version"]) is not None
