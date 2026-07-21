#!/usr/bin/env bash
# Public-repo guard: block commits/pushes that would publish secrets or private info.
#
# This repo (worldwidejimmy/pipeline) is PUBLIC. This scanner runs from the
# pre-commit and pre-push hooks (installed via `git config core.hooksPath .githooks`)
# and fails when the outgoing change contains:
#   1. files that should never be published (.env, keys, certs, archives, DBs,
#      the visitor-IP blacklist, ops logs)
#   2. added lines matching well-known secret formats (API keys, JWTs, private keys)
#   3. added lines matching PRIVATE patterns from ~/.config/sms-repo-guard/patterns.txt
#      — personal names/domains/server IPs live in that file, OUTSIDE the repo,
#      precisely so the blocklist itself is never published.
#
# Usage: guard-scan.sh <label> -- <git-diff-args...>
# Bypass (only with the repo owner's explicit say-so): git commit/push --no-verify
set -u

label="$1"; shift
[ "${1:-}" = "--" ] && shift

fail=0
say() { printf '🛑 public-repo guard (%s): %s\n' "$label" "$1" >&2; fail=1; }

# ── 1. Forbidden file types/names ─────────────────────────────────────────────
while IFS= read -r f; do
  base=$(basename "$f")
  case "$base" in
    .env.example|*.env.example) continue ;;
  esac
  case "$f" in
    *.pem|*.key|*.p12|*.pfx|*.crt|*.der|*id_rsa*|*id_ed25519*|*id_ecdsa*)
      say "key/cert material staged: $f" ;;
    .env|*.env|.env.*)
      say "dotenv file staged: $f" ;;
    *.tgz|*.tar.gz|*.tar|*.zip|*.age|*.gpg)
      say "archive/encrypted blob staged (backups don't belong here): $f" ;;
    *.sqlite|*.sqlite3|*.db)
      say "database file staged: $f" ;;
    *ip_blacklist*|*ops-logs*|*nightly-logs*)
      say "ops/visitor data staged (contains IPs/history): $f" ;;
  esac
done < <(git diff --name-only --diff-filter=ACMR "$@")

# ── 2. Secret-format patterns in ADDED lines ──────────────────────────────────
# .githooks/ is excluded from the FORMAT checks only (its detection patterns would
# match themselves); private patterns and filename rules still cover it.
added=$(git diff -U0 --diff-filter=ACMR "$@" -- ':(top,exclude).githooks' ':(top)' \
        | grep -E '^\+' | grep -vE '^\+\+\+' || true)
added_all=$(git diff -U0 --diff-filter=ACMR "$@" | grep -E '^\+' | grep -vE '^\+\+\+' || true)

check() {  # check <description> <extended-regex>
  local hits
  hits=$(printf '%s\n' "$added" | grep -nE -- "$2" | head -3 || true)
  if [ -n "$hits" ]; then
    say "$1:"
    printf '%s\n' "$hits" | sed 's/^/    /' >&2
  fi
}

check "private key block"           '-----BEGIN [A-Z ]*PRIVATE KEY-----'
check "Anthropic API key"           'sk-ant-[A-Za-z0-9_-]{20,}'
check "OpenAI-style secret key"     'sk-(proj-)?[A-Za-z0-9_-]{32,}'
check "Tavily API key"              'tvly-[A-Za-z0-9-]{20,}'
check "Brevo/Sendinblue key"        'xkeysib-[A-Za-z0-9-]{16,}'
check "GitHub token"                '(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
check "AWS access key id"           'AKIA[0-9A-Z]{16}'
check "Slack token"                 'xox[baprs]-[A-Za-z0-9-]{10,}'
check "JWT (e.g. TMDB bearer)"      'eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{10,}'
check "LangSmith key"               '(ls__|lsv2_[a-z]+_)[A-Za-z0-9]{20,}'
# Credential-ish assignments with a real-looking literal value (placeholders in
# .env.example and code reading the env are exempt).
cred_hits=$(printf '%s\n' "$added" \
  | grep -nE -- '(SMTP_PASS|SMTP_USER|PREVIEW_PASSWORD|ADMIN_EMAIL|API_KEY|BEARER_TOKEN)[[:space:]]*=[[:space:]]*[^[:space:]]{4,}' \
  | grep -ivE 'your_|_here|example|changeme|placeholder|environ|getenv|process\.env|\$\{|<' | head -3 || true)
if [ -n "$cred_hits" ]; then
  say "credential assignment with a real-looking value:"
  printf '%s\n' "$cred_hits" | sed 's/^/    /' >&2
fi

# ── 2b. Personal information (PII) in ADDED lines ─────────────────────────────
# Email addresses — anything outside the allowlist of intentionally-public /
# placeholder addresses is treated as personal info.
email_allow='users\.noreply\.github\.com|@example\.com|@yourdomain\.com|@smartmoviesearch\.com|@anthropic\.com|noreply@'
email_hits=$(printf '%s\n' "$added" \
  | grep -nE -- '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
  | grep -ivE "$email_allow" | head -3 || true)
if [ -n "$email_hits" ]; then
  say "email address not on the public allowlist (personal info?):"
  printf '%s\n' "$email_hits" | sed 's/^/    /' >&2
fi

# Phone-number formats (US-style; word-bounded to avoid version strings)
check "phone-number-looking string"  '\([0-9]{3}\) ?[0-9]{3}[-. ][0-9]{4}|(^|[^0-9.-])[0-9]{3}[-.][0-9]{3}[-.][0-9]{4}([^0-9.-]|$)|\+1[ .-]?[0-9]{3}[ .-]?[0-9]{3}[ .-]?[0-9]{4}'

# Box-default / hostname-revealing identities anywhere in content
check "server-identifying account/hostname" 'ubuntu@vps|root@vps|@vps-[0-9a-f]+|\.vps\.ovh\.'

# ── 3. Private local patterns (kept OUTSIDE the repo) ─────────────────────────
priv="$HOME/.config/sms-repo-guard/patterns.txt"
if [ -f "$priv" ]; then
  while IFS= read -r p; do
    case "$p" in ''|'#'*) continue ;; esac
    hits=$(printf '%s\n' "$added_all" | grep -inE -- "$p" | head -3 || true)
    if [ -n "$hits" ]; then
      say "matches a private pattern (see $priv):"
      printf '%s\n' "$hits" | sed 's/^/    /' >&2
    fi
  done < "$priv"
else
  printf '⚠️  public-repo guard: %s not found — only generic secret patterns active\n' "$priv" >&2
fi

if [ "$fail" -ne 0 ]; then
  printf '\n🛑 public-repo guard (%s): BLOCKED. This repo is public.\n' "$label" >&2
  printf '   Fix the flagged content, or — only with explicit owner approval — bypass with --no-verify.\n' >&2
fi
exit "$fail"
