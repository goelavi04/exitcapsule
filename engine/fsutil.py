"""Cross-platform filesystem and subprocess helpers.

Why: the team is on Windows. Git object files are read-only there (plain
`rmtree` fails), paths get long, and console encodings vary. Every stage uses
these helpers instead of ad-hoc `shutil`/`subprocess` calls.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

# Directories never treated as part of the analysed project's source.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        "node_modules",
        "site-packages",
        "build",
        "dist",
        ".eggs",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".exitcapsule",
        ".idea",
        ".vscode",
    }
)

# Env vars that must never leak into target processes (sandbox scrubbing).
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")


def _on_rm_error(func: Any, path: str, exc: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path: Path) -> None:
    """Remove a tree, clearing read-only flags (Windows git objects)."""
    if path.exists():
        shutil.rmtree(path, onexc=_on_rm_error)


def copytree(src: Path, dst: Path, ignore_git: bool = False) -> None:
    names = set(IGNORED_DIRS)
    if not ignore_git:
        names.discard(".git")

    def _ignore(_dir: str, entries: list[str]) -> set[str]:
        return {e for e in entries if e in names}

    shutil.copytree(src, dst, ignore=_ignore, symlinks=False, dirs_exist_ok=True)


def iter_files(root: Path, suffixes: tuple[str, ...] | None = None) -> Iterator[Path]:
    """Walk `root` skipping ignored/hidden tool dirs; yields files in stable order."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in IGNORED_DIRS and not d.endswith(".egg-info") and not d.startswith(".")
        )
        for f in sorted(filenames):
            if suffixes is None or f.endswith(suffixes):
                yield Path(dirpath) / f


def existing(root: Path, names: tuple[str, ...] | list[str]) -> list[Path]:
    """Files in `root` whose real on-disk name is in `names` (case-exact even on Windows)."""
    if not root.is_dir():
        return []
    wanted = set(names)
    return sorted(p for p in root.iterdir() if p.is_file() and p.name in wanted)


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def scrubbed_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy of os.environ without anything that looks like a credential."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(m in k.upper() for m in _SECRET_MARKERS) and not k.upper().startswith("GEMMA_")
    }
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    if extra:
        env.update(extra)
    return env


class ProcResult(BaseModel):
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def log(self) -> str:
        return (
            f"$ {' '.join(self.cmd)}\n# exit={self.returncode} timed_out={self.timed_out} "
            f"seconds={self.seconds:.1f}\n--- stdout ---\n{self.stdout}\n"
            f"--- stderr ---\n{self.stderr}"
        )


def run(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: float = 600,
    env: Mapping[str, str] | None = None,
) -> ProcResult:
    """Run a command (list form, never shell=True) with a hard timeout."""
    t0 = time.perf_counter()
    exe = shutil.which(cmd[0]) or cmd[0]
    try:
        p = subprocess.run(
            [exe, *cmd[1:]],
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            env=dict(env) if env is not None else None,
            encoding="utf-8",
            errors="replace",
        )
        return ProcResult(
            cmd=cmd,
            returncode=p.returncode,
            stdout=p.stdout or "",
            stderr=p.stderr or "",
            seconds=time.perf_counter() - t0,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else ""
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else ""
        return ProcResult(
            cmd=cmd,
            returncode=-1,
            stdout=out,
            stderr=err + f"\n[timeout after {timeout}s]",
            seconds=time.perf_counter() - t0,
            timed_out=True,
        )
    except OSError as exc:
        return ProcResult(
            cmd=cmd, returncode=-2, stdout="", stderr=str(exc), seconds=time.perf_counter() - t0
        )
