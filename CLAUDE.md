# SmartMovieSearch / CineAI — Claude Code rules

A production agentic RAG system (**LangGraph + Milvus + Anthropic Claude**) deployed as
[smartmoviesearch.com](https://smartmoviesearch.com) — a natural-language movie/TV/music intelligence
platform. A **Supervisor** agent routes each question to sub-agents (**RAG** over a curated KB incl.
optional Roger Ebert reviews, **TMDB**, **Music**/MusicBrainz, **Web Search**) and synthesizes a single
streaming answer. The app lives under `cineai/`.

> ⚠️ **This is a PUBLIC GitHub repo** (`worldwidejimmy/pipeline`) — one of the few public ones. Assume every commit, message, diff, and author identity is world-visible. **Never commit secrets/keys/`.env`.** Commit with a real name/email (not the box default `ubuntu@<vps-host>`), and avoid leaking internal URLs/hostnames in commit trailers.
>
> **Guardrails are installed and mandatory:** `.githooks/` (pre-commit + pre-push) scans every outgoing change for secret formats, forbidden file types, and private patterns from `~/.config/sms-repo-guard/patterns.txt` (names/domains/server IPs — kept outside the repo so the blocklist itself stays private). After a fresh clone, re-enable with `git config core.hooksPath .githooks`. **Never bypass with `--no-verify`, weaken the scanner, or edit the private patterns file unless the owner explicitly asks** — a hook block is a hard stop: report it, don't work around it.

## Source-of-truth docs — read before touching an area, update after
- `cineai/HANDOFF.md` — operational detail: Ebert-review scraping, routing-rules API, git conventions for generated data — **start here**
- `README.md` — architecture overview + live-demo notes
- `cineai/WHITEPAPER.md` — design rationale · `cineai/SECURITY-AUDIT.md` — findings

## Stack & ports (dev/local ports on this box)
- **Backend** — FastAPI + LangGraph in `cineai/backend`; LLM via `src/llm.py` (Anthropic Haiku/Sonnet/Opus). Port **8001**.
- **Frontend** — React (Vite) SPA behind nginx. Port **5174** (nginx smartmoviesearch.com → 5174).
- **Milvus** hybrid RAG (BM25 + dense): gRPC **19530**, health **9091**, Attu UI **5160**; plus `etcd` + `minio` compose deps.
- Whole stack is **Docker Compose**: `cd cineai && docker compose up -d`.

## Build / test / run
- Run stack: `cd cineai && docker compose up -d`; inspect: `docker compose ps` / `docker compose logs -f <svc>`.
- Tasks/tests: `cineai/Makefile` — run `make` to list targets. `make test-e2e` = Playwright browser test of the running stack (costs 1 search + tokens).
- `cineai/devops_check.py` — health/devops checks · `cineai/backup.sh` — backups.

## Gotchas
- **Real client IP: use `CF-Connecting-IP`.** The frontend nginx clobbers `X-Real-IP` (see the `client-ip-chain` memory).
- **Anthropic-powered** — consult the `claude-api` skill before changing model ids or LLM-call shape.
- **Parsing LLM JSON: always use `src/llm.py:parse_llm_json`**, never bare `json.loads` — Claude wraps "JSON only" replies in ```json fences, and a silent except-fallback degrades answers without any error (this broke TMDB title lookups for weeks).
- Shared VPS: unrelated apps run on this box — never wildcard-kill node/npm/python (kill by PID or use the app's own restart), and don't touch services you didn't start. (Server-wide rule in `~/.claude/CLAUDE.md`.)
