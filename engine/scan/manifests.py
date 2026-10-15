"""Stage 2a — Manifest parsing.

Why: the declared dependency surface is spread across many formats
(requirements*.txt with includes, PEP 621, PEP 735 groups, Poetry, setup.cfg,
setup.py, Pipfile). Every declaration is recorded with file:line so conflicts,
commented-out pins and runtime-vs-dev roles are observed facts. setup.py is
parsed with `ast` and never executed.
"""

from __future__ import annotations

import ast
import configparser
import re
import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from engine.fsutil import existing, iter_files, read_text, rel
from engine.models import DeclaredDep, Location, ManifestConflict, TestRunner

_DEV_HINT = re.compile(r"(dev|test|lint|doc|ci|check|type|style|bench)", re.IGNORECASE)


def group_for(name: str) -> str:
    """Classify a requirements file or extra/group name as runtime or dev/test."""
    n = name.lower()
    if re.search(r"test", n):
        return "test"
    if _DEV_HINT.search(n):
        return "dev"
    return "runtime"


def _line_of(text: str, needle: str, start: int = 1) -> int:
    """Best-effort 1-based line number of `needle` in `text`."""
    for i, line in enumerate(text.splitlines()[start - 1 :], start=start):
        if needle in line:
            return i
    return start


def parse_req_string(
    raw: str,
    file: str,
    line: int,
    kind: str,
    group: str,
    commented: bool = False,
) -> DeclaredDep | None:
    raw = raw.strip()
    if not raw:
        return None
    url = None
    if raw.startswith(("-e ", "--editable ")):
        raw = raw.split(None, 1)[1].strip()
    m = re.search(r"[#&]egg=([A-Za-z0-9_.\-]+)", raw)
    if re.match(r"^(git\+|hg\+|svn\+|https?://|file:)", raw) and m:
        url = raw
        raw = m.group(1)
    try:
        req = Requirement(raw)
    except InvalidRequirement:
        return None
    if req.url:
        url = req.url
    return DeclaredDep(
        name=canonicalize_name(req.name),
        raw=raw if url is None else url,
        specifier=str(req.specifier),
        extras=sorted(req.extras),
        marker=str(req.marker) if req.marker else "",
        group=group,
        location=Location(file=file, line=line),
        manifest_kind=kind,
        commented_out=commented,
        url=url,
    )


# --------------------------------------------------------------------------- requirements


def _req_files(root: Path) -> list[Path]:
    out = []
    for p in iter_files(root, (".txt", ".in")):
        name = p.name.lower()
        if "requirement" in name or p.parent.name.lower() in {"requirements", "reqs"}:
            out.append(p)
    return out


def parse_requirements(
    path: Path, root: Path, group: str | None = None, seen: set[Path] | None = None
) -> list[DeclaredDep]:
    """Parse a requirements file, following -r/-c includes (cycle-safe)."""
    seen = seen if seen is not None else set()
    path = path.resolve()
    if path in seen or not path.exists():
        return []
    seen.add(path)
    file = rel(path, root.resolve())
    grp = group or group_for(path.stem if path.stem.lower() != "requirements" else "")
    out: list[DeclaredDep] = []
    logical = ""
    start_line = 0
    for lineno, line in enumerate(read_text(path).splitlines(), start=1):
        if not logical:
            start_line = lineno
        if line.rstrip().endswith("\\"):
            logical += line.rstrip()[:-1] + " "
            continue
        logical += line
        text, logical = logical.strip(), ""
        if not text:
            continue
        if text.startswith("#"):
            body = text.lstrip("#").strip()
            # A commented-out requirement looks exactly like a requirement line.
            if re.match(
                r"^[A-Za-z0-9][A-Za-z0-9_.\-\[\], ]*\s*(==|>=|<=|~=|!=|>|<)\s*[\w.*]+", body
            ):
                d = parse_req_string(
                    body.split(" #")[0], file, start_line, "requirements", grp, commented=True
                )
                if d:
                    out.append(d)
            continue
        text = re.split(r"\s+#", text, maxsplit=1)[0].strip()
        m = re.match(r"^(-r|--requirement|-c|--constraint)\s*=?\s*(\S+)", text)
        if m:
            inc = (path.parent / m.group(2)).resolve()
            # An included file keeps its own role (requirements-dev.txt including
            # requirements.txt must not turn runtime deps into dev deps).
            sub_group = None if m.group(1) in ("-r", "--requirement") else "constraint"
            out += parse_requirements(inc, root, sub_group, seen)
            continue
        if text.startswith("-") and not text.startswith(("-e", "--editable")):
            continue  # --index-url, --hash-only lines etc.
        if text.startswith(("-e .", "-e ./", ".", "./")):
            continue  # the project itself
        d = parse_req_string(text, file, start_line, "requirements", grp)
        if d:
            out.append(d)
    return out


# --------------------------------------------------------------------------- pyproject


def _poetry_spec(name: str, spec: Any) -> str:
    """Convert a Poetry constraint to a PEP 508 string (approximate, documented)."""
    if isinstance(spec, dict):
        if "git" in spec or "path" in spec or "url" in spec:
            return name
        spec = spec.get("version", "*")
    if not isinstance(spec, str) or spec.strip() in ("*", ""):
        return name
    s = spec.strip()
    if s.startswith("^"):
        v = s[1:]
        parts = v.split(".")
        major = int(parts[0]) if parts[0].isdigit() else 0
        if major == 0 and len(parts) > 1 and parts[1].isdigit():
            return f"{name}>={v},<0.{int(parts[1]) + 1}"
        return f"{name}>={v},<{major + 1}"
    if s.startswith("~") and not s.startswith("~="):
        v = s[1:]
        parts = v.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{name}>={v},<{parts[0]}.{int(parts[1]) + 1}"
        return f"{name}>={v}"
    if re.match(r"^\d", s):
        return f"{name}=={s}"
    return f"{name}{s}"


def parse_pyproject(path: Path, root: Path) -> tuple[list[DeclaredDep], dict[str, Any]]:
    file = rel(path, root)
    text = read_text(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return [], {}
    out: list[DeclaredDep] = []

    def add(raw: str, group: str) -> None:
        d = parse_req_string(
            raw, file, _line_of(text, raw.split(";")[0].strip()), "pyproject", group
        )
        if d:
            out.append(d)

    proj = data.get("project", {})
    for r in proj.get("dependencies", []) or []:
        add(r, "runtime")
    for extra, reqs in (proj.get("optional-dependencies", {}) or {}).items():
        g = group_for(extra)
        for r in reqs:
            add(r, g if g != "runtime" else f"optional:{extra}")
    for gname, reqs in (data.get("dependency-groups", {}) or {}).items():
        g = group_for(gname)
        for r in reqs:
            if isinstance(r, str):
                add(r, g if g != "runtime" else "dev")
    for r in data.get("build-system", {}).get("requires", []) or []:
        add(r, "build")
    poetry = data.get("tool", {}).get("poetry", {})
    for name, spec in (poetry.get("dependencies", {}) or {}).items():
        if name.lower() != "python":
            add(_poetry_spec(name, spec), "runtime")
    for name, spec in (poetry.get("dev-dependencies", {}) or {}).items():
        add(_poetry_spec(name, spec), "dev")
    for gname, g in (poetry.get("group", {}) or {}).items():
        for name, spec in (g.get("dependencies", {}) or {}).items():
            add(
                _poetry_spec(name, spec),
                group_for(gname) if group_for(gname) != "runtime" else "dev",
            )
    return out, data


# --------------------------------------------------------------------------- setup.cfg / setup.py


def parse_setup_cfg(path: Path, root: Path) -> list[DeclaredDep]:
    file = rel(path, root)
    text = read_text(path)
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(text)
    except configparser.Error:
        return []
    out: list[DeclaredDep] = []

    def add_block(value: str, group: str) -> None:
        for raw in value.splitlines():
            raw = raw.split("#")[0].strip()
            if raw:
                d = parse_req_string(raw, file, _line_of(text, raw), "setup.cfg", group)
                if d:
                    out.append(d)

    if cp.has_section("options"):
        for key, group in (
            ("install_requires", "runtime"),
            ("tests_require", "test"),
            ("setup_requires", "build"),
        ):
            if cp.has_option("options", key):
                add_block(cp.get("options", key), group)
    if cp.has_section("options.extras_require"):
        for extra, value in cp.items("options.extras_require"):
            g = group_for(extra)
            add_block(value, g if g != "runtime" else f"optional:{extra}")
    return out


def _literal_strings(node: ast.AST, names: dict[str, ast.AST]) -> list[tuple[str, int]]:
    """Extract string constants from a list/tuple literal (following simple names)."""
    if isinstance(node, ast.Name) and node.id in names:
        node = names[node.id]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [(node.value, node.lineno)]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        out = []
        for elt in node.elts:
            out += _literal_strings(elt, names)
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_strings(node.left, names) + _literal_strings(node.right, names)
    return []


def parse_setup_py(path: Path, root: Path) -> list[DeclaredDep]:
    file = rel(path, root)
    try:
        tree = ast.parse(read_text(path))
    except SyntaxError:
        return []
    names: dict[str, ast.AST] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            t = stmt.targets[0]
            if isinstance(t, ast.Name):
                names[t.id] = stmt.value
    out: list[DeclaredDep] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if fname != "setup":
            continue
        for kw in node.keywords:
            if kw.arg in ("install_requires", "tests_require", "setup_requires"):
                group = {"install_requires": "runtime", "tests_require": "test"}.get(
                    kw.arg, "build"
                )
                for raw, line in _literal_strings(kw.value, names):
                    d = parse_req_string(raw, file, line, "setup.py", group)
                    if d:
                        out.append(d)
            elif kw.arg == "extras_require":
                val = kw.value
                if isinstance(val, ast.Name) and val.id in names:
                    val = names[val.id]  # type: ignore[assignment]
                if isinstance(val, ast.Dict):
                    for k, v in zip(val.keys, val.values, strict=False):
                        extra = str(k.value) if isinstance(k, ast.Constant) else "extra"
                        g = group_for(str(extra))
                        for raw, line in _literal_strings(v, names):
                            d = parse_req_string(
                                raw,
                                file,
                                line,
                                "setup.py",
                                g if g != "runtime" else f"optional:{extra}",
                            )
                            if d:
                                out.append(d)
    return out


# --------------------------------------------------------------------------- Pipfile


def parse_pipfile(path: Path, root: Path) -> list[DeclaredDep]:
    file = rel(path, root)
    text = read_text(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    out: list[DeclaredDep] = []
    for section, group in (("packages", "runtime"), ("dev-packages", "dev")):
        for name, spec in (data.get(section, {}) or {}).items():
            if isinstance(spec, dict):
                spec = spec.get("version", "*")
            raw = name if spec in ("*", "", None) else f"{name}{spec}"
            d = parse_req_string(raw, file, _line_of(text, name), "pipfile", group)
            if d:
                out.append(d)
    return out


# --------------------------------------------------------------------------- test runners

_RUNNER = re.compile(
    r"(?<![\w-])(py\.test|pytest|nosetests|nose2|python3?\s+-m\s+(?:pytest|unittest|nose2?)"
    r"|python3?\s+setup\.py\s+test|tox|trial|green)(?![\w-])"
)
_RUNNER_FILES = (
    "Makefile",
    "makefile",
    "tox.ini",
    "noxfile.py",
    ".travis.yml",
    "Dockerfile",
    "Justfile",
    "justfile",
    "setup.cfg",
    "pytest.ini",
    "pyproject.toml",
    "run_tests.sh",
    "runtests.py",
)


def _runner_name(cmd: str) -> str:
    c = cmd.replace("py.test", "pytest")
    for n in ("pytest", "unittest", "nose2", "nosetests", "tox", "trial", "green"):
        if n in c:
            return "nose" if n == "nosetests" else n
    if "setup.py test" in c:
        return "setup.py test"
    return c.split()[0]


def find_test_runners(root: Path) -> list[TestRunner]:
    files = existing(root, _RUNNER_FILES)
    for d in (root / ".github" / "workflows", root / ".circleci", root / "scripts"):
        if d.is_dir():
            files += sorted(p for p in d.iterdir() if p.is_file())
    out: list[TestRunner] = []
    seen: set[tuple[str, str]] = set()
    for p in files:
        file = rel(p, root)
        for lineno, line in enumerate(read_text(p).splitlines(), start=1):
            s = line.strip()
            if s.startswith("#") and p.suffix not in (".py",):
                continue
            if p.name in ("setup.cfg", "pytest.ini", "pyproject.toml"):
                if re.match(r"^\[(tool:pytest|pytest|tool\.pytest\.ini_options)\]", s):
                    key = ("pytest", file)
                    if key not in seen:
                        seen.add(key)
                        out.append(
                            TestRunner(
                                name="pytest",
                                command="pytest",
                                location=Location(file=file, line=lineno),
                            )
                        )
                continue
            m = _RUNNER.search(s)
            if m:
                name = _runner_name(m.group(1))
                key = (name, file)
                if key in seen:
                    continue
                seen.add(key)
                cmd = re.sub(r"[\"'\],]+", " ", s[m.start() :]).strip()
                cmd = re.sub(r"\s+", " ", cmd)
                out.append(
                    TestRunner(
                        name=name, command=cmd[:200], location=Location(file=file, line=lineno)
                    )
                )
    return out


# --------------------------------------------------------------------------- orchestration


def find_conflicts(deps: list[DeclaredDep]) -> list[ManifestConflict]:
    """Same distribution declared with different specifiers in different manifests."""
    by_name: dict[str, list[DeclaredDep]] = {}
    for d in deps:
        if not d.commented_out and d.group != "constraint":
            by_name.setdefault(d.name, []).append(d)
    out = []
    for name, ds in sorted(by_name.items()):
        files = {d.location.file for d in ds}
        specs = {d.specifier for d in ds if d.specifier}
        if len(files) > 1 and len(specs) > 1:
            pins = {s for s in specs if s.startswith("==")}
            if len(pins) > 1:
                detail = "different exact pins: " + ", ".join(sorted(pins))
            else:
                detail = "specifiers differ across manifests: " + "; ".join(
                    f"{d.location.file}:{d.location.line} {d.specifier or '*'}" for d in ds
                )
            out.append(ManifestConflict(name=name, specifiers=ds, detail=detail))
    return out


class ManifestScan:
    def __init__(self) -> None:
        self.deps: list[DeclaredDep] = []
        self.runners: list[TestRunner] = []
        self.conflicts: list[ManifestConflict] = []
        self.install_paths: list[str] = []
        self.pyproject: dict[str, Any] = {}
        self.scripts: dict[str, str] = {}


def scan_manifests(root: Path) -> ManifestScan:
    ms = ManifestScan()
    seen: set[Path] = set()
    req_files = _req_files(root)
    for p in req_files:
        ms.deps += parse_requirements(p, root, seen=seen)
    pp = root / "pyproject.toml"
    if pp.exists():
        deps, data = parse_pyproject(pp, root)
        ms.deps += deps
        ms.pyproject = data
        ms.scripts = dict(data.get("project", {}).get("scripts", {}) or {})
        ms.scripts.update(data.get("tool", {}).get("poetry", {}).get("scripts", {}) or {})
    if (root / "setup.cfg").exists():
        ms.deps += parse_setup_cfg(root / "setup.cfg", root)
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read_string(read_text(root / "setup.cfg"))
            if cp.has_option("options.entry_points", "console_scripts"):
                for line in cp.get("options.entry_points", "console_scripts").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        ms.scripts[k.strip()] = v.strip()
        except configparser.Error:
            pass
    if (root / "setup.py").exists():
        ms.deps += parse_setup_py(root / "setup.py", root)
    if (root / "Pipfile").exists():
        ms.deps += parse_pipfile(root / "Pipfile", root)
    ms.runners = find_test_runners(root)
    ms.conflicts = find_conflicts(ms.deps)

    # Install paths: each top-level runtime requirements file, and the project itself.
    for p in req_files:
        r = rel(p, root)
        if group_for(p.stem if p.stem.lower() != "requirements" else "") == "runtime":
            ms.install_paths.append(f"requirements:{r}")
    has_project = bool(ms.pyproject.get("project") or ms.pyproject.get("tool", {}).get("poetry"))
    if has_project or (root / "setup.py").exists() or (root / "setup.cfg").exists():
        ms.install_paths.append("project:.")
    if (root / "Pipfile").exists() and not ms.install_paths:
        ms.install_paths.append("pipfile:Pipfile")
    return ms
