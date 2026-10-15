"""Stage 2c — Import name ↔ distribution mapping.

Why: code imports `yaml` but manifests declare `PyYAML`. Mapping is resolved
generically, in order of evidence strength:
1. the installed environment's own metadata (`packages_distributions()`,
   i.e. top_level.txt / RECORD) when a baseline env exists;
2. a small, generic alias table of well-known mismatches (no per-repo entries);
3. name normalisation heuristics against the declared distributions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from packaging.utils import canonicalize_name

from engine.fsutil import run, scrubbed_env
from engine.models import DeclaredDep, Dependency, UsageSite, observed

# Generic, ecosystem-wide import->distribution mismatches. Not specific to any repo.
ALIASES: dict[str, str] = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "Crypto": "pycryptodome",
    "Cryptodome": "pycryptodomex",
    "OpenSSL": "pyopenssl",
    "attr": "attrs",
    "magic": "python-magic",
    "serial": "pyserial",
    "usb": "pyusb",
    "git": "gitpython",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "slugify": "python-slugify",
    "jose": "python-jose",
    "multipart": "python-multipart",
    "socks": "pysocks",
    "zmq": "pyzmq",
    "MySQLdb": "mysqlclient",
    "Levenshtein": "python-levenshtein",
    "pkg_resources": "setuptools",
    "_pytest": "pytest",
    "telegram": "python-telegram-bot",
    "discord": "discord-py",
    "gi": "pygobject",
    "wx": "wxpython",
    "kafka": "kafka-python",
    "ldap": "python-ldap",
    "memcache": "python-memcached",
    "nacl": "pynacl",
    "fitz": "pymupdf",
    "google.protobuf": "protobuf",
    "win32api": "pywin32",
    "win32con": "pywin32",
    "pythoncom": "pywin32",
    "mpl_toolkits": "matplotlib",
    "Xlib": "python-xlib",
    "rest_framework": "djangorestframework",
    "corsheaders": "django-cors-headers",
    "debug_toolbar": "django-debug-toolbar",
    "editor": "python-editor",
    "pydispatch": "pydispatcher",
    "websocket": "websocket-client",
    "engineio": "python-engineio",
    "socketio": "python-socketio",
    "Bio": "biopython",
    "past": "future",
    "dns": "dnspython",
    "github": "pygithub",
    "gitlab": "python-gitlab",
    "ruamel": "ruamel-yaml",
}

# Distributions that are normally used as tools / plugins, not imported.
_TOOL_PREFIXES = ("pytest-", "types-", "flake8", "mypy", "sphinx", "pylint", "pre-commit")
TOOL_DISTS = frozenset(
    {
        "black", "ruff", "isort", "coverage", "tox", "nox", "wheel", "setuptools", "twine",
        "build", "pip", "pip-tools", "bump2version", "bumpversion", "codecov", "autopep8",
        "yapf", "bandit", "safety", "pyflakes", "pycodestyle", "pydocstyle", "invoke",
        "gunicorn", "uvicorn", "hypercorn", "waitress", "watchdog", "ipython", "jupyter",
        "notebook", "hatchling", "flit", "flit-core", "poetry", "poetry-core", "setuptools-scm",
        "cython", "nose", "pytest", "mkdocs", "mkdocs-material", "docutils", "check-manifest",
    }
)  # fmt: skip

_PACKAGES_DISTRIBUTIONS_SCRIPT = (
    "import json, importlib.metadata as m; "
    "print(json.dumps({k: v for k, v in m.packages_distributions().items()}))"
)


def env_import_map(python: Path, timeout: float = 60) -> dict[str, list[str]]:
    """Ask an installed environment which distributions provide which import names."""
    res = run(
        [str(python), "-c", _PACKAGES_DISTRIBUTIONS_SCRIPT], timeout=timeout, env=scrubbed_env()
    )
    if not res.ok:
        return {}
    try:
        raw = json.loads(res.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {}
    return {k: sorted({canonicalize_name(d) for d in v}) for k, v in raw.items()}


def is_tool_dist(name: str) -> bool:
    return name in TOOL_DISTS or name.startswith(_TOOL_PREFIXES)


def _variants(name: str) -> set[str]:
    """Normalised spellings a distribution name may take as an import name."""
    n = canonicalize_name(name)
    out: set[str] = {str(n)}
    for prefix in ("python-", "py-", "py"):
        if n.startswith(prefix) and len(n) > len(prefix) + 1:
            out.add(n[len(prefix) :])
    for suffix in ("-python", "-py", "-binary", "-headless"):
        if n.endswith(suffix):
            out.add(n[: -len(suffix)])
    return out


def resolve_import(
    module: str,
    declared: set[str],
    env_map: dict[str, list[str]] | None = None,
) -> tuple[str, str]:
    """Return (distribution, how) for an import name. `how` documents the evidence."""
    if env_map and module in env_map:
        dists = env_map[module]
        hit = [d for d in dists if d in declared]
        return (hit or dists)[0], "installed-env metadata (packages_distributions)"
    norm = canonicalize_name(module)
    for d in sorted(declared):
        if norm in _variants(d):
            return d, "name match with declared distribution"
    if module in ALIASES:
        return ALIASES[module], "generic alias table"
    return norm, "assumed import name == distribution name (unverified)"


def build_dependencies(
    declared: list[DeclaredDep],
    sites: list[UsageSite],
    imported: dict[str, list[str]],
    env_map: dict[str, list[str]] | None = None,
) -> list[Dependency]:
    declared_names = {d.name for d in declared}
    deps: dict[str, Dependency] = {}
    for d in declared:
        deps.setdefault(d.name, Dependency(name=d.name)).declared.append(d)
    module_to_dist: dict[str, str] = {}
    for module in sorted(imported):
        dist, how = resolve_import(module, declared_names, env_map)
        module_to_dist[module] = dist
        dep = deps.setdefault(dist, Dependency(name=dist))
        if module not in dep.import_names:
            dep.import_names.append(module)
        dep.mapping_note = observed(f"{module} -> {dist}", note=how)
    for s in sites:
        owner = module_to_dist.get(s.module)
        if owner:
            deps[owner].usage.append(s)
    return sorted(deps.values(), key=lambda d: d.name)


def mentioned_outside_python(root: Path, dist: str, files: list[Path]) -> str | None:
    """Is a distribution invoked as a tool (Procfile/Dockerfile/Makefile/CI)? Returns where."""
    pat = re.compile(rf"(?<![\w-]){re.escape(dist)}(?![\w-])", re.IGNORECASE)
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if pat.search(line) and "install" not in line.lower():
                return f"{p.relative_to(root).as_posix()}:{i}"
    return None
