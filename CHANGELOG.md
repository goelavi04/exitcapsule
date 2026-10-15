# Changelog

## m0 — Environment + scaffold
- uv project (Python 3.12), Typer CLI skeleton with every stage command, Rich progress renderer.
- Core: `Claim` provenance model and shared models, settings via `.env`, event bus, evidence store, resumable per-stage pipeline with timings.
- Gemma provider abstraction (Google AI Studio, Ollama, fake) with strict Gemma-4-only model discovery.
- `exitcapsule doctor` / `scripts/doctor.py`; `setup.ps1|sh`, `dev.ps1|sh`.
- Tests (incl. guard test against target-specific strings), ruff, mypy strict, coverage ≥ 70 %, GitHub Actions CI (Ubuntu + Windows).
