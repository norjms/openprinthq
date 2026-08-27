#!/bin/sh
# Select the OpenPrintHQ theme by default, then hand off to the upstream start.
#
# The theme is read from localStorage at first paint, so a user who has never
# visited would otherwise land on the upstream default and flash to ours. This
# patches the bootstrap default rather than writing storage from outside, which
# a cross-origin parent frame cannot do anyway.
#
# Idempotent, and it never fails the container: an upstream change to either
# line should degrade to the stock theme, not stop a tenant's library starting.
# Note the sed delimiter is '#', because the theme line itself contains '||'.
set -e
INDEX=/app/public/index.html
if [ -f "$INDEX" ] && ! grep -q 'ophq-theme.css' "$INDEX"; then
  sed -i "s#getItem('gv_theme') || 'glass'#getItem('gv_theme') || 'openprinthq'#g" "$INDEX" || true
  sed -i 's#<link rel="stylesheet" href="/css/style.css">#<link rel="stylesheet" href="/css/style.css">\n  <link rel="stylesheet" href="/css/ophq-theme.css">#' "$INDEX" || true
  if grep -q 'ophq-theme.css' "$INDEX"; then echo "openprinthq theme applied"; else echo "WARNING: theme not applied, upstream markup changed"; fi
fi
# Upstream ships its own entrypoint; keep it in the chain rather than replacing it.
exec docker-entrypoint.sh "$@"
