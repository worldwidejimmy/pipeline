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
| SEC-1 | 🔴 High | **Origin not locked to Cloudflare.** An attacker who finds the origin IP could hit it directly (CF reaches the origin over HTTP/80, Flexible SSL) with `Host: smartmoviesearch.com`, bypassing Cloudflare's WAF/bot/DDoS and spoofing `CF-Connecting-IP`. | **FIXED 2026-06-27** — **mTLS (Authenticated Origin Pulls)**: origin on 443 with a CF Origin cert, Cloudflare in **Full (Strict)** + AOP **Global**, nginx `ssl_verify_client on`. Only Cloudflare (valid client cert) can reach the app; direct origin → 400, port 80 → 301. The earlier IP allow-list was retired (no more IP-range maintenance). Plus the `CF-Connecting-IP` anti-spoof overwrite. |
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

## Host nginx changes (NOT in git — re-apply if the box/config is rebuilt)
Origin is now locked to Cloudflare by **mTLS (Authenticated Origin Pulls)** — the IP
allow-list was retired (2026-06-27).
- Cloudflare side: SSL/TLS mode **Full (Strict)** + **Authenticated Origin Pulls → Global**
  (uses CF's shared client cert; no upload).
- `/etc/ssl/sms-origin-cert.pem` + `sms-origin-key.pem` — Cloudflare **Origin** cert (the
  origin's server cert for Full Strict; covers `*.smartmoviesearch.com`, expires 2041).
- `/etc/nginx/ssl/cloudflare-origin-pull-ca.pem` — CF's origin-pull CA (verifies CF's client
  cert). Public download: developers.cloudflare.com/ssl/static/authenticated_origin_pull_ca.pem
- `/etc/nginx/sites-available/smartmoviesearch.com` vhost: `listen 443 ssl` with the origin
  cert/key, `ssl_client_certificate <CA>` + **`ssl_verify_client on`** (enforces mTLS),
  `set_real_ip_from` (v4+v6) + `proxy_set_header CF-Connecting-IP $remote_addr` (anti-spoof),
  and `if ($server_port = 80) { return 301 https://… }` (port 80 unused by CF). Backups:
  `*.bak-secaudit`, `*.bak-cflock`, `*.bak-aop`, `*.bak-enforce`.
- **This box needs `systemctl reload nginx` run twice** for config to fully apply.

## TODO — owner: **user**
- **SEC-3 — Anthropic billing guardrails:** console.anthropic.com → set a monthly spend
  limit + usage alerts. (In-app `DAILY_TOKEN_HARD_CAP` + the 30/day call cap already help.)
- **Optional hardening:** upgrade Cloudflare SSL from **Flexible → Full (Strict)** (serve the
  vhost on 443 with an origin cert) and add **Authenticated Origin Pulls** (mTLS) — removes
  the IP-list maintenance and encrypts CF↔origin. Also consider Cloudflare **Bot Fight Mode**.
