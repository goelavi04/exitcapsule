from __future__ import annotations

from typer.testing import CliRunner

from engine.cli import app

runner = CliRunner()


def test_help() -> None:
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("analyze", "scan", "baseline", "plan", "migrate", "compare", "capsule", "doctor"):
        assert cmd in r.output


def test_version() -> None:
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0 and "exitcapsule" in r.output


def test_stage_command_without_runs(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    from engine.config import get_settings

    get_settings.cache_clear()
    r = runner.invoke(app, ["plan", "--run", "latest"])
    assert r.exit_code != 0
    get_settings.cache_clear()
