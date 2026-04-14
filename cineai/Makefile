.PHONY: up down logs restart ingest build dev test-api clean help

BACKEND_DIR := backend
FRONTEND_DIR := frontend
COMPOSE     := docker compose

help:   ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Docker Compose ─────────────────────────────────────────────────────────────

up:     ## Start full stack (Milvus + backend + frontend)
	$(COMPOSE) up -d
	@echo "Waiting for Milvus to be healthy..."
	@$(COMPOSE) exec -T milvus curl -sf http://localhost:9091/healthz > /dev/null 2>&1 || sleep 15
	@echo "Stack is up. Frontend: http://localhost:5174  Backend: http://localhost:8001"

down:   ## Stop all services
	$(COMPOSE) down

restart: ## Restart backend (after code changes)
	$(COMPOSE) restart backend

logs:   ## Tail logs from all services
	$(COMPOSE) logs -f --tail=50

logs-backend: ## Tail backend logs only
	$(COMPOSE) logs -f backend --tail=100

# ── Application ────────────────────────────────────────────────────────────────

ingest: ## Ingest docs/ into Milvus (run after 'make up')
	$(COMPOSE) exec backend python scripts/ingest.py docs/

ingest-local: ## Ingest docs using local venv (dev mode)
	cd $(BACKEND_DIR) && .venv/bin/python scripts/ingest.py docs/

build:  ## Build frontend static files
	cd $(FRONTEND_DIR) && npm run build
	@echo "Frontend built to frontend/dist/"

# ── Dev mode (no Docker for app, Milvus still Docker) ─────────────────────────

dev-backend: ## Start backend in dev mode with auto-reload
	cd $(BACKEND_DIR) && .venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8001 --reload

dev-frontend: ## Start frontend Vite dev server
	cd $(FRONTEND_DIR) && npm run dev

setup-venv: ## Create Python venv and install dependencies
	cd $(BACKEND_DIR) && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	@echo "Venv created. Activate with: source backend/.venv/bin/activate"

setup-frontend: ## Install frontend dependencies
	cd $(FRONTEND_DIR) && npm install

setup: setup-venv setup-frontend ## Install all dependencies (dev mode)
	@cp -n $(BACKEND_DIR)/.env.example $(BACKEND_DIR)/.env || true
	@echo "\nSetup complete. Edit backend/.env with your API keys, then run: make up"

# ── Testing ───────────────────────────────────────────────────────────────────

test-api: ## Smoke test the running backend API
	@echo "=== Health check ===" && curl -sf http://localhost:8001/api/health | python3 -m json.tool
	@echo "\n=== Movie search ===" && curl -sf "http://localhost:8001/api/search?q=inception" | python3 -m json.tool | head -20
	@echo "\n=== Trending ===" && curl -sf "http://localhost:8001/api/trending" | python3 -m json.tool | head -20
	@echo "\n=== SSE stream (5s) ===" && timeout 5 curl -N "http://localhost:8001/api/query?q=what+is+inception&thread_id=test" || true

# ── Cleanup ───────────────────────────────────────────────────────────────────

clean:  ## Remove built frontend, Python cache
	rm -rf $(FRONTEND_DIR)/dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

clean-all: clean ## Remove venv and node_modules too
	rm -rf $(BACKEND_DIR)/.venv $(FRONTEND_DIR)/node_modules
