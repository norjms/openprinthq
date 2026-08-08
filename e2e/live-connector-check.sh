#!/usr/bin/env bash
# Attach a REAL connector agent to a tier and make it do work over the tunnel.
#
# This exists because handshake probes are not enough. A bug where
# proxyViaConnector wrote to target.raw directly shipped to all three tiers:
# the upgrade succeeded, the connector reported online, every endpoint probe
# was green, and every job failed. Nothing that tests only the connection can
# catch that class of bug — you have to attach an agent and ask it to do work.
#
# Usage: BASE_URL=https://openprinthq-dev.nnlink.org NPM_IP=192.168.1.9 \
#        OPHQ_DEV_LOGIN_SECRET=... ./live-connector-check.sh
set -euo pipefail

BASE_URL="${BASE_URL:?set BASE_URL}"
HOST_ONLY="$(echo "$BASE_URL" | sed -E 's#https?://##; s#/.*##')"
CLIENT_REF="${CLIENT_REF:-main}"
WORK="$(mktemp -d)"
JAR="$WORK/cookies.txt"
AGENT_PID=""
CID=""
finish() {
  [ -n "$AGENT_PID" ] && kill "$AGENT_PID" 2>/dev/null || true
  [ -n "${AGENT_CT:-}" ] && docker rm -f "$AGENT_CT" >/dev/null 2>&1 || true
  [ -n "$CID" ] && cj -o /dev/null -X DELETE "$BASE_URL/api/connectors/$CID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap finish EXIT

RESOLVE=()
[ -n "${NPM_IP:-}" ] && RESOLVE=(--resolve "$HOST_ONLY:443:$NPM_IP")
cj() { curl -sS "${RESOLVE[@]}" -b "$JAR" -c "$JAR" "$@"; }

echo "== signing in =="
# This gate needs dev-login, which exists only on dev and test. On prod it is
# deliberately off, so skip cleanly rather than reporting a failure that is
# really "this check does not apply here".
LOGIN_CODE=$(cj -o /dev/null -w '%{http_code}' -X POST "$BASE_URL/api/auth/dev-login" \
   -H 'content-type: application/json' \
   ${OPHQ_DEV_LOGIN_SECRET:+-H "x-ophq-dev-login: $OPHQ_DEV_LOGIN_SECRET"} \
   -d "{\"email\":\"${TEST_USER_EMAIL:-e2e@openprinthq.test}\"}" || echo 000)
case "$LOGIN_CODE" in
  2*) ;;
  *) echo "SKIP: dev-login unavailable on $BASE_URL (HTTP $LOGIN_CODE)."
     echo "      This gate runs on dev/test; prod is covered by the tier it was promoted from."
     exit 0;;
esac

echo "== creating a throwaway connector =="
NAME="livecheck-$$"
CREATE=$(cj -X POST "$BASE_URL/api/connectors" -H 'content-type: application/json' -d "{\"name\":\"$NAME\"}")
TOKEN=$(echo "$CREATE" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("token",""))
except Exception: print("")')
[ -n "$TOKEN" ] || { echo "FAIL: no connector token returned. Response was:"; echo "$CREATE" | head -c 300; exit 1; }
CID=$(cj "$BASE_URL/api/connectors" | python3 -c "
import sys,json
print(next((c['id'] for c in json.load(sys.stdin) if c['name']=='$NAME'),''))")
echo "   connector id=$CID"

echo "== fetching the agent ($CLIENT_REF) =="
git clone -q --depth 1 --branch "$CLIENT_REF" \
  https://github.com/norjms/openprinthq-cloud-client.git "$WORK/client"

echo "== attaching the agent =="
# Run the agent in a container: the operator hosts don't all have node, and
# this pins the runtime to the version the client actually ships with.
AGENT_CT="livecheck-agent-$$"
finish_agent() { docker rm -f "$AGENT_CT" >/dev/null 2>&1 || true; }
# The agent fails closed without a pinned command-signing key, which is correct:
# an unpinned connector would execute unauthenticated commands against someone's
# LAN. Give it a writable path so it pins on first run, exactly as a real install
# does, rather than disabling the check with OPHQ_ALLOW_UNSIGNED and testing a
# configuration no user should ever run.
docker run -d --name "$AGENT_CT" \
  ${NPM_IP:+--add-host "$HOST_ONLY:$NPM_IP"} \
  -v "$WORK/client:/client:ro" -v "$WORK:/state" \
  -e OPHQ_CONTROL_URL="$BASE_URL" -e OPHQ_CONNECTOR_TOKEN="$TOKEN" \
  -e OPHQ_CONNECTOR_NAME="$NAME" -e OPHQ_CLIENT_KEY_FILE="/state/key.pem" \
  -e OPHQ_SIGNING_PUBKEY_FILE="/state/signing.pem" \
  -e NODE_TLS_REJECT_UNAUTHORIZED=0 \
  node:22-alpine node /client/agent/src/agent.js >/dev/null
AGENT_PID=""

for i in $(seq 1 30); do docker logs "$AGENT_CT" 2>&1 | grep -q "connected to" && break; sleep 1; done
AGENT_LOG="$(docker logs "$AGENT_CT" 2>&1)"
echo "$AGENT_LOG" | grep -q "connected to" || { echo "FAIL: agent never connected"; echo "$AGENT_LOG" | head -30; exit 1; }

TRANSPORT=multiplexed
echo "$AGENT_LOG" | grep -q "compatibility stream" && TRANSPORT=legacy-SSE
echo "   agent connected over: $TRANSPORT"

echo "== making it actually DO something (discovery job over the tunnel) =="
RESP=$(cj -X POST "$BASE_URL/api/connectors/$CID/discover" \
       -H 'content-type: application/json' -d '{"window_ms":4000}')
echo "   response: $(echo "$RESP" | head -c 200)"

case "$RESP" in
  *"connector write failed"*) echo "FAIL: job could not be written to the transport"; exit 1;;
  *"connector timeout"*)      echo "FAIL: job was written but never answered"; exit 1;;
  *"connector offline"*)      echo "FAIL: control-plane thinks the agent is offline"; exit 1;;
esac
echo "$RESP" | python3 -c 'import sys,json;json.load(sys.stdin)' >/dev/null 2>&1 \
  || { echo "FAIL: job response was not valid JSON"; exit 1; }

echo
echo "PASS: agent attached over $TRANSPORT and completed a real job."
