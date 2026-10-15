# Contributing

1. `./scripts/setup.ps1` (Windows) or `./scripts/setup.sh`.
2. Quality gates before every commit:
   `uv run ruff check . && uv run ruff format --check . && uv run mypy engine && uv run coverage run -m pytest -m "not slow" && uv run coverage report`
3. Conventional Commits (`feat(scan): ...`, `fix(execute): ...`).
4. **No target-specific code in `engine/`** — the guard test enforces this. Fix bugs generically and add a fixture regression test.
5. Never commit `.env`, keys, `.exitcapsule/`, venvs or `node_modules`.
