# OpenPrintHQ: Broker / Rendezvous Architecture (direct client connection)

Replaces the cloud-proxied tunnel (see docs/tickets/relay-tunnel-no-data.md) with a
rendezvous model. The cloud never carries printer bytes; it only brokers
connection info. The browser talks directly to the client app, which talks to the
printers on its LAN.

## The triangle

    (1) Cloud broker (static IP: openprinthq.com)
              /\
             /  \  registers address              serves address
            /    \  + printer list                 to browser
           /      \
   (2) Client app  ------ direct connection ------  (3) Browser
   (printer LAN)          (data path, no cloud)     (anywhere)
        |
        | LAN
     printers (Voron Moonraker :7125, Bambu MQTT :8883 / RTSPS :322, etc.)

- (1) Cloud = broker only. Records, per client: public endpoint (host:port) and the
  printers it fronts. Hands that to authorized browsers. Zero printer bytes transit
  the cloud.
- (2) Client app = a small local gateway. Opens a listening port (port-forwarded
  from the router, or the client is on a reachable host). Registers its
  public endpoint + printer inventory with the cloud. On an inbound browser
  connection, bridges to the requested local printer (proxying Moonraker HTTP/WS,
  Bambu MQTT/RTSPS, camera streams).
- (3) Browser = fetches the client endpoint from the cloud, connects directly to the
  client for control + camera. The web app points its API/WS/stream URLs at the
  client endpoint, not the cloud.

## Why this is better than the tunnel
- No byte-forwarding through the cloud = no cloud bandwidth cost, lower latency.
- Camera streaming works directly (browser <-> client go2rtc), which the tunnel
  could not do well.
- Simpler failure model: if the client is reachable, it works; if not, it doesn't
  (no silent half-open tunnels).

## Accepted limitation
- Direct connect requires the client to be reachable from the browser: a
  port-forward on the router, or browser+client on the same network / VPN.
- Will NOT traverse CG-NAT (carrier-grade NAT) - no inbound port possible. Accepted
  for now. (A future fallback could re-introduce an optional relay for CG-NAT users,
  but that is explicitly out of scope here.)

## Components to build

### A. Client app (openprinthq-cloud-client)
1. Local gateway listener on a configurable port (default e.g. 8787), bound to
   0.0.0.0 so a router port-forward can reach it.
2. Registration: on start and periodically, POST to the cloud broker:
   { public_host, public_port, printers: [{id, vendor, ip, ports...}] }.
   - public_host: either user-provided (their DDNS / public IP) or discovered.
   - public_port: the externally-forwarded port that maps to the gateway listener.
3. Reverse proxy / bridge per protocol:
   - Moonraker: proxy HTTP + WebSocket to printer_ip:7125.
   - Bambu: MQTT over the printer's :8883 (TLS) and camera via local go2rtc
     (RTSPS :322 -> MJPEG/JPEG), re-served to the browser.
   - Generic TCP passthrough where needed.
4. Auth: the browser must present a token/short-lived credential the cloud issued,
   so the open gateway port is not world-usable. Client verifies it before bridging.

### B. Cloud broker (openprinthq control-plane)
1. Registration endpoint: client posts its public endpoint + printer inventory.
   Store per user/connector. TTL / heartbeat so stale endpoints expire.
2. Lookup endpoint: browser asks "endpoint for printer X" -> returns
   { host, port, token }. Authz: only the owning user.
3. Issue short-lived browser tokens the client can verify (shared secret or signed).
4. Remove/retire the openTcp relay machinery from the hot path (keep behind a flag
   for a possible future CG-NAT fallback, or delete).

### C. Web app (openprinthq web)
1. When a printer is "direct" (has a client endpoint), point its Moonraker/MQTT/
   camera URLs at the client endpoint (host:port) + token, not the cloud.
2. Fall back to "unreachable" state cleanly when no endpoint is registered.

### D. Engine (openprinthq-engine)
- The engine currently connects to printers for status/print. In the broker model the
  engine either (a) also connects via the client's public endpoint, or (b) the
  browser-driven direct path handles live view while the engine keeps its own path.
  Decide during implementation: simplest is the engine connects to the client
  endpoint the same way the browser does. Revisit hysteresis (it was compensating for
  the flaky tunnel; with a stable direct connection much of it may be unnecessary).

## Migration / rollout
- Build client first (gateway + registration), then broker endpoints, then web wiring.
- Test on the LAN (browser + client same network) first, then via port-forward.
- Keep the cloud tunnel code dormant (flagged off) until the broker path is proven.
