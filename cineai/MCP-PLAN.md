# MCP server: design and threat model

Status: **shipped** 2026-10-30 (the public endpoint is live; `/mcp/agent` is waiting on the owner's
choice of authorization server). Phase 2, step 1 of `PLATFORM-PLAN.md`. Ops: `HANDOFF.md` → MCP server.

SmartMovieSearch exposes its retrieval stack as a remote **Model Context Protocol** server,
so any MCP client (Claude Desktop, Claude Code, Cursor, an Agent SDK app) can use the
curated movie/music knowledge base as tools.

## Shape: primitives, not the agent

An MCP client already has its own model, so the public endpoint offers the building
blocks (retrieval and lookups), not our LLM. That keeps it close to free per call. It also
leaves synthesis with the caller's model, which is the right split for tool use.

| Endpoint | Auth | Tools | Our cost/call |
|---|---|---|---|
| `https://smartmoviesearch.com/mcp` | none, rate-limited | `search_knowledge_base`, `find_titles`, `get_title`, `find_person`, `get_artist` | ≈$0 (one embedding, or a free API) |
| `https://smartmoviesearch.com/mcp/agent` | **OAuth 2.1 bearer** | `ask` (the full multi-agent pipeline) | one pipeline run (~$0.001) |

Built on the official Python SDK (`mcp` 2.x, `MCPServer`), Streamable HTTP, **stateless**
(no server-side session state, so nothing to hijack and nothing to clean up). It runs in
the existing FastAPI process, reusing its Milvus/TMDB/MusicBrainz clients.

## Threat model → controls

Early MCP shipped with no auth. Its well-known failures map one-to-one onto controls here:

| Threat (real-world precedent) | Control |
|---|---|
| **No authentication** on a server that spends money | The public endpoint spends nothing LLM-side. `ask` sits behind OAuth 2.1: the server is a pure *resource server*, and tokens come from an external authorization server (no home-made OAuth). |
| **Token passthrough / confused deputy:** a token for server A is accepted, or forwarded, by B | JWT verified against the issuer's JWKS: signature, `iss`, `exp`, **`aud` = this server's URL** (RFC 8707 resource indicator), and the required scope. The token is never forwarded; upstream APIs use our own keys. |
| **Discovery:** clients need to find the auth server | RFC 9728 Protected Resource Metadata at `/.well-known/oauth-protected-resource/mcp/agent`; a 401 carries a `WWW-Authenticate` pointer to it. |
| **DNS rebinding** against HTTP MCP servers (the MCP Inspector RCE) | `Host`/`Origin` allow-list (SDK transport security, on). The app binds to 127.0.0.1 behind nginx. |
| **Abuse / cost** | Per-IP rate limit (real IP from `CF-Connecting-IP`), a daily global cap, the existing IP blacklist, request-size cap, and a kill switch (`MCP_ENABLED=false`). |
| **Indirect prompt injection:** our results flow into *other people's* agents, and TMDB/MusicBrainz text is user-editable | Results are structured data, not prose. Text fields are stripped of HTML and invisible/bidi/tag Unicode (the hidden-instruction carriers), and truncated. Tool descriptions say returned text is third-party data. A red-team eval plants an injection and asserts it is neutralised. |
| **Tool poisoning / rug pull:** tool definitions change after the user approved them | Definitions are static code. A test pins them to a committed snapshot, so any change is a reviewed diff. All tools are annotated `readOnlyHint`, `openWorldHint`. |
| **Over-broad capability** | Read-only. No tool writes, deletes, fetches arbitrary URLs or runs code. |
| **Copyright** (Ebert reviews are third-party text) | Excerpts (one chunk) plus a link, never full reviews. |
| **Audit** | One log line per call: tool, latency, status and a **salted hash** of the IP (no raw IPs at rest). Counts on `/admin`. |

## Testing

- **T0 unit tests:** the JWT verifier (good token, wrong `aud`, wrong `iss`, expired, missing
  scope, bad signature) against a locally generated key/JWKS; sanitiser cases; the tool
  snapshot pin; the rate limiter.
- **T6 MCP eval tier (nightly):** a real MCP client over HTTP lists the tools, checks them
  against the snapshot, and calls each with a known query (e.g. `get_title` Inception →
  Nolan).

## Owner action

`/mcp/agent` stays unmounted until an authorization server is configured
(`MCP_OAUTH_ISSUER`, `MCP_OAUTH_AUDIENCE`, optional `MCP_OAUTH_JWKS_URL`). Any
standards-compliant provider works (e.g. WorkOS AuthKit or Auth0, both of which document
MCP setups). Choosing and signing up is the owner's call; the code is provider-agnostic.

## Public vs private

Tool code, the threat model and the snapshot are public, which is fine (security by
design, not obscurity). Logs, IP hashes and the salt are server-only.
