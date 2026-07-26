# OpenPrintHQ Local Connector

A tiny agent you run **on the same network as your printers**. It gives your
cloud-hosted OpenPrintHQ a way to reach those printers **without opening any
ports** on your router — so it works behind a home router, a strict company
firewall, or carrier-grade NAT (CGNAT), where inbound port-forwarding is
impossible.

## How it works

The connector never listens for inbound connections. Instead it dials **out**
to your OpenPrintHQ instance and holds the connection open:

```
   Your LAN                              Cloud
 ┌───────────────┐   outbound HTTPS   ┌──────────────────┐
 │  connector    │ ─────────────────► │  control-plane   │
 │  agent        │ ◄───── jobs ────── │  (SSE stream)    │
 │      │        │                    └──────────────────┘
 │      ▼ local  │
 │  printers     │   The agent performs each requested HTTP call against a
 │  192.168.x.x  │   printer on your LAN and posts the response back up.
 └───────────────┘
```

1. The agent opens a long-lived **Server-Sent-Events** stream to
   `GET /api/connector/stream` (authenticated with a connector token).
2. The control-plane pushes **jobs** down that stream — each is one HTTP request
   to perform against a printer (e.g. `GET http://10.10.10.121:7125/printer/info`).
3. The agent runs the request locally and `POST`s the result back to
   `/api/connector/result`.

Because the tunnel is **outbound-only**, no firewall changes, no port-forward,
and no public IP are required.

## Security

- The agent authenticates with a **connector token** you create in the web UI
  (Settings → Connectors). Revoke it there at any time.
- The agent will only talk to hosts/ports on its **allow-list** — by default the
  private RFC1918 ranges plus common printer/camera ports. A compromised
  control-plane therefore can't use your connector to reach arbitrary hosts
  (SSRF protection). Tighten it with `OPHQ_ALLOW` / `OPHQ_ALLOW_PORTS`.
- All traffic to the cloud is HTTPS.

## Requirements

- **Node.js ≥ 20** (for the bare-metal installs) — the agent has **zero npm
  dependencies**, it uses only Node built-ins.
- Or **Docker** (no Node needed on the host).

## Install

First create a connector token: **Settings → Connectors → New connector**, and
copy the token.

### Docker (any OS)

```bash
cp .env.example .env         # set OPHQ_CONTROL_URL + OPHQ_CONNECTOR_TOKEN
docker compose up -d
docker logs -f openprinthq-connector
```

### Linux (systemd)

```bash
sudo mkdir -p /opt/openprinthq-connector
sudo cp -r src package.json /opt/openprinthq-connector/
sudo cp .env.example /etc/openprinthq-connector.env   # edit it
sudo cp packaging/systemd/openprinthq-connector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openprinthq-connector
journalctl -u openprinthq-connector -f
```

### macOS (launchd)

```bash
mkdir -p "$HOME/Library/Application Support/openprinthq-connector"
cp -r src package.json "$HOME/Library/Application Support/openprinthq-connector/"
cp packaging/launchd/org.openprinthq.connector.plist ~/Library/LaunchAgents/
# edit the plist: set OPHQ_* values, node path, and REPLACE_WITH_HOME
launchctl load ~/Library/LaunchAgents/org.openprinthq.connector.plist
tail -f /tmp/openprinthq-connector.log
```

### Windows 11 (PowerShell, as Administrator)

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\install-service.ps1 `
  -ControlUrl "https://openprinthq.example.org" `
  -Token "<connector token>" -Name "windows-pc"
```

### Quick manual test (any OS)

```bash
OPHQ_CONTROL_URL=https://openprinthq.example.org \
OPHQ_CONNECTOR_TOKEN=<token> \
node src/agent.js
```

You should see `connected … — waiting for jobs`, and the connector appears as
**online** in Settings → Connectors.

## Configuration

See `.env.example` for every option (`OPHQ_ALLOW`, `OPHQ_ALLOW_PORTS`, …).

## Status / roadmap

- **HTTP(S) proxying** — covers Klipper/Moonraker printers and HTTP/MJPEG
  cameras. (`proxyViaConnector()`.)
- **Raw TCP tunnelling** — multiplexed bidirectional byte streams carry *any*
  TCP protocol through the connector, including Bambu MQTT (8883) and FTP (990).
  (`openTcpStream()`; agent `tcp-open`/`tcp-data`/`tcp-close`.) Verified with a
  live Moonraker request round-tripped over a raw TCP stream.
- **Per-printer routing** — each printer can be set to *Direct* or *via a
  connector* in Settings → Connectors (stored as `printer_automation.connector_id`).
- **Next:** auto-activation — when a printer is set "via connector", have the
  control-plane stand up a persistent local relay and point that printer's
  engine connection at it (opt-in, so existing local printers are never
  silently rerouted).
