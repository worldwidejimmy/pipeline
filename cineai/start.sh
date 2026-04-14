#!/usr/bin/env bash
# Start CineAI backend and frontend
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── Backend ────────────────────────────────────────────────────────────────────
if [ ! -d "$ROOT/backend/.venv" ]; then
  echo "→ Creating Python venv..."
  python3 -m venv "$ROOT/backend/.venv"
fi

echo "→ Installing backend dependencies..."
"$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"

if [ ! -f "$ROOT/backend/.env" ]; then
  cp "$ROOT/backend/.env.example" "$ROOT/backend/.env"
  echo "⚠  Created backend/.env from example — fill in your API keys before starting."
  exit 1
fi

echo "→ Starting backend on http://localhost:8001 ..."
cd "$ROOT/backend"
"$ROOT/backend/.venv/bin/uvicorn" src.main:app --host 0.0.0.0 --port 8001 --reload &
BACKEND_PID=$!

# ── Frontend ───────────────────────────────────────────────────────────────────
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "→ Installing frontend dependencies..."
  cd "$ROOT/frontend" && npm install
fi

echo "→ Starting frontend on http://localhost:5174 ..."
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "✓ CineAI running:"
echo "  Frontend  →  http://localhost:5174"
echo "  Backend   →  http://localhost:8001"
echo "  API docs  →  http://localhost:8001/docs"
echo ""
echo "Press Ctrl+C to stop both services."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT INT TERM
wait
