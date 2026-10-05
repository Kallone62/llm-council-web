#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js/npm is required."
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
fi

if [ ! -x .venv/bin/python ]; then
  uv sync
fi

if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install)
fi

echo "Starting backend on http://localhost:8001..."
uv run python -m backend.main &
BACKEND_PID=$!

sleep 2

echo "Starting frontend on http://localhost:5173..."
(cd frontend && npm run dev) &
FRONTEND_PID=$!

echo
printf 'LLM Council is running. Open http://localhost:5173\n'
printf 'Configure OpenRouter and council models from Settings.\n'
printf 'Press Ctrl+C to stop both servers.\n'

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
wait
