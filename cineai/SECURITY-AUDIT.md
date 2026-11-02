# SmartMovieSearch — Security Audit & Remediation

_Audit date: 2026-06-25. Scope: the now-public open-access site (smartmoviesearch.com),
its API, the deployment on the VPS host, and the public `worldwidejimmy/pipeline` repo._

## Verdict

- **Server breach / RCE / secret theft:** no path found. The LLM has no privileged tools,
  no secrets sit in its prompt, and there's no user-controlled code/URL execution.
- **Abuse / cost harm:** real, until the origin is locked to the CDN (see SEC-1). The
  per-IP defenses key on a header that can be spoofed by reaching the origin directly.
- **Public repo:** clean — no secrets, keys, server IPs, or personal emails in tracked
  files or git history. Two minor metadata items (below).

## Findings

| ID | Sev | Finding | Status |
|----|-----|---------|--------|
| SEC-1 | 🔴 High | **Origin not locked to the CDN.** An attacker who finds the origin IP could hit it directly (the CDN reached the origin over plain HTTP/80) with `Host: smartmoviesearch.com`, bypassing the CDN's WAF/bot/DDoS and spoofing the CDN's client-IP header. | **FIXED 2026-06-27** — **mTLS origin authentication**: origin on 443 with a CDN origin certificate, the CDN in **Full (Strict)** with origin authentication enabled globally, nginx `ssl_verify_client on`. Only the CDN (valid client cert) can reach the app; direct origin → 400, port 80 → 301. The earlier IP allow-list was retired (no more IP-range maintenance). Plus the anti-spoof overwrite of the CDN's client-IP header. |
| SEC-2 | 🟠 Med | **Docker ports on `0.0.0.0`** — `8001` (backend), `5174` (frontend), `5160` (Attu, the Milvus **admin UI, no auth**), `19530` (Milvus DB, no auth). Docker's iptables bypass ufw. External probes currently get `ECONNREFUSED` (the hosting provider's edge likely blocks), but it's undocumented/fragile; if ever reachable → full vector-DB read/wipe. | **Fixed** — all bound to `127.0.0.1` in `docker-compose.yml`. |
| SEC-3 | 🟠 Med-High | **Financial DoS** on the paid Anthropic key. Open LLM access; per-IP cap is bypassable (SEC-1) and IP-rotatable. No global daily spend ceiling. | **Fixed (app side)** — `DAILY_TOKEN_HARD_CAP` kill-switch pauses the LLM when exceeded. **Set a billing limit + alerts in the Anthropic console** (owner: user). |
| SEC-4 | 🟡 Low | **Unauthenticated `GET`/`DELETE /api/history`** — anyone could read/clear a thread's conversation. | **Fixed** — gated to the admin token. |
| SEC-5 | 🟡 Low | **Info disclosure** — `/api/status` exposes key-presence booleans, model, Milvus chunk counts. Access token rides in the SSE URL (`?_t=`) so it lands in nginx logs. | Accepted (low). Protect logs. Consider scoping the SSE token separately later. |
| SEC-6 | 🟡 Low | **Prompt injection** — user query + RAG/Tavily/TMDB text flow into Claude. Blast radius is output manipulation + token waste only; no tool execution, no secrets in context. Cannot escalate to RCE or data theft. | Accepted (content risk). A CDN challenge (CAPTCHA) + spend cap would reduce abuse. |
| REPO-1 | 🟡 Low | A box-default commit author email could leak the host in public commit metadata. | **Mitigated** — repo git identity pinned to `…@users.noreply.github.com`; pre-commit + pre-push hooks reject any box-default identity. Verified 2026-08-04: no commit in published history uses one. |
| REPO-2 | ⚪ Info | `Claude-Session:` URLs in commit bodies (auth-gated, not public-readable). | Accepted. |

## What an attacker CANNOT do
RCE · read `.env`/API keys · escape the container · pivot to other apps on the box ·
SQL/NoSQL injection · SSRF to internal services.

---

## Host nginx changes (NOT in git — re-apply if the box/config is rebuilt)
Origin is now locked to the CDN by **mTLS origin authentication** — the IP
allow-list was retired (2026-06-27).
- CDN side: SSL/TLS mode **Full (Strict)** + **mTLS origin authentication → Global**
  (uses the CDN's shared client cert; no upload).
- The origin certificate and key: a CDN-issued **origin** certificate (the
  origin's server cert for Full Strict; covers `*.smartmoviesearch.com`, expires 2041).
- The CDN's origin-pull CA certificate (verifies the CDN's client
  cert).
- `/etc/nginx/sites-available/smartmoviesearch.com` vhost: `listen 443 ssl` with the origin
  cert/key, `ssl_client_certificate <CA>` + **`ssl_verify_client on`** (enforces mTLS),
  `set_real_ip_from` (v4+v6) + overwriting the CDN client-IP header with `$remote_addr` (anti-spoof),
  and `if ($server_port = 80) { return 301 https://… }` (port 80 unused by the CDN). Backups:
  `*.bak-*` (one per hardening step).
- **This box needs `systemctl reload nginx` run twice** for config to fully apply.

## Owner items

Open: **SEC-3**, set a monthly spend limit and usage alerts in the Anthropic console (the in-app
`DAILY_TOKEN_HARD_CAP` and the 30/day call cap already help). Tracked in
**[../TODO.md](../TODO.md)**, with optional CDN bot protection.

Done (verified 2026-11-02): the origin serves HTTPS on 443 with a CDN origin certificate
and enforces **mTLS origin authentication** (mTLS: only the CDN can connect; a direct request
is refused), plain HTTP redirects to HTTPS, and 80/443 are firewalled to the CDN's IP ranges.
