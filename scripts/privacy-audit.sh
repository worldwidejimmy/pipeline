#!/usr/bin/env bash
# Privacy audit for this PUBLIC repo — run before pushing (and any time you want
# assurance nothing private is published).
#
#   scripts/privacy-audit.sh            # scan the whole current tree (HEAD content)
#   scripts/privacy-audit.sh --history  # also scan ALL git history (slower)
#
# Full-tree scan reuses .githooks/guard-scan.sh by diffing HEAD against the empty
# tree, so every tracked file's content passes through the SAME detectors the
# commit/push hooks use (secret formats, PII, forbidden files, private patterns).
# The --history pass greps every commit for the private patterns and secret
# formats — it can only REPORT (history is already published; see the note it
# prints on a hit), but it tells you exactly what a cloner can still read.
set -u

ROOT="$(git rev-parse --show-toplevel)"
GUARD="$ROOT/.githooks/guard-scan.sh"
EMPTY_TREE=4b825dc642cb6eb9a060e54bf8d69288fbee4904
PRIV="$HOME/.config/sms-repo-guard/patterns.txt"
rc=0

echo "🔎 privacy audit — full working tree (HEAD content)"
if [ -x "$GUARD" ]; then
  "$GUARD" privacy-audit -- "$EMPTY_TREE" HEAD || rc=1
else
  echo "⚠️  $GUARD not found/executable — cannot run full-tree scan" >&2
  rc=1
fi
[ "$rc" -eq 0 ] && echo "  ✓ tree clean"

if [ "${1:-}" = "--history" ]; then
  echo
  echo "🕓 privacy audit — full git history (report-only)"
  # Secret formats + the private patterns, across every commit's content.
  secret_re='sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|tvly-[A-Za-z0-9-]{20,}|xkeysib-[A-Za-z0-9-]{16,}|(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{10,}'
  revs=$(git rev-list --all)
  hist_hits=$(git grep -nI -E -- "$secret_re" $revs -- ':!.githooks' ':!*.env.example' 2>/dev/null \
              | grep -vE 'your_|_here|example|placeholder' | head -20)
  if [ -n "$hist_hits" ]; then
    echo "🛑 secret-format string(s) found in history:" >&2
    printf '%s\n' "$hist_hits" | sed 's/^/    /' >&2
    rc=1
  fi
  if [ -f "$PRIV" ]; then
    while IFS= read -r p; do
      case "$p" in ''|'#'*) continue ;; esac
      ph=$(git grep -niI -E -- "$p" $revs -- ':!.githooks' 2>/dev/null | head -6)
      if [ -n "$ph" ]; then
        echo "🛑 private pattern in history ($p):" >&2
        printf '%s\n' "$ph" | sed 's/^/    /' >&2
        rc=1
      fi
    done < "$PRIV"
  else
    echo "⚠️  $PRIV not found — history scan limited to generic secret formats" >&2
  fi
  if [ "$rc" -ne 0 ]; then
    echo >&2
    echo "ℹ️  History hits are ALREADY PUBLIC (anyone can 'git clone' and read them)." >&2
    echo "   Editing files only removes them from new commits. To purge from history you must" >&2
    echo "   rewrite it (git filter-repo) and force-push — a disruptive, owner-approved action" >&2
    echo "   — and rotate anything that was a live secret. IPs/hostnames: prefer firewall/CF." >&2
  else
    echo "  ✓ history clean"
  fi
fi

echo
if [ "$rc" -eq 0 ]; then
  echo "✅ privacy audit passed"
else
  echo "🛑 privacy audit FOUND ISSUES — do not push until resolved (or, for history, decide with the owner)."
fi
exit "$rc"
