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
