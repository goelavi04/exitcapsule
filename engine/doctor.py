"""Environment doctor.

Why: a hackathon team on Windows needs one command that says exactly what is
missing and how to fix it. Used by `exitcapsule doctor`, `scripts/doctor.py`
and `GET /api/health`.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path

import httpx
from pydantic import BaseModel

from engine.config import Settings, get_settings


class Check(BaseModel):
    name: str
    ok: bool
    required: bool = True
    detail: str = ""
    hint: str = ""


def _cmd_version(cmd: list[str], timeout: int = 15) -> str | None:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, *cmd[1:]], capture_output=True, text=True, timeout=timeout, encoding="utf-8"
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""


def check_tools() -> list[Check]:
    tools = [
        ("git", ["git", "--version"], True, "winget install Git.Git"),
        ("gh", ["gh", "--version"], False, "winget install GitHub.cli"),
        ("uv", ["uv", "--version"], True, "irm https://astral.sh/uv/install.ps1 | iex"),
        ("node", ["node", "-v"], False, "winget install OpenJS.NodeJS.LTS (needed for the UI)"),
    ]
    out = []
    for name, cmd, req, hint in tools:
        v = _cmd_version(cmd)
        out.append(Check(name=name, ok=v is not None, required=req, detail=v or "", hint=hint))
    return out


def check_pythons() -> list[Check]:
    out = []
    for ver in ("3.12", "3.13"):
        found = _cmd_version(["uv", "python", "find", ver])
        out.append(
            Check(
                name=f"python {ver}",
                ok=found is not None,
                required=ver == "3.12",
                detail=found or "",
                hint=f"uv python install {ver}",
            )
        )
    return out


def check_deps() -> Check:
    mods = ["typer", "rich", "pydantic", "httpx", "packaging", "jinja2", "jsonschema", "fastapi"]
    missing = []
    for m in mods:
        try:
            importlib.import_module(m)
        except ImportError:
            missing.append(m)
    return Check(
        name="python deps importable",
        ok=not missing,
        detail="missing: " + ", ".join(missing) if missing else "ok",
        hint="uv sync",
    )


def check_env_file() -> Check:
    p = Path(".env")
    return Check(
        name=".env present",
        ok=p.exists(),
        detail=str(p.resolve()) if p.exists() else "",
        hint="copy .env.example to .env and fill GEMINI_API_KEY",
    )


def check_gemma(settings: Settings) -> Check:
    from engine.ai.provider import ProviderError, make_provider

    try:
        prov = make_provider(settings)
        model_id = prov.resolve_model()
    except ProviderError as exc:
        return Check(name="gemma model reachable", ok=False, detail=str(exc), hint=exc.hint)
    except Exception as exc:
        return Check(
            name="gemma model reachable",
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            hint="check GEMINI_API_KEY / network",
        )
    return Check(name="gemma model reachable", ok=True, detail=f"{prov.name}: {model_id}")


def check_pypi() -> Check:
    try:
        r = httpx.get("https://pypi.org/pypi/pip/json", timeout=10)
        ok = r.status_code == 200
        detail = f"HTTP {r.status_code}"
    except httpx.HTTPError as exc:
        ok, detail = False, str(exc)
    return Check(name="PyPI reachable", ok=ok, detail=detail, hint="check network / proxy")


def check_docker() -> Check:
    v = _cmd_version(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
    return Check(
        name="docker (optional sandbox)",
        ok=v is not None,
        required=False,
        detail=f"server {v}" if v else "not running",
        hint="start Docker Desktop, or use --sandbox local (default)",
    )


def run_checks(settings: Settings | None = None, include_gemma: bool = True) -> list[Check]:
    settings = settings or get_settings()
    checks = [*check_tools(), *check_pythons(), check_deps(), check_env_file()]
    if include_gemma:
        checks.append(check_gemma(settings))
    checks += [check_pypi(), check_docker()]
    return checks


def all_required_ok(checks: list[Check]) -> bool:
    return all(c.ok for c in checks if c.required)
