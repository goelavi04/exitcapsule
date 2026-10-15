"""Stage 2d — Package metadata (PyPI + GitHub).

Why: staleness, inactivity, yanked pins, Python support and wheel availability
are facts that live in package indexes, not in the repo. Fetched async and
concurrently, disk-cached with a TTL; in replay mode only the cache is used.
Raw (trimmed) responses are saved as evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from engine.config import Settings
from engine.evidence import EvidenceStore
from engine.models import Dependency, PackageMetadata

PYPI = "https://pypi.org/pypi"
GITHUB_API = "https://api.github.com/repos"
_GH = re.compile(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)")


class MetadataCache:
    """JSON-on-disk cache keyed by URL, with TTL; `replay` ignores TTL and never fetches."""

    def __init__(self, root: Path, ttl_hours: int, replay: bool = False) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_hours * 3600
        self.replay = replay

    def _path(self, url: str) -> Path:
        return self.root / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")

    def get(self, url: str) -> dict[str, Any] | None:
        p = self._path(url)
        if not p.exists():
            return None
        try:
            entry: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not self.replay and time.time() - entry.get("fetched_at", 0) > self.ttl:
            return None
        return entry

    def put(self, url: str, status: int, body: Any) -> None:
        self._path(url).write_text(
            json.dumps({"url": url, "status": status, "body": body, "fetched_at": time.time()}),
            encoding="utf-8",
        )


async def _get_json(
    client: httpx.AsyncClient,
    cache: MetadataCache,
    url: str,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    hit = cache.get(url)
    if hit is not None:
        return int(hit["status"]), hit["body"]
    if cache.replay:
        return -1, None
    for attempt in range(3):
        try:
            r = await client.get(url, headers=headers, timeout=20)
        except httpx.HTTPError:
            if attempt == 2:
                return -1, None
            await asyncio.sleep(0.5 * 2**attempt)
            continue
        if r.status_code in (429, 500, 502, 503) and attempt < 2:
            await asyncio.sleep(0.5 * 2**attempt)
            continue
        body = (
            r.json() if r.headers.get("content-type", "").startswith("application/json") else None
        )
        if r.status_code in (200, 404):
            cache.put(url, r.status_code, body)
        return r.status_code, body
    return -1, None


_URL_PRIORITY = ("source", "repository", "code", "github", "homepage", "home")
_URL_SKIP = ("fund", "sponsor", "donat", "tidelift")


def github_repo_from_urls(urls: dict[str, str]) -> str | None:
    """owner/repo of the upstream GitHub project, preferring source links over funding links."""

    def rank(item: tuple[str, str]) -> int:
        key = item[0].lower()
        return next((i for i, p in enumerate(_URL_PRIORITY) if p in key), len(_URL_PRIORITY))

    for key, url in sorted(urls.items(), key=rank):
        if any(s in key.lower() for s in _URL_SKIP):
            continue
        m = _GH.search(url)
        if m and m.group(1).lower() not in ("sponsors", "orgs"):
            return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
    return None


def github_token(settings: Settings) -> str | None:
    """GITHUB_TOKEN from settings, else the local `gh` CLI login (read-only use)."""
    if settings.github_token and settings.github_token.get_secret_value():
        return settings.github_token.get_secret_value()
    from engine.fsutil import run

    res = run(["gh", "auth", "token"], timeout=10)
    tok = res.stdout.strip()
    return tok if res.ok and tok else None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _wheel_support(files: list[dict[str, Any]], minor: int) -> tuple[bool | None, bool]:
    """(has wheel usable on CPython 3.<minor>, has a pure-python universal wheel).

    The first value is None when there is nothing to choose for Python 3 except
    building the sdist (no wheels, or only pure-python wheels for Python 2).
    """
    wheels = [f["filename"] for f in files if f.get("filename", "").endswith(".whl")]
    universal = False
    usable = False
    binary = False
    for w in wheels:
        try:
            _, _, _, tags = parse_wheel_filename(w)
        except InvalidWheelFilename:
            continue
        for t in tags:
            if t.abi != "none" or t.platform != "any":
                binary = True
            if t.platform == "any" and t.abi == "none" and t.interpreter.startswith("py3"):
                universal = True
            interp = t.interpreter
            if interp == f"cp3{minor}":
                usable = True
            elif t.abi == "abi3" and interp.startswith("cp3") and interp[3:].isdigit():
                usable = usable or int(interp[3:]) <= minor
    if usable or universal:
        return True, universal
    return (False if binary else None), False


def _requires(reqs: list[str] | None) -> list[str]:
    """Unconditional (non-extra) requirement names from Requires-Dist."""
    out = set()
    for r in reqs or []:
        try:
            req = Requirement(r)
        except InvalidRequirement:
            continue
        if req.marker and "extra" in str(req.marker):
            continue
        out.add(str(canonicalize_name(req.name)))
    return sorted(out)


def _best_version(spec: str, releases: list[str]) -> str | None:
    try:
        ss = SpecifierSet(spec or "")
    except InvalidSpecifier:
        return None
    vs = []
    for r in releases:
        try:
            vs.append(Version(r))
        except InvalidVersion:
            continue
    ok = sorted((v for v in vs if ss.contains(v, prereleases=False)), reverse=True)
    return str(ok[0]) if ok else None


def summarise(
    name: str,
    body: dict[str, Any],
    version_body: dict[str, Any] | None,
    target: str | None,
    now: datetime | None = None,
) -> PackageMetadata:
    """Turn PyPI JSON (project + the target version) into PackageMetadata."""
    now = now or datetime.now(UTC)
    info = body.get("info", {}) or {}
    releases: dict[str, list[dict[str, Any]]] = body.get("releases", {}) or {}
    dated: list[tuple[Version, datetime]] = []
    for ver, files in releases.items():
        if not files:
            continue
        try:
            v = Version(ver)
        except InvalidVersion:
            continue
        times = [t for t in (_parse_dt(f.get("upload_time_iso_8601")) for f in files) if t]
        if times:
            dated.append((v, min(times)))
    dated.sort(key=lambda x: x[0])
    stable = [v for v, _ in dated if not v.is_prerelease]
    latest = str(info.get("version") or (stable[-1] if stable else ""))
    last_dt = max((d for _, d in dated), default=None)
    md = PackageMetadata(
        name=name,
        latest_version=latest or None,
        installed_or_pinned=target,
        releases=[str(v) for v, _ in dated][-50:],
        last_release_date=last_dt.date().isoformat() if last_dt else None,
        days_since_last_release=(now - last_dt).days if last_dt else None,
        classifiers=list(info.get("classifiers") or []),
        project_urls={k: v for k, v in (info.get("project_urls") or {}).items() if v},
        summary=info.get("summary"),
        requires_python=info.get("requires_python") or None,
    )
    if info.get("home_page"):
        md.project_urls.setdefault("Homepage", info["home_page"])
    md.github_repo = github_repo_from_urls(md.project_urls)
    if target and latest:
        try:
            tv, lv = Version(target), Version(latest)
            md.majors_behind = max(0, lv.major - tv.major)
            md.minors_behind = max(0, lv.minor - tv.minor) if lv.major == tv.major else None
        except InvalidVersion:
            pass
    files = (version_body or {}).get("urls") or releases.get(target or latest, []) or []
    if version_body:
        vinfo = version_body.get("info", {}) or {}
        md.requires_python = vinfo.get("requires_python") or None
        md.yanked = bool(vinfo.get("yanked"))
    elif files:
        md.yanked = all(f.get("yanked") for f in files)
    md.requires_dist = _requires(
        ((version_body or {}).get("info") or {}).get("requires_dist") or info.get("requires_dist")
    )
    md.wheel_312, md.universal_wheel = _wheel_support(files, 12)
    md.wheel_313, _ = _wheel_support(files, 13)
    return md


def target_version(dep: Dependency, releases: list[str]) -> str | None:
    """The version the repo actually asks for: an exact pin, else the best match."""
    from engine.scan.risk import _pinned_version

    pinned = _pinned_version(dep)
    if pinned is not None:
        return str(pinned)
    specs = [d.specifier for d in dep.declared if not d.commented_out and d.specifier]
    return _best_version(",".join(specs), releases) if specs else None


async def _fetch_one(
    dep: Dependency,
    client: httpx.AsyncClient,
    cache: MetadataCache,
    evidence: EvidenceStore,
    gh_headers: dict[str, str],
    sem: asyncio.Semaphore,
    use_github: bool,
) -> None:
    async with sem:
        status, body = await _get_json(client, cache, f"{PYPI}/{dep.name}/json")
        if status == 404:
            eid = evidence.save("pypi", dep.name, {"status": 404}, f"PyPI lookup for {dep.name}")
            dep.metadata = PackageMetadata(name=dep.name, found=False, evidence=[eid])
            return
        if status != 200 or not isinstance(body, dict):
            dep.metadata = PackageMetadata(
                name=dep.name, found=True, error=f"PyPI unavailable (status {status})"
            )
            return
        releases = [v for v, files in (body.get("releases") or {}).items() if files]
        target = target_version(dep, releases)
        vbody = None
        if target:
            vs, vb = await _get_json(client, cache, f"{PYPI}/{dep.name}/{target}/json")
            vbody = vb if vs == 200 and isinstance(vb, dict) else None
        md = summarise(dep.name, body, vbody, target)
        trimmed = {
            "info": {
                k: body.get("info", {}).get(k)
                for k in (
                    "name",
                    "version",
                    "summary",
                    "requires_python",
                    "classifiers",
                    "project_urls",
                    "home_page",
                    "yanked",
                )
            },
            "release_count": len(releases),
            "target_version": target,
            "target_files": [f.get("filename") for f in (vbody or {}).get("urls", [])],
        }
        md.evidence.append(
            evidence.save("pypi", dep.name, trimmed, f"PyPI metadata for {dep.name}")
        )
        if use_github and md.github_repo:
            gs, gb = await _get_json(client, cache, f"{GITHUB_API}/{md.github_repo}", gh_headers)
            if gs == 200 and isinstance(gb, dict):
                md.github_archived = bool(gb.get("archived"))
                md.github_pushed_at = gb.get("pushed_at")
                md.evidence.append(
                    evidence.save(
                        "github",
                        md.github_repo,
                        {k: gb.get(k) for k in ("full_name", "archived", "pushed_at", "disabled")},
                        f"GitHub repo metadata for {md.github_repo}",
                    )
                )
            elif gs in (403, 429):
                md.error = "GitHub rate-limited; archived flag unknown"
        dep.metadata = md


def fetch_metadata(
    deps: list[Dependency],
    settings: Settings,
    evidence: EvidenceStore,
    replay: bool = False,
    use_github: bool = True,
) -> None:
    """Populate `dep.metadata` for every dependency (in place)."""
    cache = MetadataCache(settings.cache_dir / "metadata", settings.metadata_ttl_hours, replay)
    headers = {"Accept": "application/vnd.github+json"}
    token = github_token(settings) if use_github and not replay else None
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async def main() -> None:
        sem = asyncio.Semaphore(12)
        async with httpx.AsyncClient(headers={"User-Agent": "exitcapsule"}) as client:
            await asyncio.gather(
                *(_fetch_one(d, client, cache, evidence, headers, sem, use_github) for d in deps)
            )

    asyncio.run(main())
