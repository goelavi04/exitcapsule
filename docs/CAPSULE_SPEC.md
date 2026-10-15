# Capsule specification

> Living document. The full capsule JSON schema lands in M6 (`engine/capsule/schema.json`).

## Risk score (deterministic, observed-only)

`score = min(100, Σ weight(signal.severity))` with weights **info 0 · low 5 · medium 15 · high 30**.

A dependency is a **migration candidate (flagged)** when it has at least one signal of severity ≥ `low`.
Gemma's severity opinion is displayed next to this score and never changes it.

### Signals and rules

| Kind | Severity | Rule (source of evidence) |
|---|---|---|
| `stale` | low / medium / high | last release ≥ 2 / 3 / 5 years ago (PyPI upload times) |
| `inactive_classifier` | high | trove classifier `Development Status :: 7 - Inactive` |
| `archived_upstream` | high | GitHub repo `archived: true` (source/repository URL preferred over funding links) |
| `far_behind` | medium / high | targeted version is 1 / ≥2 majors behind latest |
| `far_behind` | low | same major, ≥ 5 minors behind |
| `old_major_pin` | medium | specifier caps below the latest major (`<N`, `<=`, `~=`) |
| `yanked` | high | targeted version is yanked |
| `py312_313_incompatible` | high | targeted version's `requires_python` excludes 3.12 or 3.13 |
| `py312_313_incompatible` | high / medium | imports a stdlib module removed in 3.12 / 3.13 |
| `py312_313_incompatible` | medium | binary wheels exist but none for CPython 3.12 |
| `py312_313_incompatible` | low | sdist-only (source build) at the targeted version; or cp312 wheels but none for cp313 |
| `not_on_pypi` | high | PyPI returns 404 |
| `cross_manifest_conflict` | high / medium | different exact pins / different specifiers across manifests |
| `private_api_use` | medium | a `_private` name of the package is used (AST) |
| `undeclared` | medium / low / info | imported by app code / only by tests / only inside `try: … except ImportError` |
| `unused` | low | declared runtime dependency never imported or invoked |
| `unused` | info | tooling/dev dependency, invoked as a tool (Procfile, Dockerfile, CI…), or a pinned transitive dependency of another declared package |
| `commented_out` | info | a commented-out requirement line |
| `build_tool_at_runtime` | medium | `pkg_resources` / `setuptools` / `distutils` / `pip` / `wheel` used in application code |
| `dead_test_runner` | high | a test runner the repo invokes depends on a package with medium/high `stale`, `inactive_classifier`, `archived_upstream`, `py312_313_incompatible` or `not_on_pypi` |
| `runtime_deprecation` | — | from baseline execution (M3) |
| `install_failure` | — | from baseline execution (M3) |

**"Targeted version"** = the exact `==` pin if one exists, else the newest release satisfying all declared specifiers.

**Indirect (transitive-only) pins.** A declared package that is never imported or invoked, and is in the `Requires-Dist` of another declared package, has its metadata signals downgraded one level (high→medium→low→info), with `(indirect via X)` appended to the detail. Rationale: it is migrated by migrating its parent.
