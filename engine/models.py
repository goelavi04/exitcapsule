"""Shared data models.

Why: stages communicate *only* through these models (brief §15.1), and every
fact the engine emits carries provenance via `Claim` (brief §2).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Source = Literal["observed", "executed", "ai"]
Verification = Literal["verified", "failed", "unverified", "n/a"]
Severity = Literal["info", "low", "medium", "high"]

SEVERITY_ORDER: dict[str, int] = {"info": 0, "low": 1, "medium": 2, "high": 3}


class Claim(BaseModel):
    value: Any
    source: Source
    verification: Verification
    evidence: list[str] = Field(default_factory=list)
    note: str | None = None


def observed(value: Any, evidence: list[str] | None = None, note: str | None = None) -> Claim:
    return Claim(
        value=value, source="observed", verification="n/a", evidence=evidence or [], note=note
    )


def executed(value: Any, evidence: list[str] | None = None, note: str | None = None) -> Claim:
    return Claim(
        value=value, source="executed", verification="n/a", evidence=evidence or [], note=note
    )


def ai(
    value: Any,
    evidence: list[str] | None = None,
    verification: Verification = "unverified",
    note: str | None = None,
) -> Claim:
    return Claim(
        value=value, source="ai", verification=verification, evidence=evidence or [], note=note
    )


# --------------------------------------------------------------------------- ingest


class RepoInfo(BaseModel):
    source: str  # URL or local path as given
    name: str
    local_path: str  # working copy (never modified by later stages)
    commit_sha: Claim | None = None
    default_branch: Claim | None = None
    python_hints: list[Claim] = Field(default_factory=list)
    files_scanned: int = 0


# --------------------------------------------------------------------------- scan


class Location(BaseModel):
    file: str  # posix path relative to repo root
    line: int


class DeclaredDep(BaseModel):
    name: str  # PEP 503 normalized
    raw: str  # the requirement line / string as written
    specifier: str = ""
    extras: list[str] = Field(default_factory=list)
    marker: str = ""
    group: str = "runtime"  # runtime | dev | optional:<x> | build | test
    location: Location
    manifest_kind: str  # requirements | pyproject | setup.cfg | setup.py | pipfile
    commented_out: bool = False
    url: str | None = None  # VCS / direct references


class TestRunner(BaseModel):
    name: str  # pytest | unittest | nose | tox | ...
    command: str
    location: Location


class ManifestConflict(BaseModel):
    name: str
    specifiers: list[DeclaredDep]
    detail: str


class UsageSite(BaseModel):
    file: str
    line: int
    col: int = 0
    module: str  # top-level import name, e.g. "yaml"
    qualname: str  # full dotted name used, e.g. "yaml.load"
    kind: Literal[
        "import", "call", "attribute", "decorator", "base_class", "config_class", "kwarg", "name"
    ]
    snippet: str = ""
    private: bool = False
    in_tests: bool = False
    covered: bool | None = None  # filled by baseline coverage


class PackageMetadata(BaseModel):
    name: str
    found: bool = True
    latest_version: str | None = None
    installed_or_pinned: str | None = None
    releases: list[str] = Field(default_factory=list)
    last_release_date: str | None = None
    days_since_last_release: int | None = None
    majors_behind: int | None = None
    minors_behind: int | None = None
    yanked: bool = False
    requires_python: str | None = None
    classifiers: list[str] = Field(default_factory=list)
    wheel_312: bool | None = None
    wheel_313: bool | None = None
    universal_wheel: bool | None = None
    project_urls: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None
    github_repo: str | None = None
    github_archived: bool | None = None
    github_pushed_at: str | None = None
    requires_dist: list[str] = Field(default_factory=list)  # normalized names (no extras)
    error: str | None = None  # metadata could not be (fully) fetched
    evidence: list[str] = Field(default_factory=list)


RiskKind = Literal[
    "stale",
    "inactive_classifier",
    "archived_upstream",
    "far_behind",
    "old_major_pin",
    "yanked",
    "py312_313_incompatible",
    "cross_manifest_conflict",
    "private_api_use",
    "undeclared",
    "unused",
    "runtime_deprecation",
    "dead_test_runner",
    "install_failure",
    "build_tool_at_runtime",
    "commented_out",
    "not_on_pypi",
]


class RiskSignal(BaseModel):
    kind: RiskKind
    severity: Severity
    detail: str
    evidence: list[str] = Field(default_factory=list)
    source: Source = "observed"


class Dependency(BaseModel):
    name: str  # normalized distribution name
    import_names: list[str] = Field(default_factory=list)
    declared: list[DeclaredDep] = Field(default_factory=list)
    usage: list[UsageSite] = Field(default_factory=list)
    metadata: PackageMetadata | None = None
    signals: list[RiskSignal] = Field(default_factory=list)
    score: int = 0
    flagged: bool = False
    mapping_note: Claim | None = None
    required_by: list[str] = Field(default_factory=list)  # other declared deps needing it

    @property
    def runtime(self) -> bool:
        return any(d.group == "runtime" and not d.commented_out for d in self.declared)


class ScanResult(BaseModel):
    repo: RepoInfo
    dependencies: list[Dependency] = Field(default_factory=list)
    test_runners: list[TestRunner] = Field(default_factory=list)
    conflicts: list[ManifestConflict] = Field(default_factory=list)
    install_paths: list[str] = Field(default_factory=list)
    local_modules: list[str] = Field(default_factory=list)
    has_tests: bool = False
    limitations: list[str] = Field(default_factory=list)

    def dep(self, name: str) -> Dependency | None:
        return next((d for d in self.dependencies if d.name == name), None)


# --------------------------------------------------------------------------- run


class StageTiming(BaseModel):
    stage: str
    seconds: float
    status: str = "done"


class RunInfo(BaseModel):
    run_id: str
    created_at: str
    source: str
    options: dict[str, Any] = Field(default_factory=dict)
    timings: list[StageTiming] = Field(default_factory=list)
    stages_done: list[str] = Field(default_factory=list)
