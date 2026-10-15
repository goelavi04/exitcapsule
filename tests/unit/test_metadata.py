from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from engine.config import Settings
from engine.models import DeclaredDep, Dependency, Location
from engine.pipeline import Run
from engine.scan import risk
from engine.scan.metadata import (
    MetadataCache,
    _wheel_support,
    fetch_metadata,
    github_repo_from_urls,
    summarise,
)

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def _files(
    *names: str, when: str = "2015-06-02T00:00:00Z", yanked: bool = False
) -> list[dict[str, Any]]:
    return [{"filename": n, "upload_time_iso_8601": when, "yanked": yanked} for n in names]


def project(
    name: str, latest: str, releases: dict[str, list[dict[str, Any]]], **info: Any
) -> dict[str, Any]:
    return {"info": {"name": name, "version": latest, **info}, "releases": releases}


def dep(name: str, spec: str = "") -> Dependency:
    return Dependency(
        name=name,
        declared=[
            DeclaredDep(
                name=name,
                raw=name + spec,
                specifier=spec,
                location=Location(file="requirements.txt", line=1),
                manifest_kind="requirements",
            )
        ],
    )


def kinds(d: Dependency) -> dict[str, str]:
    return {s.kind: s.severity for s in d.signals}


def test_wheel_support() -> None:
    assert _wheel_support(_files("a-1.0-py2.py3-none-any.whl"), 12) == (True, True)
    assert _wheel_support(_files("a-1.0.tar.gz"), 12) == (None, False)
    assert _wheel_support(_files("a-1.0-cp311-cp311-win_amd64.whl"), 12) == (False, False)
    assert _wheel_support(_files("a-1.0-cp312-cp312-win_amd64.whl"), 12) == (True, False)
    assert _wheel_support(_files("a-1.0-cp38-abi3-manylinux1_x86_64.whl"), 13) == (True, False)


def test_summarise_stale_far_behind_and_signals() -> None:
    body = project(
        "oldlib",
        "3.0.0",
        {
            "1.2.0": _files("oldlib-1.2.0-py3-none-any.whl", when="2016-01-01T00:00:00Z"),
            "3.0.0": _files("oldlib-3.0.0-py3-none-any.whl", when="2019-01-01T00:00:00Z"),
        },
        classifiers=["Development Status :: 7 - Inactive"],
        project_urls={
            "Funding": "https://github.com/sponsors/x",
            "Source": "https://github.com/o/oldlib",
        },
    )
    vbody = {
        "info": {"requires_python": ">=2.7,<3.12", "yanked": True},
        "urls": _files("oldlib-1.2.0-py3-none-any.whl"),
    }
    d = dep("oldlib", "==1.2.0")
    d.metadata = summarise("oldlib", body, vbody, "1.2.0", now=NOW)
    md = d.metadata
    assert md.latest_version == "3.0.0" and md.majors_behind == 2
    assert md.last_release_date == "2019-01-01" and md.days_since_last_release
    assert md.github_repo == "o/oldlib" and md.yanked
    risk.apply(d, risk.metadata_signals(d))
    k = kinds(d)
    assert k["stale"] == "high" and k["inactive_classifier"] == "high"
    assert k["far_behind"] == "high" and k["yanked"] == "high"
    assert k["py312_313_incompatible"] == "high"
    assert d.score == 100 and d.flagged


def test_old_major_pin_and_sdist_only() -> None:
    body = project(
        "lib",
        "5.0",
        {
            "4.9": _files("lib-4.9.tar.gz", when="2026-01-01T00:00:00Z"),
            "5.0": _files("lib-5.0.tar.gz", when="2026-06-01T00:00:00Z"),
        },
    )
    d = dep("lib", "<5")
    d.metadata = summarise("lib", body, None, "4.9", now=NOW)
    risk.apply(d, risk.metadata_signals(d))
    k = kinds(d)
    assert k["old_major_pin"] == "medium"
    assert k["far_behind"] == "medium"
    assert k["py312_313_incompatible"] == "low"  # sdist only
    assert "stale" not in k


def test_github_repo_priority() -> None:
    urls = {
        "Funding": "https://github.com/sponsors/a",
        "Homepage": "https://x.io",
        "Source Code": "https://github.com/b/c.git",
    }
    assert github_repo_from_urls(urls) == "b/c"
    assert github_repo_from_urls({"Docs": "https://x.io"}) is None


@respx.mock
def test_fetch_metadata_async_cache_and_404(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("engine.scan.metadata.github_token", lambda s: None)
    respx.get("https://pypi.org/pypi/good/json").mock(
        return_value=httpx.Response(
            200,
            json=project(
                "good",
                "2.0",
                {
                    "1.0": _files("good-1.0-py3-none-any.whl"),
                    "2.0": _files("good-2.0-py3-none-any.whl", when="2026-09-01T00:00:00Z"),
                },
                project_urls={"Source": "https://github.com/o/good"},
            ),
        )
    )
    respx.get("https://pypi.org/pypi/good/1.0/json").mock(
        return_value=httpx.Response(
            200,
            json={
                "info": {"requires_python": ">=3.8"},
                "urls": _files("good-1.0-py3-none-any.whl"),
            },
        )
    )
    respx.get("https://pypi.org/pypi/missing-pkg/json").mock(
        return_value=httpx.Response(404, json={})
    )
    gh = respx.get("https://api.github.com/repos/o/good").mock(
        return_value=httpx.Response(
            200, json={"archived": True, "pushed_at": "2020-01-01T00:00:00Z"}
        )
    )
    run = Run.create("x", settings=settings)
    deps = [dep("good", "==1.0"), dep("missing-pkg")]
    fetch_metadata(deps, settings, run.evidence)
    good, missing = deps
    assert good.metadata and good.metadata.github_archived is True and good.metadata.evidence
    assert missing.metadata and missing.metadata.found is False
    risk.apply(good, risk.metadata_signals(good))
    risk.apply(missing, risk.metadata_signals(missing))
    assert kinds(good)["archived_upstream"] == "high"
    assert kinds(missing)["not_on_pypi"] == "high"
    calls = gh.call_count
    # second fetch is served from the disk cache
    deps2 = [dep("good", "==1.0")]
    fetch_metadata(deps2, settings, run.evidence)
    assert gh.call_count == calls


@respx.mock
def test_replay_uses_cache_only(settings: Settings) -> None:
    route = respx.get("https://pypi.org/pypi/never/json").mock(return_value=httpx.Response(200))
    run = Run.create("x", settings=settings)
    d = dep("never")
    fetch_metadata([d], settings, run.evidence, replay=True)
    assert route.call_count == 0
    assert d.metadata and d.metadata.error and not risk.metadata_signals(d)


def test_cache_ttl(tmp_path) -> None:  # type: ignore[no-untyped-def]
    c = MetadataCache(tmp_path, ttl_hours=0)
    c.put("u", 200, {"a": 1})
    assert c.get("u") is None  # expired immediately
    assert MetadataCache(tmp_path, ttl_hours=0, replay=True).get("u") is not None


def test_py2_only_wheel_counts_as_source_build() -> None:
    assert _wheel_support(_files("a-1.0-py2-none-any.whl", "a-1.0.tar.gz"), 12) == (None, False)


def test_requires_dist_and_indirect_downgrade() -> None:
    body = project("parent", "1.0", {"1.0": _files("parent-1.0-py3-none-any.whl")})
    vbody = {
        "info": {"requires_dist": ["child>=1", "extra-only; extra == 'x'", "Other_Pkg"]},
        "urls": _files("parent-1.0-py3-none-any.whl"),
    }
    md = summarise("parent", body, vbody, "1.0", now=NOW)
    assert md.requires_dist == ["child", "other-pkg"]
    sig = risk._sig("stale", "high", "old")
    down = risk.indirect([sig], ["parent"])[0]
    assert down.severity == "medium" and "indirect via parent" in down.detail


def test_transitive_pin_is_info_not_unused_low() -> None:
    d = dep("child", "==1.0")
    d.required_by = ["parent"]
    sigs = risk.scan_signals(d, [], is_tool=False, tool_usage=None, guarded_modules=set())
    assert [(s.kind, s.severity) for s in sigs] == [("unused", "info")]
    d2 = dep("lonely", "==1.0")
    sigs2 = risk.scan_signals(d2, [], is_tool=False, tool_usage=None, guarded_modules=set())
    assert [(s.kind, s.severity) for s in sigs2] == [("unused", "low")]
