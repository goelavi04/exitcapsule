#!/usr/bin/env bash
# ExitCapsule bootstrap (Linux/macOS). Idempotent: safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

need() { command -v "$1" >/dev/null 2>&1; }
need git || { echo "git is required (install with your package manager)"; exit 1; }
if ! need uv; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv python install 3.12 3.13
uv sync
if [ -f ui/package.json ]; then
  if need npm; then (cd ui && npm install); else echo "npm not found: UI skipped (install Node.js LTS)"; fi
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env - put your key on the line: GEMINI_API_KEY=<your key>"
fi
uv run exitcapsule doctor
