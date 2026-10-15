# Changelog

## m2 — Metadata + risk scoring + `scan` table
- Async, concurrent PyPI + GitHub metadata with a TTL disk cache and replay (cache-only) mode; trimmed raw responses stored as evidence.
- Per targeted version: latest, release history, days since last release, majors/minors behind, yanked, requires_python, cp312/cp313/universal wheels, Requires-Dist, GitHub archived/pushed_at.
- GitHub token from `GITHUB_TOKEN`, else the local `gh auth token`; degrades gracefully when rate-limited.
- Deterministic metadata signals (stale, inactive_classifier, archived_upstream, far_behind, old_major_pin, yanked, py312_313_incompatible, not_on_pypi, dead_test_runner) and transitive-pin downgrade; scoring documented in docs/CAPSULE_SPEC.md.
- `exitcapsule scan <repo>` Rich risk table. Verified on the target repo and on miguelgrinberg/microblog with no code changes.

## m1 — Ingest + manifests + AST usage + mapping
- Ingest: shallow clone / local copy, commit SHA, branch, Python-version hints (pyproject, setup.*, README, Dockerfile, CI, tox, Makefile).
- Manifests: requirements*.txt (`-r`/`-c` includes, continuations, commented-out pins, VCS/egg URLs), PEP 621 + optional deps, PEP 735 groups, Poetry, setup.cfg, setup.py (AST, never executed), Pipfile; runtime/dev/test/build groups; cross-manifest conflicts; declared test runners; install paths; console scripts.
- AST usage: imports, calls, attributes, decorators, base classes, inner Config/Meta classes, keyword args with file:line:snippet; private-API, test-file and guarded-import detection; imports of stdlib modules removed in 3.12/3.13.
- Mapping: installed-env metadata → generic alias table → name heuristics, with the evidence recorded.
- Scan-derived deterministic risk signals: cross_manifest_conflict, private_api_use, undeclared, unused, commented_out, build_tool_at_runtime, py312_313_incompatible (removed stdlib).
- Fixes: case-exact file matching on Windows; storage setting no longer reads `HOME`.

## m0 — Environment + scaffold
- uv project (Python 3.12), Typer CLI skeleton with every stage command, Rich progress renderer.
- Core: `Claim` provenance model and shared models, settings via `.env`, event bus, evidence store, resumable per-stage pipeline with timings.
- Gemma provider abstraction (Google AI Studio, Ollama, fake) with strict Gemma-4-only model discovery.
- `exitcapsule doctor` / `scripts/doctor.py`; `setup.ps1|sh`, `dev.ps1|sh`.
- Tests (incl. guard test against target-specific strings), ruff, mypy strict, coverage ≥ 70 %, GitHub Actions CI (Ubuntu + Windows).
