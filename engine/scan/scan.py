"""Stage 2 — Scan orchestration (no AI, pure evidence).

Why: combines manifests, AST usage, mapping, metadata and deterministic risk
into one `ScanResult` that every later stage reads.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from engine.evidence import EvidenceStore
from engine.fsutil import existing, iter_files
from engine.models import Dependency, RepoInfo, ScanResult, observed
from engine.scan import risk
from engine.scan.manifests import scan_manifests
from engine.scan.mapping import build_dependencies, is_tool_dist, mentioned_outside_python
from engine.scan.usage import REMOVED_STDLIB, scan_usage

MetadataHook = Callable[[list[Dependency]], None]

_TOOL_FILES = (
    "Procfile",
    "Dockerfile",
    "Makefile",
    "makefile",
    "tox.ini",
    "noxfile.py",
    "Justfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".pre-commit-config.yaml",
)  # fmt: skip  (manifests excluded: they mention every dist they declare)


def _tool_files(root: Path) -> list[Path]:
    files = existing(root, _TOOL_FILES)
    for d in (root / ".github" / "workflows", root / "scripts", root / "bin"):
        if d.is_dir():
            files += sorted(p for p in d.iterdir() if p.is_file())
    files += [p for p in root.glob("*.sh")]
    return files


def scan_repo(
    repo: RepoInfo,
    evidence: EvidenceStore,
    env_map: dict[str, list[str]] | None = None,
    metadata_hook: MetadataHook | None = None,
) -> ScanResult:
    root = Path(repo.local_path)
    ms = scan_manifests(root)
    us = scan_usage(root)
    evidence.save(
        "json",
        "manifests",
        {
            "declared": [d.model_dump() for d in ms.deps],
            "test_runners": [r.model_dump() for r in ms.runners],
            "conflicts": [c.model_dump() for c in ms.conflicts],
            "install_paths": ms.install_paths,
            "scripts": ms.scripts,
        },
        "parsed manifest declarations",
    )
    usage_eid = evidence.save(
        "json",
        "usage",
        {
            "sites": [s.model_dump() for s in us.sites],
            "imported": us.imported,
            "parse_errors": us.parse_errors,
            "local_modules": sorted(us.local),
        },
        "AST usage sites",
    )

    deps = build_dependencies(ms.deps, us.sites, us.imported, env_map)
    if metadata_hook:
        metadata_hook(deps)

    tool_files = _tool_files(root)
    for d in deps:
        tool_use = None
        if d.declared and not d.usage:
            tool_use = mentioned_outside_python(root, d.name, tool_files)
        sigs = risk.scan_signals(d, ms.conflicts, is_tool_dist(d.name), tool_use, us.guarded)
        for s in sigs:
            s.evidence.append(usage_eid)
        risk.apply(d, sigs + risk.metadata_signals(d))

    # Imports of stdlib modules removed in 3.12/3.13 are migration candidates too.
    for mod, locs in sorted(us.removed_stdlib.items()):
        d = Dependency(name=mod, import_names=[mod])
        d.mapping_note = observed(
            f"{mod} (stdlib, removed in Python {REMOVED_STDLIB[mod]})",
            note="CPython removed-module list",
        )
        d.usage = [s for s in us.sites if s.module == mod]
        risk.apply(d, [risk.removed_stdlib_signal(mod, REMOVED_STDLIB[mod], locs)])
        deps.append(d)

    limitations = []
    if us.test_files == 0:
        limitations.append(
            "Repository has no test files; behaviour evidence will rely on smoke "
            "tests and generated characterization tests."
        )
    limitations += [f"Could not parse (Python 2 syntax?): {e}" for e in us.parse_errors]
    has_py = any(True for _ in iter_files(root, (".py",)))
    if not has_py:
        limitations.append("No Python files found.")
    return ScanResult(
        repo=repo,
        dependencies=sorted(deps, key=lambda d: (-d.score, d.name)),
        test_runners=ms.runners,
        conflicts=ms.conflicts,
        install_paths=ms.install_paths,
        local_modules=sorted(us.local),
        has_tests=us.test_files > 0,
        limitations=limitations,
    )
