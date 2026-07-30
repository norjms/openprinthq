#!/bin/bash
# Run the OpenPrintHQ e2e regression suite against a deployed instance using the
# official Playwright docker image (browsers preinstalled — no host pollution).
# The public hostnames are resolved to npmplus so the tests exercise the REAL
# reverse-proxy + Authentik forward-auth path, not just the app in isolation.
#
#   BASE_URL                which tier to hit (default: dev)
#   NPM_IP                  npmplus LXC IP that terminates TLS + forward-auth
#   OPHQ_DEV_LOGIN_SECRET   shared secret for the dev/test auth bypass (enables
#                           the authenticated specs; omit on tiers without it)
#   TEST_USER_EMAIL         e2e user (default e2e@openprinthq.test)
set -euo pipefail
BASE_URL="${BASE_URL:-https://internal.example.com}"
NPM_IP="${NPM_IP:-10.10.10.9}"
IMAGE="${PW_IMAGE:-mcr.microsoft.com/playwright:v1.55.0-noble}"
HERE="$(cd "$(dirname "$0")" && pwd)"
echo "== e2e: $BASE_URL  (npmplus $NPM_IP, image $IMAGE) =="
exec sudo docker run --rm \
  --add-host internal.example.com:"$NPM_IP" \
  --add-host internal.example.com:"$NPM_IP" \
  --add-host openprinthq.com:"$NPM_IP" \
  --add-host internal.example.com:"$NPM_IP" \
  -e BASE_URL="$BASE_URL" -e CI=1 \
  -e OPHQ_DEV_LOGIN_SECRET="${OPHQ_DEV_LOGIN_SECRET:-}" \
  -e TEST_USER_EMAIL="${TEST_USER_EMAIL:-e2e@openprinthq.test}" \
  -v "$HERE":/e2e -w /e2e \
  "$IMAGE" \
  sh -c "npm ci --no-audit --no-fund && npx playwright test"
