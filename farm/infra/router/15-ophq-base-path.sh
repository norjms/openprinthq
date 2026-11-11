#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Writes the router's configuration for OPHQ_BASE_PATH, the path the web image
# was built for (for example /apps/farm; empty for the root). Runs before the
# image's own template step, so a configuration supplied in
# /etc/nginx/templates (as the ArtisanHub installer does) still wins.
set -eu

if ls /etc/nginx/templates/*.template >/dev/null 2>&1; then
    echo "$0: using the configuration in /etc/nginx/templates"
    exit 0
fi

base="${OPHQ_BASE_PATH:-}"
base="${base%/}"
if [ -n "$base" ] && ! printf '%s\n' "$base" | grep -Eq '^(/[A-Za-z0-9._-]+)+$'; then
    echo "$0: OPHQ_BASE_PATH must be empty or a path like /apps/farm, not '$base'" >&2
    exit 1
fi

conf=/etc/nginx/conf.d/default.conf
sed "s#@BASE@#${base}#g" /etc/nginx/ophq.conf.in > "$conf"
if [ -n "$base" ]; then
    # Outside the path: the root goes to the app, the bare path gets its
    # slash, and nothing else is served.
    sed -i "s|#@OUTSIDE_BASE@|location = / { return 302 ${base}/; }\n    location = ${base} { return 301 ${base}/; }\n    location / { return 404; }|" "$conf"
fi
echo "$0: serving under '${base:-/}'"
