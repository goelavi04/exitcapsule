"""Stage 1 — Ingest.

Why: get an immutable working copy of the target repo (shallow clone or local
copy) and record observed facts about it: commit SHA, default branch, and every
hint about which Python versions it targets.
"""

from __future__ import annotations

import configparser
import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from engine.evidence import EvidenceStore
from engine.fsutil import copytree, iter_files, read_text, rel, rmtree, run
from engine.models import Claim, RepoInfo, observed


class IngestError(RuntimeError):
    pass


def _is_url(source: str) -> bool:
    return bool(re.match(r"^(https?|git|ssh)://", source)) or source.startswith("git@")


def repo_name(source: str) -> str:
    if _is_url(source):
        path = urlparse(source).path if "://" in source else source.split(":", 1)[-1]
        name = path.rstrip("/").split("/")[-1]
    else:
        name = Path(source).resolve().name
    return name.removesuffix(".git") or "repo"


def fetch(source: str, dest: Path, evidence: EvidenceStore, timeout: int = 300) -> None:
    """Shallow-clone a URL or copy a local path into `dest`."""
    rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _is_url(source):
        res = run(
            ["git", "-c", "core.longpaths=true", "clone", "--depth", "1", source, str(dest)],
            timeout=timeout,
        )
        evidence.save("log", "git-clone", res.log(), "git clone output")
        if not res.ok:
            raise IngestError(f"git clone failed: {res.stderr.strip()[-500:]}")
    else:
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            raise IngestError(f"not a directory: {source}")
        copytree(src, dest)


def _git(dest: Path, *args: str) -> str | None:
    if not (dest / ".git").exists():
        return None
    res = run(["git", *args], cwd=dest, timeout=30)
    return res.stdout.strip() if res.ok else None


_PY_VER = re.compile(r"\b3\.(\d{1,2})\b")


def python_hints(root: Path) -> list[Claim]:
    """Collect every place the repo states which Python it targets (all observed)."""
    hints: list[Claim] = []

    def add(value: str, where: str) -> None:
        hints.append(observed(value, note=where))

    pp = root / "pyproject.toml"
    if pp.exists():
        try:
            data = tomllib.loads(read_text(pp))
        except tomllib.TOMLDecodeError:
            data = {}
        proj = data.get("project", {})
        if rp := proj.get("requires-python"):
            add(f"requires-python {rp}", "pyproject.toml [project]")
        for c in proj.get("classifiers", []):
            if c.startswith("Programming Language :: Python :: 3."):
                add(c, "pyproject.toml classifiers")
        poetry_py = data.get("tool", {}).get("poetry", {}).get("dependencies", {}).get("python")
        if isinstance(poetry_py, str):
            add(f"poetry python {poetry_py}", "pyproject.toml [tool.poetry]")
    cfg = root / "setup.cfg"
    if cfg.exists():
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read_string(read_text(cfg))
            if cp.has_option("options", "python_requires"):
                add(f"python_requires {cp.get('options', 'python_requires')}", "setup.cfg")
        except configparser.Error:
            pass
    setup_py = root / "setup.py"
    if setup_py.exists():
        m = re.search(r"python_requires\s*=\s*['\"]([^'\"]+)['\"]", read_text(setup_py))
        if m:
            add(f"python_requires {m.group(1)}", "setup.py")
    for name in (".python-version", "runtime.txt"):
        p = root / name
        if p.exists():
            add(read_text(p).strip().splitlines()[0] if read_text(p).strip() else "", name)
    for p in sorted(root.glob("README*")):
        for m in re.finditer(
            r"python[-_ ]?(?:version)?[-/ :]*(3\.\d{1,2}(?:[ ,|%2C-]+3\.\d+)*)",
            read_text(p),
            re.IGNORECASE,
        ):
            add(f"README mentions python {m.group(1)}", p.name)
            break
    for p in [*root.glob("Dockerfile*"), *root.glob("*/Dockerfile*")]:
        for m in re.finditer(r"^\s*FROM\s+\S*python:(\S+)", read_text(p), re.MULTILINE):
            add(f"Docker FROM python:{m.group(1)}", rel(p, root))
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        for p in sorted(wf.glob("*.y*ml")):
            vers = sorted(set(_PY_VER.findall(read_text(p))), key=int)
            if vers:
                add("CI python " + ", ".join(f"3.{v}" for v in vers), rel(p, root))
    for name in ("tox.ini", "noxfile.py", ".travis.yml", "Makefile"):
        p = root / name
        if p.exists():
            txt = read_text(p)
            vers = sorted(
                set(re.findall(r"py3(\d{1,2})\b", txt)) | set(_PY_VER.findall(txt)), key=int
            )
            if vers:
                add(f"{name} python " + ", ".join(f"3.{v}" for v in vers), name)
    return hints


def ingest(source: str, dest: Path, evidence: EvidenceStore) -> RepoInfo:
    fetch(source, dest, evidence)
    sha = _git(dest, "rev-parse", "HEAD")
    branch = _git(dest, "rev-parse", "--abbrev-ref", "HEAD")
    info = RepoInfo(
        source=source,
        name=repo_name(source),
        local_path=str(dest),
        commit_sha=observed(sha) if sha else None,
        default_branch=observed(branch) if branch else None,
        python_hints=python_hints(dest),
        files_scanned=sum(1 for _ in iter_files(dest)),
    )
    evidence.save("json", "repo-info", info.model_dump(), "ingest summary")
    return info
