# Remote printer connection architecture (agent-local model)

## Attribution

This design is directly inspired by **OctoEverywhere** by Quinn Damerell
(https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere), which is licensed
under AGPL-3.0 — the same license as OpenPrintHQ. We adopt its core architectural
approach and adapt the patterns to our stack. No source code is copied verbatim;
the implementation here is written for OpenPrintHQ's agent/engine/control-plane,
but the architecture — "the on-LAN agent holds the printer connection and relays
state + camera frames up over a single persistent tunnel" — is OctoEverywhere's.

Specific patterns we credit and adapt:
- Agent makes printer requests LOCALLY and relays results up, rather than the
  cloud reaching through to the printer. (cf. OctoEverywhere's `octohttprequest`
  `OctoHttpRequest.LocalHostAddress = "127.0.0.1"` and `webcamhelper`.)
- A local MQTT proxy/mux holds the single real MQTT session to a Bambu printer
  on the LAN and relays state up. (cf. OctoEverywhere's `mqttmux` /
  `mqttwebsocketproxy`.)
- Persistent tunnel with exponential backoff + jitter on reconnect, and
  ping/pong keepalive. (cf. OctoEverywhere's `octoservercon` / `pingpong`.)

Thank you to the OctoEverywhere project and the maker community.

## Why (the problem)

The previous model stood up raw per-port TCP relays (`openTcpRelay` →
`RELAY_HOST:39000+`) and had the CLOUD engine run the printer session end-to-end
THROUGH the relay. Two consequences:

1. **Online/offline flip-flop.** The cloud engine's MQTT (Bambu) / Moonraker
   (Klipper) session traversed the relay, so relay latency/jitter/brief drops
   crossed the engine's staleness threshold and status oscillated.
2. **Cameras unreachable.** The camera was never relayed (and RTSPS-over-TLS to a
   printer cert can't be transparently TCP-relayed to a different host anyway), so
   go2rtc / snapshot polling dialed the raw LAN IP from the cloud and got nothing.

## The model (what changes)

The on-LAN **agent** (connector) holds the stable, local connection to each
printer and relays UP:

- **Camera:** the agent runs a local go2rtc that holds ONE RTSPS pull from the
  Bambu (port 322, on-LAN where TLS + reachability are fine) and re-serves MJPEG /
  single-frame JPEG over plain HTTP. The cloud fetches those frames over the
  existing HTTP tunnel. Klipper webcams (MJPEG on the host) are fetched locally by
  the agent the same way. No cloud->printer camera path.
- **Klipper status:** the agent holds the Moonraker connection locally and relays
  status up (it already probes :7125 during discovery).
- **Bambu status:** the agent holds the local MQTT session to each Bambu and
  relays state up (mux pattern), so tunnel jitter can't drop the printer session.

The cloud engine consumes relayed state/frames instead of dialing the LAN. Raw
per-port TCP relays are retired for migrated paths (FTP file transfer may remain
tunneled as before).

---

## Transport (added 2026-08)

The agent-local model above says *what* runs where. This section is *how* the
agent and the cloud talk, and why the first two attempts were wrong.

### What was tried and rejected

**Broker / rendezvous (31 July, reverted).** The connector listened on an
inbound port (default 16384) and the cloud engine dialled it. That reads well on
a whiteboard and cannot work for the deployment this product exists to serve:
printers on a home LAN behind CGNAT have no inbound path and the user cannot
forward a port they don't control. Reverted in full; the implementation remains
reachable at `2c368fa` (control-plane) and `5332302` (client) if the stream
framing is ever wanted.

**SSE + POST-per-result (the original).** Workable but two costs showed up as
"the connector keeps dropping":

1. Every upstream message opened a fresh TCP+TLS connection. A single discovery
   scan made ~250 of them in about a second.
2. Every byte of printer traffic — MQTT, FTP, camera frames — was base64'd into
   one ordered event stream. A large transfer sat in front of a status poll until
   the engine's staleness threshold fired and the UI declared the printer offline.

### Current: one multiplexed WebSocket

`GET /api/connector/ws` carries the same job protocol over one reused,
**outbound-only** connection. Control messages are JSON text frames; bulk TCP
payloads are binary frames chunked to 16 KB, so transfers interleave with polls
instead of blocking them and nothing pays the base64 tax.

Both transports share `connectorFor` / `proxyViaConnector` / `openTcpStream`
unchanged — a session exposes `send()` and an optional `sendData()`, and SSE
supplies only the former. One code path for routing, signing and multi-site
selection rather than two that drift.

Backwards compatible by design: SSE and `/api/connector/result` still work, and
`/api/pub/config` advertises `connector_ws` so an agent knows before it probes.
An agent that cannot upgrade falls back automatically; `OPHQ_DISABLE_WS=1`
forces the legacy path.

**`openTcpRelay` is still live and is not the retired broker.** `routing.js`
uses it to give the cloud engine a local TCP endpoint that tunnels through the
connector. Only the *inbound* broker listener was removed.

### Things that bite at the edge

Both transports are long-lived, and every proxy in front of them must be told so.
Two separate outages traced to exactly this:

- `proxy_buffering off` — otherwise SSE events arrive batched.
- `proxy_read_timeout` well above the heartbeat — the default closes the stream
  and the client reports it as the tunnel dropping.
- `Connection $connection_upgrade`, never a hardcoded value. Pinning it to `""`
  silently blocks the WebSocket upgrade; pinning it to `upgrade` breaks ordinary
  keep-alive. `infra/router/nginx.conf` has the map.

## Camera (added 2026-08)

Frame relay over the tunnel (above) still works and remains the fallback. In
addition, connector-routed printers can negotiate **WebRTC directly with the
browser**: the control-plane relays only the SDP offer/answer to the connector's
own go2rtc, and media then flows browser ↔ connector without crossing the cloud
host at all.

Direct paths frequently fail on CGNAT, which is the same population this whole
design targets, so a TURN relay is the fallback. Credentials are Cloudflare
Realtime, held in owner settings (encrypted at rest, write-only over the API),
with STUN always available and unmetered. **Without TURN configured, remote
cameras work on permissive networks and fail on strict ones** — that is the
expected behaviour, not a bug.

## What is deliberately NOT retired yet

- **The cloud go2rtc.** It still serves local-mode printers, which the cloud can
  reach directly. Retiring it waits on the connector-side WebRTC path having
  actually carried traffic.
- **The SSE transport.** Kept until no deployed client needs it.

## Testing note

Handshake probes are not sufficient. A bug where `proxyViaConnector` wrote to
`target.raw` directly shipped to all three tiers with every endpoint check
green: the upgrade succeeded, the connector reported online, and every job
failed. `e2e/live-connector-check.sh` attaches a real agent and makes it do
work; it is a gate in `promote.sh`.

## Tiers (updated 2026-08)

The dev tier was retired. Images are built on the test VM and pushed straight to
`:test`, so **test is where unvalidated code lands** and prod is the only thing
promotion protects.

    build on test  ->  :test  ->  deploy test  ->  gates  ->  promote to prod

That is one gate rather than two. It was a deliberate trade: dev existed mostly
to be a build host, and running a whole extra tier to protect a tier that is
itself disposable was not worth a machine. The gate that matters -- the one in
front of prod -- is unchanged, and it still runs the full Playwright suite plus
a real agent attaching over the tunnel and completing a job.

Practical consequences:

- `promote.sh test-to-prod` is the only promotion. `dev-to-test` is accepted as
  an alias and does the same thing.
- The regression suite runs against test, so the `ophq-e2e` tenant lives there
  now. It needs a camera-backed printer or the browser camera tests silently
  skip and camera rendering stops being checked at all.
- Multi-arch builds are mandatory: prod is aarch64. The build host must be a VM,
  not an LXC container, because binfmt_misc cannot be registered inside LXC.
