<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# OpenPrintHQ Cloud Client & Local Connectors

Your OpenPrintHQ account runs in the cloud, but your printers live on a private
network (home, shop, office) behind a router. The **Cloud Client** is a small app
you install on a computer at that site. It dials **out** to your OpenPrintHQ
instance and opens a secure tunnel, so the cloud can reach your printers without
any port-forwarding, VPN, or opening your firewall. Nothing listens for inbound
connections on your network.

Each site you run a Cloud Client on is called a **connector**.

---

## Quick start

1. In OpenPrintHQ, open **Settings → Connectors** and click **+ New connector**.
   Give it a site name (e.g. `home-lab`). A **connector token** is shown once —
   this is the site's key.
2. Install the **Cloud Client** on a computer at that site
   (Windows `.msi`, macOS `.pkg`, Linux `.deb`/`.rpm`, or the Docker image).
3. Open the Cloud Client, paste your instance URL (e.g. `https://your-instance`)
   and the connector token, give it a name, and click **Save & connect**.
4. The status pill turns green (**connected**). That's it — no keys to copy.
5. Back in OpenPrintHQ, the connector shows **online** and **🔒 paired**.

You can now add printers on that site (see *Adding printers*, below).

---

## Pairing: trust-on-first-use ("sticky" key)

For security, a connector is more than just its token. The Cloud Client also
generates its own private **client key** and keeps it on that computer. This
means a leaked token alone is not enough to impersonate your connector.

Pairing works like SSH host keys or a "sticky MAC":

- **First connect wins.** The first Cloud Client that connects with a connector's
  token automatically registers ("locks onto") its client key. You'll see the
  connector flip from **🔓 awaiting first client** to **🔒 paired**. There is
  nothing to copy or paste.
- **Locked after that.** Once paired, the connector accepts **only** that client.
  If a different computer tries to connect with the same token, it is rejected —
  even though the token is correct. This is deliberate: it stops a stolen token
  from being used elsewhere.
- **Replacing the client.** If you reinstall the Cloud Client, move it to a new
  computer, or otherwise need to pair a *different* client, click **Reset key**
  on that connector in **Settings → Connectors**. That unpairs the current
  client; the **next** client to connect with the token locks in.

> Rule of thumb: one connector token = one client. Reset the key when you swap
> the client; revoke the connector entirely when you retire the site.

If you ever need the client key manually (advanced setups, or to pre-register
it), the Cloud Client's **Copy connector key** button puts the public key on your
clipboard, and **Settings → Connectors → Key** accepts a pasted key.

---

## Multiple sites

You can run **more than one connector at the same time** — one per site. Create a
separate connector (and token) for each network, install the Cloud Client at each
site, and each pairs independently.

When you add a printer, you choose **which site** it's on. Its traffic is then
tunnelled through that site's connector. If a site has more than one connector
online, the first one wins for that site.

---

## Finding printers (LAN discovery)

The **Scan** button in OpenPrintHQ used to run on the cloud engine, which cannot
see your private network — so it never found LAN-only printers. Discovery now
runs **through the connector, on the site's own network**, where the printers
actually are.

- In **Settings → Connectors**, click **Scan LAN** on a connector to list the
  printers announcing themselves on that site (Bambu Lab printers in LAN mode are
  detected via SSDP). Each result has an **Add** button.
- When adding a printer, pick the **Site** and click **Scan this site** to
  discover and pre-fill it.

The connector must be **online** to scan. If a printer doesn't appear, confirm
it's powered on and in **LAN mode**, then scan again.

---

## Checking for updates

The Cloud Client's **Check for updates** compares your installed version against
the latest published release and, if newer, opens the releases page. Beta builds
are published as pre-releases and are included in the check.

---

## Troubleshooting

Turn on **Verbose debug logging** (Cloud Client → *Advanced*), click **Start**,
then **View logs** to see every job the connector receives and every request it
makes on your LAN. On the server side, an operator can set `OPHQ_DEBUG=1` on the
control-plane to trace the connector ↔ cloud ↔ engine path end to end.

| Symptom | Likely cause / fix |
|---|---|
| Client won't connect, token is correct | The connector is **paired to a different client**. Click **Reset key** in Settings → Connectors, then reconnect. |
| Connector shows offline | The Cloud Client isn't running at that site, or the computer is off/asleep. |
| Scan finds nothing | Connector offline, printers not in LAN mode, or they're on a different subnet than the connector's computer. |
| Printer added but not reachable | Confirm the printer's **Site** is set correctly in Settings → Connectors (routing), and that site's connector is online. |

---

## Security notes

- The tunnel is **outbound-only** — your network accepts no inbound connections.
- The connector enforces an **allow-list** of hosts/ports it will reach on your
  LAN (defaults to private ranges + common printer ports); tighten it in
  *Advanced* if you like.
- **Command signing** (optional but recommended): generate a signing key in
  Settings → Connectors and set it on the client so the connector only executes
  commands signed by your instance.
- Client keys are stored locally on the connector computer and never leave it;
  only the **public** key is registered with your instance.
