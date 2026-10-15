from __future__ import annotations

import pytest
from pydantic import BaseModel

from engine.ai.fake import FAKE_MODEL_ID, FakeProvider
from engine.ai.provider import ProviderError, is_gemma4, pick_gemma4
from engine.config import Settings
from engine.events import EventBus
from engine.models import ai, executed, observed
from engine.pipeline import Run, StageError


def test_claim_helpers() -> None:
    assert observed(1).source == "observed" and observed(1).verification == "n/a"
    assert executed("x", ["e1"]).evidence == ["e1"]
    c = ai("pkg")
    assert c.source == "ai" and c.verification == "unverified"


def test_event_bus_fanout_and_history() -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append(e.message))
    bus.subscribe(lambda e: 1 / 0)  # broken subscriber must not break emit
    bus.emit("scan", "started", "hi")
    assert seen == ["hi"] and len(bus.history) == 1


def test_evidence_store_roundtrip(settings: Settings) -> None:
    run = Run.create("x", settings=settings)
    eid = run.evidence.save("log", "install/py3.12", "hello")
    assert run.evidence.read_text(eid) == "hello"
    eid2 = run.evidence.save("json", "pypi", {"a": 1})
    assert '"a": 1' in run.evidence.read_text(eid2)
    # index persists across loads
    again = Run.load(run.info.run_id, settings=settings)
    assert eid in again.evidence.index


class _Out(BaseModel):
    n: int


def test_run_stage_persists_and_times(settings: Settings) -> None:
    run = Run.create("src", {"a": 1}, settings=settings)
    run.run_stage("scan", lambda r: _Out(n=3))
    assert run.load_stage("scan", _Out).n == 3
    assert "scan" in run.info.stages_done
    assert Run.load("latest", settings=settings).info.run_id == run.info.run_id
    with pytest.raises(StageError):
        run.load_stage("plan", _Out)


def test_run_stage_failure_recorded(settings: Settings) -> None:
    run = Run.create("src", settings=settings)

    def boom(r: Run) -> BaseModel:
        raise ValueError("nope")

    with pytest.raises(ValueError):
        run.run_stage("scan", boom)
    assert run.info.timings[-1].status == "failed"
    assert run.bus.history[-1].status == "failed"


def test_gemma4_only_selection() -> None:
    assert is_gemma4("gemma-4-31b-it") and is_gemma4("gemma4:e4b")
    assert not is_gemma4("gemini-2.5-flash") and not is_gemma4("gemma-3-27b-it")
    ids = ["gemini-2.5-pro", "gemma-3-27b-it", "gemma-4-26b-a4b-it", "gemma-4-31b-it"]
    assert pick_gemma4(ids) == "gemma-4-31b-it"
    assert pick_gemma4(["gemini-2.5-pro", "llama3.2:latest"]) is None


def test_provider_refuses_non_gemma(settings: Settings) -> None:
    s = settings.model_copy(update={"gemma_model": "gemini-2.5-flash"})
    with pytest.raises(ProviderError):
        FakeProvider(s).resolve_model()
    assert FakeProvider(settings).resolve_model() == FAKE_MODEL_ID


async def test_fake_generate(settings: Settings) -> None:
    p = FakeProvider(settings, responder=lambda s, u: '{"ok": true}')
    out = await p.generate("sys", "user")
    assert out.text == '{"ok": true}' and out.model_id == FAKE_MODEL_ID


def test_settings_ignore_home_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/somewhere/else")
    monkeypatch.setenv("EXITCAPSULE_HOME", "custom")
    from pathlib import Path

    assert Settings(_env_file=None).home == Path("custom")  # type: ignore[call-arg]
