# Hosting / CDN / DNS / registrar vendor names that must not appear in DOCS or commit
# messages: docs describe the deployment generically ("cloud VPS", "CDN/WAF proxy"), because
# naming the providers tells an attacker where to look. Sourced by guard-scan.sh (docs) and
# commit-msg. Code is exempt: it has to use real identifiers (e.g. a CDN's client-IP header).
# Add new vendors here; extended regex, matched case-insensitively.
INFRA_VENDORS='cloudflare|\bovh(cloud)?\b|hetzner|digital ?ocean|linode|vultr|akamai|fastly|scaleway|contabo|ionos|godaddy|namecheap|porkbun|bunny\.net|cf-connecting-ip|cf-ray'
