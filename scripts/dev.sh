#!/usr/bin/env bash
# Start backend (FastAPI on :8000) and UI (Vite on :5173) together.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run uvicorn backend.app:app --port 8000 --reload &
API=$!
trap 'kill $API 2>/dev/null || true' EXIT
(cd ui && npm run dev)
