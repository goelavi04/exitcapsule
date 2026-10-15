from __future__ import annotations

from pathlib import Path

import pytest

from engine.config import Settings
from engine.ingest.ingest import ingest, python_hints, repo_name
from engine.models import RiskSignal, ScanResult
from engine.pipeline import Run
from engine.scan.manifests import (
    _poetry_spec,
    find_test_runners,
    parse_requirements,
    scan_manifests,
)
from engine.scan.mapping import resolve_import
from engine.scan.scan import scan_repo
from engine.scan.usage import scan_usage
from tests.conftest import FIXTURES

REPOS = FIXTURES / "repos"


@pytest.fixture
def legacy(settings: Settings) -> ScanResult:
    run = Run.create(str(REPOS / "legacyapp"), settings=settings)
    repo = ingest(str(REPOS / "legacyapp"), run.dir / "repo", run.evidence)
    return scan_repo(repo, run.evidence)


def sigs(result: ScanResult, name: str) -> dict[str, RiskSignal]:
    dep = result.dep(name)
    assert dep is not None, f"{name} missing: {[d.name for d in result.dependencies]}"
    return {s.kind: s for s in dep.signals}


# --------------------------------------------------------------------------- manifests


def test_requirements_includes_comments_and_groups() -> None:
    root = REPOS / "legacyapp"
    deps = parse_requirements(root / "requirements-dev.txt", root)
    by = {(d.name, d.group, d.commented_out) for d in deps}
    assert ("pytest", "dev", False) in by
    # included runtime file keeps its own role
    assert ("pyyaml", "runtime", False) in by
    assert ("click", "runtime", False) in by
    assert ("flask", "runtime", True) in by  # commented-out pin recorded
    pyyaml = next(d for d in deps if d.name == "pyyaml")
    assert pyyaml.specifier == "==3.13" and pyyaml.location.file == "requirements.txt"
    assert pyyaml.location.line == 2


def test_cross_manifest_conflict_detected() -> None:
    ms = scan_manifests(REPOS / "legacyapp")
    c = {x.name: x for x in ms.conflicts}
    assert "pyyaml" in c and "different exact pins" in c["pyyaml"].detail
    assert "click" not in c  # same specifier everywhere
    assert "requirements:requirements.txt" in ms.install_paths
    assert "project:." in ms.install_paths
    assert ms.scripts == {"legacyapp": "legacyapp.cli:main"}


def test_test_runners() -> None:
    runners = {r.name: r for r in find_test_runners(REPOS / "legacyapp")}
    assert {"pytest", "nose"} <= set(runners)
    assert runners["nose"].location.file == "Makefile"


def test_setup_py_ast_setup_cfg_and_pipfile() -> None:
    ms = scan_manifests(REPOS / "setuppy_app")
    kinds = {(d.name, d.manifest_kind, d.specifier, d.group) for d in ms.deps}
    assert ("docopt", "setup.py", "==0.6.2", "runtime") in kinds
    assert ("python-dateutil", "setup.py", "<2.8", "runtime") in kinds
    assert ("pyyaml", "setup.py", ">=5", "optional:yaml") in kinds
    assert ("pytest", "setup.py", "", "test") in kinds
    assert ("docopt", "setup.cfg", "==0.6.1", "runtime") in kinds
    assert ("docopt", "pipfile", "==0.6.2", "runtime") in kinds
    assert any(c.name == "docopt" for c in ms.conflicts)


def test_poetry() -> None:
    ms = scan_manifests(REPOS / "poetry_app")
    by = {d.name: d for d in ms.deps}
    assert by["requests"].specifier == "<3,>=2.20"
    assert by["attrs"].specifier == "<19.4,>=19.3"
    assert by["toml"].specifier == "==0.10.0"
    assert by["pytest"].group == "dev"
    assert by["sphinx"].group == "dev"
    assert by["poetry-core"].group == "build"
    assert _poetry_spec("x", "^0.4.1") == "x>=0.4.1,<0.5"


# --------------------------------------------------------------------------- usage


def test_usage_sites_kinds() -> None:
    us = scan_usage(REPOS / "legacyapp")
    kinds = {(s.qualname, s.kind) for s in us.sites}
    assert ("yaml.load", "call") in kinds
    assert ("yaml.load(Loader=)", "kwarg") in kinds
    assert ("yaml.FullLoader", "attribute") in kinds
    assert ("requests.Session", "base_class") in kinds
    assert ("click.command", "decorator") in kinds
    assert ("click.option(default=)", "kwarg") in kinds
    assert ("pydantic.BaseModel.Config", "config_class") in kinds
    load = next(s for s in us.sites if s.qualname == "yaml.load")
    assert load.file == "legacyapp/config.py" and load.line == 6
    assert "yaml.load" in load.snippet
    # local package and stdlib are not third-party
    assert "legacyapp" not in us.imported and "json" not in us.imported
    assert "ujson" in us.guarded
    assert us.removed_stdlib == {"imp": ["legacyapp/compat.py:1"]}


def test_private_api_flag() -> None:
    us = scan_usage(REPOS / "legacyapp")
    priv = [s for s in us.sites if s.private]
    assert priv and all(s.module == "requests" for s in priv)
    assert any(s.qualname == "requests._internal_utils.to_native_string" for s in priv)


def test_test_files_marked() -> None:
    us = scan_usage(REPOS / "legacyapp")
    mock_sites = [s for s in us.sites if s.module == "mock"]
    assert mock_sites and all(s.in_tests for s in mock_sites)


# --------------------------------------------------------------------------- mapping


def test_resolve_import() -> None:
    assert resolve_import("yaml", {"pyyaml"})[0] == "pyyaml"
    assert resolve_import("yaml", set())[0] == "pyyaml"  # generic alias table
    assert resolve_import("dateutil", {"python-dateutil"})[0] == "python-dateutil"
    assert resolve_import("foo", set(), {"foo": ["foo-dist"]})[0] == "foo-dist"
    d, how = resolve_import("someunknown", set())
    assert d == "someunknown" and "unverified" in how


# --------------------------------------------------------------------------- full scan


def test_scan_signals_legacyapp(legacy: ScanResult) -> None:
    assert "cross_manifest_conflict" in sigs(legacy, "pyyaml")
    assert sigs(legacy, "pyyaml")["cross_manifest_conflict"].severity == "high"
    assert "private_api_use" in sigs(legacy, "requests")
    und = sigs(legacy, "pydantic")["undeclared"]
    assert und.severity == "medium"
    assert sigs(legacy, "mock")["undeclared"].severity == "low"  # tests only
    assert sigs(legacy, "ujson")["undeclared"].severity == "info"  # guarded import
    assert sigs(legacy, "simplejson")["unused"].severity == "low"
    assert sigs(legacy, "pytest")["unused"].severity == "info"  # tooling
    assert "commented_out" in sigs(legacy, "flask")
    assert "build_tool_at_runtime" in sigs(legacy, "setuptools")
    imp = sigs(legacy, "imp")["py312_313_incompatible"]
    assert imp.severity == "high"
    imp_dep = legacy.dep("imp")
    assert imp_dep and imp_dep.usage and imp_dep.usage[0].file == "legacyapp/compat.py"


def test_flagging_and_score(legacy: ScanResult) -> None:
    flagged = {d.name for d in legacy.dependencies if d.flagged}
    assert {"pyyaml", "requests", "pydantic", "simplejson", "imp", "setuptools"} <= flagged
    assert "ujson" not in flagged and "pytest" not in flagged
    pyyaml = legacy.dep("pyyaml")
    assert pyyaml and pyyaml.score >= 30
    assert pyyaml.usage and pyyaml.import_names == ["yaml"]
    assert legacy.has_tests
    # sorted by score desc
    scores = [d.score for d in legacy.dependencies]
    assert scores == sorted(scores, reverse=True)


def test_no_tests_limitation(settings: Settings) -> None:
    run = Run.create(str(REPOS / "setuppy_app"), settings=settings)
    repo = ingest(str(REPOS / "setuppy_app"), run.dir / "repo", run.evidence)
    res = scan_repo(repo, run.evidence)
    assert not res.has_tests
    assert any("no test files" in lim for lim in res.limitations)
    six = res.dep("six")
    assert six and six.usage and not any(s.kind == "undeclared" for s in six.signals)


# --------------------------------------------------------------------------- ingest


def test_ingest_local_and_hints(settings: Settings) -> None:
    run = Run.create("x", settings=settings)
    info = ingest(str(REPOS / "legacyapp"), run.dir / "repo", run.evidence)
    assert info.name == "legacyapp" and info.files_scanned > 5
    hints = [h.value for h in info.python_hints]
    assert any("requires-python >=3.8" in h for h in hints)
    assert any("tox.ini" in (h.note or "") for h in info.python_hints)
    assert all(h.source == "observed" for h in info.python_hints)


def test_repo_name() -> None:
    assert repo_name("https://github.com/a/b.git") == "b"
    assert repo_name("git@github.com:a/c.git") == "c"


def test_python_hints_empty(tmp_path: Path) -> None:
    assert python_hints(tmp_path) == []


def test_scan_stage_via_pipeline(settings: Settings) -> None:
    from engine.pipeline import run_pipeline

    run = Run.create(str(REPOS / "poetry_app"), settings=settings)
    run_pipeline(run, ["ingest", "scan"])
    res = run.load_stage("scan", ScanResult)
    attrs = res.dep("attrs")
    assert attrs and attrs.import_names == ["attr"] and attrs.usage
    assert any(s.kind == "decorator" for s in attrs.usage)
