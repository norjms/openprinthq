// OpenPrintHQ control-plane — connector auto-activation (vendor-agnostic routing)
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Copied to control-plane/src/routing.js. When a printer is set "via connector"
// this module stands up one stable local TCP relay per endpoint the printer's
// vendor needs, tunnels each through the connector, and repoints the engine at
// the relays. Setting it back to "Direct" restores the real address and drops
// the relays.
//
// Vendor-agnostic: each printer type is described by a CONNECTION PROFILE below
// (the endpoints it exposes + how to write/restore the engine's address fields).
// Adding a vendor = add a profile entry, and — if it introduces a new address
// field — teach the engine to honour `endpoint_overrides` for that role. The
// engine keeps `ip_address` as the (relay) host and reads a per-role PORT from
// `endpoint_overrides`, so a single relay host serves every port a printer uses.
import { getInstanceForUser, getAutomation, setRouteDirect, listActiveRoutes, setPlugRoute, getPlugRoutes } from './db.js';
import { openTcpRelay, closeRelay, connectorOnline, RELAY_HOST, relayPort, proxyViaConnector, onConnectorOnline } from './connector.js';
import { dbg } from './debuglog.js';

const ENGINE_PORT = 8000;
const engineBase = (inst) => (inst && inst.subdomain ? `http://ophq-${inst.subdomain}:${ENGINE_PORT}` : null);
const MAX_ENDPOINTS = 8;   // relay-port slots reserved per printer
const CP_PORT = Number(process.env.PORT || 8080);   // control-plane port on the docker net

async function eng(base, path, opts = {}) {
  const headers = { accept: 'application/json' };
  if (opts.body !== undefined) headers['content-type'] = 'application/json';
  const res = await fetch(base + path, { ...opts, headers });
  if (!res.ok) throw Object.assign(new Error(`engine ${res.status}`), { status: res.status });
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

// ---- vendor connection profiles -----------------------------------------
// endpoints: the printer ports that must be reachable, in a stable order.
// apply(ports): the engine PATCH that points the printer at the relays
//   (ports maps role -> allocated relay port).
// restore(direct): the engine PATCH that puts it back on its real address.
// Each profile touches ONLY the fields ITS printer uses. Combined with the
// per-printer relay ports (relayPort = 39000 + printerId*10 + i) and per-printer
// engine PATCH, this guarantees routing one printer — or adding a brand — never
// affects any other printer of any brand.
//
// Single-endpoint transports (Moonraker/Klipper, OctoPrint, PrusaLink, Duet,
// FlashForge, MKS, Snapmaker) all connect on one port the engine reads from
// `moonraker_port`, so they need only a profile — no engine change.
// devicePort: the port ON THE PRINTER. It must not be read back from the
// printer record, because activateRoute overwrites moonraker_port with the
// RELAY port. Reading it again on reconcile pointed the tunnel at
// <printer-ip>:<relay-port>, where nothing listens, and the printer silently
// went offline and stayed there. printer_automation.direct_port holds the real
// device port and is never rewritten, so prefer it.
function singleEndpoint(defaultPort) {
  return (p, devicePort) => ({
    endpoints: [{ role: 'api', port: Number(devicePort) || defaultPort }],
    apply: (ports) => ({ ip_address: RELAY_HOST, moonraker_port: ports.api }),
    restore: (d) => ({ ip_address: d.host, moonraker_port: Number(d.port) || defaultPort })
  });
}
const PROFILES = {
  klipper: singleEndpoint(7125),
  octoprint: singleEndpoint(80),
  prusalink: singleEndpoint(80),
  duet: singleEndpoint(80),
  flashforge: singleEndpoint(8899),
  mks: singleEndpoint(8080),
  snapmaker: singleEndpoint(8080),
  // Bambu is multi-endpoint. MQTT (status/control, 8883) and FTP (file
  // browse/upload/download, 990) are both wired end-to-end: the engine reads a
  // per-role port from `endpoint_overrides` (mqtt via BambuMQTTClient, ftp via
  // BambuFTPClient's ftp_port, threaded through every FTP call site). Camera is
  // still LAN-only — live video over the control tunnel is impractical (see the
  // camera note below); it degrades to "unavailable while remote" rather than
  // break. A non-routed Bambu always resolves to native ports (overrides null),
  // so adding/removing a route never affects an unrouted printer of any brand.
  bambu: () => ({
    endpoints: [
      { role: 'mqtt', port: 8883 },
      { role: 'ftp', port: 990 }
    ],
    apply: (ports) => ({
      ip_address: RELAY_HOST,
      endpoint_overrides: { mqtt: `${RELAY_HOST}:${ports.mqtt}`, ftp: `${RELAY_HOST}:${ports.ftp}` }
    }),
    restore: (d) => ({ ip_address: d.host, endpoint_overrides: null })
  })
  // obico is a cloud relay (not a LAN device) — no connector routing needed.
};

// Per-vendor endpoint-relay status, surfaced so the UI/docs can be honest about
// what's tunnelled. Single-endpoint vendors are fully relayed; Bambu relays
// control (MQTT) + files (FTP), with camera still LAN-only.
export const VENDOR_ROUTING = {
  klipper: 'full', octoprint: 'full', prusalink: 'full', duet: 'full',
  flashforge: 'full', mks: 'full', snapmaker: 'full',
  bambu: 'control+files'
};
function profileFor(printer, devicePort) {
  const key = (printer.connection_type || '').toLowerCase();
  const fn = PROFILES[key];
  return fn ? { key, ...fn(printer, devicePort) } : null;
}
// Enable "via connector" for a printer. Returns { ok, reason?, endpoints? }.
export async function activateRoute(userId, printerId) {
  const inst = await getInstanceForUser(userId);
  const base = engineBase(inst);
  if (!base) return { ok: false, reason: 'no engine instance' };
  // Which site (connector) is this printer routed through?
  const autoAll = await getAutomation(userId);
  const connectorId = autoAll[printerId]?.connector_id ?? null;
  if (!connectorOnline(userId, connectorId)) return { ok: false, reason: 'connector (site) offline — route saved, will activate when it connects' };

  let printer;
  try { printer = await eng(base, `/api/v1/printers/${printerId}`); }
  catch { return { ok: false, reason: 'printer not found' }; }
  // On a re-activation printer.moonraker_port is already the relay port, so
  // prefer the device port recorded on the route.
  const prof = profileFor(printer, autoAll[printerId]?.direct_port);
  if (!prof) return { ok: false, reason: `auto-activation not supported for ${printer.connection_type || 'this'} printers yet` };

  // Capture the real host once (don't save the relay host as "direct").
  let directHost = printer.ip_address;
  let directPort = Number(printer.moonraker_port) || null;
  if (directHost === RELAY_HOST) {
    const auto = autoAll[printerId] || {};
    directHost = auto.direct_host; directPort = auto.direct_port;
    if (!directHost) return { ok: false, reason: 'lost the printer\'s real address; set it to Direct and re-add' };
  } else {
    await setRouteDirect(userId, printerId, directHost, directPort);
  }

  // One relay per endpoint (stable per-printer ports), tunnelled through the
  // printer's assigned site (connector).
  const ports = {};
  prof.endpoints.forEach((ep, idx) => {
    const rp = relayPort(printerId, idx);
    openTcpRelay(userId, directHost, ep.port, rp, connectorId);
    ports[ep.role] = rp;
  });
  await eng(base, `/api/v1/printers/${printerId}`, { method: 'PATCH', body: JSON.stringify(prof.apply(ports)) });

  // Agent-local camera relay (OctoEverywhere-style): ask the connector to hold
  // the camera locally, then point the engine's external_camera_url at our
  // internal relay so its existing external-camera path fetches frames through
  // the connector. Best-effort — a camera failure never blocks routing.
  try {
    await setupCameraRelay(userId, printerId, printer, directHost, connectorId, base);
  } catch (e) { /* camera relay is non-fatal */ }

  return { ok: true, vendor: prof.key, endpoints: prof.endpoints.map((ep, i) => ({ role: ep.role, relayPort: relayPort(printerId, i) })) };
}

// Register the printer's camera with its connector and wire external_camera_url.
async function setupCameraRelay(userId, printerId, printer, directHost, connectorId, base) {
  const vendor = printer.connection_type;
  if (vendor === 'bambu') {
    const reg = await proxyViaConnector(userId, {
      kind: 'camera-register', vendor, ip: directHost,
      access_code: printer.access_code, model: printer.model, name: `p${printerId}`, printer_id: printerId
    }, 15000, connectorId);
    if (!reg || !reg.ok) {
      // Not necessarily an A1/P1. It is equally likely the connector was
      // offline, go2rtc failed to start on the connector host, or the job timed
      // out — and swallowing all of those as "unsupported model" is why a
      // camera can be dark for days with nothing to point at.
      dbg('routing', 'camera-register refused', {
        printerId, connectorId, model: printer.model,
        error: reg?.error || 'no answer from connector'
      });
      return;
    }
  }
  // Point the engine at our internal relay endpoint. RELAY_HOST is the
  // control-plane's docker hostname, reachable from the engine.
  // Carry the gateway secret in the URL: the engine fetches this itself and
  // cannot attach a header. Without it every frame fetch came back 403 while
  // registration looked perfectly successful.
  const gw = process.env.OPHQ_GATEWAY_SECRET || '';
  const relayUrl = `http://${RELAY_HOST}:${CP_PORT}/api/internal/camera-relay/${userId}/${printerId}/frame`
    + (gw ? `?gw=${encodeURIComponent(gw)}` : '');
  await eng(base, `/api/v1/printers/${printerId}`, {
    method: 'PATCH',
    body: JSON.stringify({
      external_camera_enabled: true,
      external_camera_type: 'snapshot',
      external_camera_url: relayUrl,
      external_camera_snapshot_url: relayUrl
    })
  });
}

// Disable "via connector": restore the real address and drop every relay.
export async function deactivateRoute(userId, printerId) {
  const inst = await getInstanceForUser(userId);
  const base = engineBase(inst);
  const auto = (await getAutomation(userId))[printerId] || {};
  if (base && auto.direct_host) {
    let printer = null;
    try { printer = await eng(base, `/api/v1/printers/${printerId}`); } catch { /* */ }
    const prof = printer ? profileFor(printer, r.direct_port) : null;
    const patch = prof ? prof.restore({ host: auto.direct_host, port: auto.direct_port })
                       : { ip_address: auto.direct_host, moonraker_port: auto.direct_port || 7125, endpoint_overrides: null };
    try { await eng(base, `/api/v1/printers/${printerId}`, { method: 'PATCH', body: JSON.stringify(patch) }); } catch { /* best effort */ }
  }
  for (let i = 0; i < MAX_ENDPOINTS; i++) closeRelay(relayPort(printerId, i));
  return { ok: true };
}

// On boot, re-open every routed printer's relays (engine addresses are stable).
export async function reconcileRoutes() {
  let rows = [];
  try { rows = await listActiveRoutes(); } catch { return; }
  let n = 0;
  for (const r of rows) {
    if (!r.direct_host) continue;
    const base = engineBase(await getInstanceForUser(r.user_id));
    let printer = null;
    try { if (base) printer = await eng(base, `/api/v1/printers/${r.printer_id}`); } catch { /* */ }
    const prof = printer ? profileFor(printer, r.direct_port) : null;
    if (prof) prof.endpoints.forEach((ep, idx) => openTcpRelay(r.user_id, r.direct_host, ep.port, relayPort(r.printer_id, idx), r.connector_id ?? null));
    else openTcpRelay(r.user_id, r.direct_host, r.direct_port || 7125, relayPort(r.printer_id, 0), r.connector_id ?? null);
    n++;
  }
  if (n) console.log(`[routing] reconciled ${n} via-connector printer(s)`);
}

// Camera registration needs the connector present, and reconcile runs at
// startup — before any agent has reconnected — so retrying there always failed
// with "connector offline". Hang it off the session instead: whenever a
// connector attaches, register the camera for any of its printers still
// missing one. That makes it self-healing, which matters because a single
// failed attempt previously left a camera dark indefinitely with control and
// file transfer working normally.
// Smart plugs live on the tenant's LAN, but the engine calls them directly with
// httpx. On a cloud-hosted instance that is simply unroutable: every control and
// status request times out, so plugs appear dead while the device itself is
// perfectly healthy. They were built for local-mode deployments and never taught
// about the connector.
//
// No engine change is needed. It builds `http://{ip}/cm?...`, so pointing
// ip_address at "relayhost:port" produces a valid URL through the same TCP relay
// the printers use. Plug relays are allocated well above the printer range so the
// two schemes cannot collide.
const PLUG_RELAY_BASE = 41000;
export function plugRelayPort(plugId) { return PLUG_RELAY_BASE + (Number(plugId) % 900); }

export async function reconcileSmartPlugs(userId, connectorId, base) {
  if (!base || !connectorId) return 0;
  let plugs = [];
  try { plugs = await eng(base, '/api/v1/smart-plugs/'); } catch { return 0; }
  const list = Array.isArray(plugs) ? plugs : (plugs.items || plugs.smart_plugs || []);
  const known = await getPlugRoutes(userId).catch(() => ({}));
  let n = 0;
  for (const plug of list) {
    const addr = String(plug.ip_address || '');
    if (!addr || !plug.enabled) continue;
    // Already pointed at a relay: leave it be, but keep the tunnel open.
    const viaRelay = addr.startsWith(RELAY_HOST);
    const lanIp = viaRelay ? (known[plug.id] || null) : addr.split(':')[0];
    if (!lanIp) continue;
    if (!viaRelay) await setPlugRoute(userId, plug.id, lanIp).catch(() => {});
    const port = plugRelayPort(plug.id);
    openTcpRelay(userId, lanIp, 80, port, connectorId);
    if (!viaRelay) {
      try {
        // The engine exposes PATCH on this route, not PUT: a PUT returns 405 and
        // the reroute silently did nothing while the relay sat listening.
        await eng(base, `/api/v1/smart-plugs/${plug.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ ip_address: `${RELAY_HOST}:${port}` })
        });
        dbg('routing', 'smart plug routed via connector', { plugId: plug.id, lanIp, port });
      } catch (e) { dbg('routing', 'smart plug reroute failed', { plugId: plug.id, error: e?.message }); }
    }
    n++;
  }
  if (n) console.log(`[routing] ${n} smart plug(s) routed via connector`);
  return n;
}

onConnectorOnline(async (connectorId, userId) => {
  let rows = [];
  try { rows = await listActiveRoutes(); } catch { return; }
  const base = engineBase(await getInstanceForUser(userId));
  if (!base) return;
  // Plugs are per-tenant, not per-printer, so do them once per attach.
  try { await reconcileSmartPlugs(userId, connectorId, base); }
  catch (e) { dbg('routing', 'smart plug reconcile failed', { error: e?.message }); }
  for (const r of rows) {
    if (r.connector_id !== connectorId || !r.direct_host) continue;
    let printer = null;
    try { printer = await eng(base, `/api/v1/printers/${r.printer_id}`); } catch { continue; }
    if (!printer || printer.external_camera_enabled) continue;
    try {
      await setupCameraRelay(userId, r.printer_id, printer, r.direct_host, connectorId, base);
      // setupCameraRelay returns normally when the connector refuses, so don't
      // claim success — re-read the printer and report what actually happened.
      let after = null;
      try { after = await eng(base, `/api/v1/printers/${r.printer_id}`); } catch { /* best effort */ }
      dbg('routing', after?.external_camera_enabled
        ? 'camera registered on connector attach'
        : 'camera still not registered after attach', { printerId: r.printer_id, connectorId });
    } catch (e) {
      dbg('routing', 'camera register on attach failed', { printerId: r.printer_id, error: e?.message });
    }
  }
});
