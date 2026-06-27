# SmartMovieSearch — Security Audit & Remediation

_Audit date: 2026-06-25. Scope: the now-public open-access site (smartmoviesearch.com),
its API, the deployment on the OVH host, and the public `worldwidejimmy/pipeline` repo._

## Verdict

- **Server breach / RCE / secret theft:** no path found. The LLM has no privileged tools,
  no secrets sit in its prompt, and there's no user-controlled code/URL execution.
- **Abuse / cost harm:** real, until the origin is locked to Cloudflare (see SEC-1). The
  per-IP defenses key on a header that can be spoofed by reaching the origin directly.
- **Public repo:** clean — no secrets, keys, server IPs, or personal emails in tracked
  files or git history. Two minor metadata items (below).

## Findings

| ID | Sev | Finding | Status |
|----|-----|---------|--------|
| SEC-1 | 🔴 High | **Origin not locked to Cloudflare.** Port 443 on the origin IP is world-open; host nginx forwards the client's `CF-Connecting-IP` unchanged. An attacker who finds the origin IP can hit it directly with `Host: smartmoviesearch.com` + a forged `CF-Connecting-IP`, bypassing Cloudflare's WAF/bot/DDoS **and** spoofing a fresh IP per request — defeating our rate limit, blacklist, auth-lockout, and bot checks. | **Partly fixed** (nginx now overwrites `CF-Connecting-IP` from validated `$remote_addr` — closes the spoof). **Cloudflare allow-list / Authenticated Origin Pulls still TODO** (owner: user, see below). |
| SEC-2 | 🟠 Med | **Docker ports on `0.0.0.0`** — `8001` (backend), `5174` (frontend), `5160` (Attu, the Milvus **admin UI, no auth**), `19530` (Milvus DB, no auth). Docker's iptables bypass ufw. External probes currently get `ECONNREFUSED` (OVH edge likely blocks), but it's undocumented/fragile; if ever reachable → full vector-DB read/wipe. | **Fixed** — all bound to `127.0.0.1` in `docker-compose.yml`. |
| SEC-3 | 🟠 Med-High | **Financial DoS** on the paid Anthropic key. Open LLM access; per-IP cap is bypassable (SEC-1) and IP-rotatable. No global daily spend ceiling. | **Fixed (app side)** — `DAILY_TOKEN_HARD_CAP` kill-switch pauses the LLM when exceeded. **Set a billing limit + alerts in the Anthropic console** (owner: user). |
| SEC-4 | 🟡 Low | **Unauthenticated `GET`/`DELETE /api/history`** — anyone could read/clear a thread's conversation. | **Fixed** — gated to the admin token. |
| SEC-5 | 🟡 Low | **Info disclosure** — `/api/status` exposes key-presence booleans, model, Milvus chunk counts. Access token rides in the SSE URL (`?_t=`) so it lands in nginx logs. | Accepted (low). Protect logs. Consider scoping the SSE token separately later. |
| SEC-6 | 🟡 Low | **Prompt injection** — user query + RAG/Tavily/TMDB text flow into Claude. Blast radius is output manipulation + token waste only; no tool execution, no secrets in context. Cannot escalate to RCE or data theft. | Accepted (content risk). Turnstile + spend cap reduce abuse. |
| REPO-1 | 🟡 Low | Commit author email `ubuntu@vps-...ovh.us` leaks the OVH host in public commit metadata. | **Mitigated going forward** — repo git identity set to `…@users.noreply.github.com`. History rewrite optional. |
| REPO-2 | ⚪ Info | `Claude-Session:` URLs in commit bodies (auth-gated, not public-readable). | Accepted. |

## What an attacker CANNOT do
RCE · read `.env`/API keys · escape the container · pivot to other apps on the box ·
SQL/NoSQL injection · SSRF to internal services.

---

## TODO — owner: **user** (do in the Cloudflare dashboard)

**SEC-1 — lock the origin to Cloudflare** (do either or both; B is strongest):

**A. Firewall allow-list (quick):** restrict origin port 443 to Cloudflare's published IP
ranges so direct-to-origin requests are dropped.
- Cloudflare IP ranges: <https://www.cloudflare.com/ips/>
- On the OVH host, replace the broad `443 ALLOW` with per-range allows (ufw), e.g. for each
  CIDR in that list: `sudo ufw allow from <CIDR> to any port 443 proto tcp`, then
  `sudo ufw delete allow 443/tcp`. (Script it — there are ~15 v4 + ~7 v6 ranges.)

**B. Authenticated Origin Pulls (strongest, no IP list to maintain):**
- Cloudflare dashboard → SSL/TLS → Origin Server → **Authenticated Origin Pulls** → enable
  (zone-level), then install Cloudflare's client cert on the origin nginx and add
  `ssl_client_certificate` + `ssl_verify_client on;` to the `smartmoviesearch.com` server
  block. Cloudflare docs: search "Authenticated Origin Pulls".
- This makes nginx reject any TLS connection not bearing Cloudflare's client cert.

**SEC-3 — Anthropic billing guardrails:** console.anthropic.com → set a monthly spend
limit + usage alerts.

**Optional — also enable Cloudflare Bot Fight Mode** (Security → Bots) for a free extra layer.
