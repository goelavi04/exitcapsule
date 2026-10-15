# Status

_Last updated: 2026-10-15_

## Current milestone

**M2 — Metadata + risk scoring + `scan` table** (next).

## Done

- **M1** — ingest, manifests, AST usage, mapping, scan-derived risk signals (tag `m1`).
  - 3 synthetic fixture repos (`tests/fixtures/repos/`: legacyapp, setuppy_app, poetry_app) cover conflicts, undeclared, unused, private API, usage-site kinds, no-tests, setup.py/Pipfile/Poetry.
  - Smoke-scanned the target repo: 16 dependencies found, scan in 0.1 s (metadata signals still pending → M2).

- **M0** — environment + scaffold (tag `m0`).
  - Tools verified: git 2.50.1, gh 2.96.0 (logged in as `goelavi04`), uv 0.11.32, Python 3.12.4 + 3.13.3, Node 22.21.0, Ollama 0.40.2.
  - Gemma: key works, `gemma-4-31b-it` and `gemma-4-26b-a4b-it` listed and answered a real hello call.
  - CLI skeleton, models, config, events, evidence store, resumable pipeline, doctor, setup/dev scripts, CI, tests (75 % coverage).

## In progress

- —

## Blocked / waiting on humans

- Team collaborators' GitHub usernames (placeholder §0.4 not filled) — needed to add collaborators.
- Hackathon deadline (placeholder §0.4 not filled).

## Assumptions (made instead of blocking)

- GitHub repo: `https://github.com/goelavi04/exitcapsule`, public (open-source project, MIT).
- Gemma provider: Google AI Studio, model auto-discovered → `gemma-4-31b-it`. If the 31B stays flaky (observed 500/503 under load), switch `GEMMA_MODEL=gemma-4-26b-a4b-it` (still Gemma 4).
- Docker Desktop is installed but the daemon is not running → default `--sandbox local`. Docker sandbox is optional (M9).
- Ollama is installed but has no Gemma 4 model pulled (only `llama3.2`, which the engine will never use). Pulling a Gemma 4 tag is deferred to M4 (optional fallback).
- CI runs `doctor --no-gemma` because CI has no secrets by design.
- Ruff excludes `*.md` (ruff 0.17 formats code blocks in Markdown and would rewrite CLAUDE.md).

## Known issues

- `gemma-4-31b-it` intermittently returns HTTP 500/503 ("high demand"); retries/backoff land in M4.

## Next

1. M2: PyPI/GitHub metadata + risk scoring + `scan` table on target repo and one other repo.
