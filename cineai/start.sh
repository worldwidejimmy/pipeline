#!/usr/bin/env bash
# CineAI — development startup script
# For production on OVH: use docker compose (see HANDOFF.md)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

# ── Preflight checks ──────────────────────────────────────────────────────────

if [ ! -f "$BACKEND/.env" ]; then
  cp "$BACKEND/.env.example" "$BACKEND/.env"
  echo "⚠  Created backend/.env from template."
  echo "   Fill in GROQ_API_KEY, TMDB_BEARER_TOKEN, OPENAI_API_KEY then re-run."
  exit 1
fi

# Warn if required keys are still placeholders
for KEY in GROQ_API_KEY TMDB_BEARER_TOKEN; do
  VALUE=$(grep "^${KEY}=" "$BACKEND/.env" | cut -d= -f2-)
  if echo "$VALUE" | grep -qE "your_|_here|placeholder"; then
    echo "⚠  $KEY looks like a placeholder in backend/.env — update it"
  fi
done

# ── Python venv ───────────────────────────────────────────────────────────────

if [ ! -d "$BACKEND/.venv" ]; then
  echo "→ Creating Python venv..."
  python3 -m venv "$BACKEND/.venv"
fi

echo "→ Installing/updating backend dependencies..."
"$BACKEND/.venv/bin/pip" install -q --upgrade pip
"$BACKEND/.venv/bin/pip" install -q -r "$BACKEND/requirements.txt"

# ── Frontend deps ─────────────────────────────────────────────────────────────

if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "→ Installing frontend dependencies..."
  cd "$FRONTEND" && npm install
fi

# ── Check Milvus ─────────────────────────────────────────────────────────────

echo "→ Checking Milvus..."
if ! curl -sf http://localhost:9091/healthz > /dev/null 2>&1; then
  echo ""
  echo "⚠  Milvus is not running. Start it with:"
  echo "     docker compose up -d milvus etcd minio"
  echo "   or from the pipeline project:"
  echo "     cd /path/to/pipeline && docker compose up -d"
  echo ""
  echo "   Continuing anyway — RAG agent will fail without Milvus."
  echo ""
fi

# ── Start backend ─────────────────────────────────────────────────────────────

echo "→ Starting backend on http://localhost:8001 ..."
cd "$BACKEND"
"$BACKEND/.venv/bin/uvicorn" src.main:app \
  --host 0.0.0.0 --port 8001 --reload \
  2>&1 | sed 's/^/[backend] /' &
BACKEND_PID=$!

# Brief pause to let backend start before frontend
sleep 2

# ── Start frontend ────────────────────────────────────────────────────────────

echo "→ Starting frontend on http://localhost:5174 ..."
cd "$FRONTEND"
npm run dev 2>&1 | sed 's/^/[frontend] /' &
FRONTEND_PID=$!

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CineAI running in development mode"
echo ""
echo "  Frontend  →  http://localhost:5174"
echo "  Backend   →  http://localhost:8001"
echo "  API docs  →  http://localhost:8001/docs"
echo "  Attu UI   →  http://localhost:5160  (if Milvus is up)"
echo ""
echo "  To ingest movie corpus:"
echo "    cd backend && .venv/bin/python scripts/ingest.py docs/"
echo ""
echo "  Press Ctrl+C to stop."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cleanup() {
  echo ""
  echo "→ Stopping services..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
wait
