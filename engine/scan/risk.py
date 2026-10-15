"""Stage 2e — Deterministic risk signals and scoring.

Why: "at-risk" must be explainable and reproducible, so it is computed by
documented rules from observed facts only — never by the model. Gemma's
severity opinion is shown *next to* this score, never instead of it.

Score = min(100, Σ weight(severity) over signals), weights below. A dependency
is flagged (a migration candidate) if it has at least one signal ≥ low.
The formula is mirrored in docs/CAPSULE_SPEC.md.
"""

from __future__ import annotations

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from engine.models import SEVERITY_ORDER, Dependency, ManifestConflict, RiskSignal, Severity

WEIGHTS: dict[str, int] = {"info": 0, "low": 5, "medium": 15, "high": 30}

# Thresholds (documented in CAPSULE_SPEC.md)
STALE_LOW_DAYS = 365 * 2
STALE_MEDIUM_DAYS = 365 * 3
STALE_HIGH_DAYS = 365 * 5
FAR_BEHIND_MINORS = 5
TARGET_PYTHONS = ("3.12", "3.13")

BUILD_TOOL_MODULES = frozenset({"pkg_resources", "setuptools", "distutils", "pip", "wheel"})


def score(signals: list[RiskSignal]) -> int:
    return min(100, sum(WEIGHTS[s.severity] for s in signals))


def is_flagged(signals: list[RiskSignal]) -> bool:
    return any(SEVERITY_ORDER[s.severity] >= SEVERITY_ORDER["low"] for s in signals)


def _sig(
    kind: str, severity: Severity, detail: str, evidence: list[str] | None = None
) -> RiskSignal:
    return RiskSignal(kind=kind, severity=severity, detail=detail, evidence=evidence or [])


# --------------------------------------------------------------------------- scan-derived


def scan_signals(
    dep: Dependency,
    conflicts: list[ManifestConflict],
    is_tool: bool,
    tool_usage: str | None,
    guarded_modules: set[str],
) -> list[RiskSignal]:
    out: list[RiskSignal] = []
    active = [d for d in dep.declared if not d.commented_out and d.group != "constraint"]
    code_sites = [u for u in dep.usage if not u.in_tests]

    for c in conflicts:
        if c.name == dep.name:
            sev: Severity = "high" if c.detail.startswith("different exact pins") else "medium"
            out.append(_sig("cross_manifest_conflict", sev, c.detail))

    private = [u for u in dep.usage if u.private]
    if private:
        locs = ", ".join(sorted({f"{u.file}:{u.line}" for u in private})[:5])
        out.append(
            _sig(
                "private_api_use",
                "medium",
                f"{len(private)} use(s) of private API ({private[0].qualname}) at {locs}",
            )
        )

    if dep.usage and not active:
        if all(m in guarded_modules for m in dep.import_names):
            sev = "info"
            why = "imported only inside try/except ImportError (optional)"
        elif code_sites:
            sev = "medium"
            why = "imported by application code"
        else:
            sev = "low"
            why = "imported only by tests"
        first = (code_sites or dep.usage)[0]
        out.append(
            _sig(
                "undeclared",
                sev,
                f"'{', '.join(dep.import_names)}' {why} but not declared in any manifest "
                f"(first at {first.file}:{first.line})",
            )
        )

    if active and not dep.usage:
        runtime = any(d.group in ("runtime",) or d.group.startswith("optional:") for d in active)
        if is_tool or tool_usage or not runtime:
            detail = "declared but never imported; " + (
                f"invoked as a tool at {tool_usage}"
                if tool_usage
                else "tooling/dev dependency (import not expected)"
            )
            out.append(_sig("unused", "info", detail))
        else:
            loc = active[0].location
            out.append(
                _sig(
                    "unused",
                    "low",
                    f"declared at {loc.file}:{loc.line} but never imported or invoked",
                )
            )

    commented = [d for d in dep.declared if d.commented_out]
    if commented:
        c0 = commented[0]
        out.append(
            _sig(
                "commented_out",
                "info",
                f"commented-out declaration '{c0.raw}' at {c0.location.file}:{c0.location.line}",
            )
        )

    bt = [
        u for u in code_sites if u.module in BUILD_TOOL_MODULES and not u.file.endswith("setup.py")
    ]
    if bt:
        out.append(
            _sig(
                "build_tool_at_runtime",
                "medium",
                f"build-tool API '{bt[0].qualname}' used in application code at "
                f"{bt[0].file}:{bt[0].line}",
            )
        )
    return out


def removed_stdlib_signal(module: str, removed_in: str, locations: list[str]) -> RiskSignal:
    return _sig(
        "py312_313_incompatible",
        "high" if removed_in == "3.12" else "medium",
        f"stdlib module '{module}' was removed in Python {removed_in}; imported at "
        + ", ".join(locations[:5]),
    )


# --------------------------------------------------------------------------- metadata-derived


def _pinned_version(dep: Dependency) -> Version | None:
    for d in dep.declared:
        if d.commented_out:
            continue
        for spec in SpecifierSet(d.specifier or ""):
            if spec.operator in ("==", "==="):
                try:
                    return Version(spec.version.replace(".*", ""))
                except InvalidVersion:
                    continue
    return None


def _upper_major_cap(dep: Dependency) -> int | None:
    caps = []
    for d in dep.declared:
        if d.commented_out:
            continue
        for spec in SpecifierSet(d.specifier or ""):
            try:
                v = Version(spec.version.replace(".*", ""))
            except InvalidVersion:
                continue
            if spec.operator == "<":
                caps.append(v.major if (v.minor or v.micro) else v.major - 1)
            elif spec.operator in ("<=", "~="):
                caps.append(v.major)
    return min(caps) if caps else None


def metadata_signals(dep: Dependency) -> list[RiskSignal]:
    md = dep.metadata
    if md is None:
        return []
    ev = list(md.evidence)
    out: list[RiskSignal] = []
    if not md.found:
        out.append(_sig("not_on_pypi", "high", f"'{dep.name}' was not found on PyPI", ev))
        return out
    days = md.days_since_last_release
    if days is not None and days >= STALE_LOW_DAYS:
        sev: Severity = (
            "high" if days >= STALE_HIGH_DAYS else "medium" if days >= STALE_MEDIUM_DAYS else "low"
        )
        out.append(_sig("stale", sev, f"last release {md.last_release_date} ({days} days ago)", ev))
    inactive = [c for c in md.classifiers if c.startswith("Development Status :: 7")]
    if inactive:
        out.append(_sig("inactive_classifier", "high", f"classifier '{inactive[0]}'", ev))
    if md.github_archived:
        out.append(
            _sig("archived_upstream", "high", f"GitHub repo {md.github_repo} is archived", ev)
        )
    if md.majors_behind and md.majors_behind >= 1:
        out.append(
            _sig(
                "far_behind",
                "high" if md.majors_behind >= 2 else "medium",
                f"pinned {md.installed_or_pinned} is {md.majors_behind} major version(s) behind "
                f"latest {md.latest_version}",
                ev,
            )
        )
    elif md.minors_behind and md.minors_behind >= FAR_BEHIND_MINORS:
        out.append(
            _sig(
                "far_behind",
                "low",
                f"pinned {md.installed_or_pinned} is {md.minors_behind} minor versions behind "
                f"latest {md.latest_version}",
                ev,
            )
        )
    cap = _upper_major_cap(dep)
    if cap is not None and md.latest_version:
        try:
            latest_major = Version(md.latest_version).major
        except InvalidVersion:
            latest_major = None
        if latest_major is not None and cap < latest_major:
            out.append(
                _sig(
                    "old_major_pin",
                    "medium",
                    f"specifier caps at major {cap} while latest is {md.latest_version}",
                    ev,
                )
            )
    if md.yanked:
        out.append(_sig("yanked", "high", f"pinned version {md.installed_or_pinned} is yanked", ev))
    if md.requires_python:
        try:
            spec = SpecifierSet(md.requires_python)
            bad = [p for p in TARGET_PYTHONS if not spec.contains(Version(p + ".0"))]
        except Exception:
            bad = []
        if bad:
            out.append(
                _sig(
                    "py312_313_incompatible",
                    "high",
                    f"requires_python '{md.requires_python}' excludes Python {', '.join(bad)}",
                    ev,
                )
            )
    if md.wheel_312 is False and md.universal_wheel is False:
        out.append(
            _sig(
                "py312_313_incompatible",
                "medium",
                f"no cp312 or universal wheel for {md.installed_or_pinned or md.latest_version}; "
                "would build from source",
                ev,
            )
        )
    return out


def apply(dep: Dependency, extra: list[RiskSignal]) -> None:
    """Merge signals into a dependency (dedup by kind+detail) and recompute score/flag."""
    seen = {(s.kind, s.detail) for s in dep.signals}
    for s in extra:
        if (s.kind, s.detail) not in seen:
            dep.signals.append(s)
            seen.add((s.kind, s.detail))
    dep.signals.sort(key=lambda s: -SEVERITY_ORDER[s.severity])
    dep.score = score(dep.signals)
    dep.flagged = is_flagged(dep.signals)


__all__ = ["WEIGHTS", "_pinned_version", "apply", "metadata_signals", "scan_signals", "score"]
