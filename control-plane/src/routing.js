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
import { getInstanceForUser, getAutomation, setRouteDirect, listActiveRoutes } from './db.js';
import { openTcpRelay, closeRelay, connectorOnline, RELAY_HOST, relayPort } from './connector.js';

const ENGINE_PORT = 8000;
const engineBase = (inst) => (inst && inst.subdomain ? `http://ophq-${inst.subdomain}:${ENGINE_PORT}` : null);
const MAX_ENDPOINTS = 8;   // relay-port slots reserved per printer

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
const PROFILES = {
  klipper: (p) => ({
    endpoints: [{ role: 'moonraker', port: Number(p.moonraker_port) || 7125 }],
    apply: (ports) => ({ ip_address: RELAY_HOST, moonraker_port: ports.moonraker, endpoint_overrides: { moonraker: `${RELAY_HOST}:${ports.moonraker}` } }),
    restore: (d) => ({ ip_address: d.host, moonraker_port: Number(d.port) || 7125, endpoint_overrides: null })
  }),
  bambu: () => ({
    // MQTT (status/control) + FTP (file upload). Camera is a follow-on endpoint.
    endpoints: [{ role: 'mqtt', port: 8883 }, { role: 'ftp', port: 990 }],
    apply: (ports) => ({ ip_address: RELAY_HOST, endpoint_overrides: { mqtt: `${RELAY_HOST}:${ports.mqtt}`, ftp: `${RELAY_HOST}:${ports.ftp}` } }),
    restore: (d) => ({ ip_address: d.host, endpoint_overrides: null })
  })
};
function profileFor(printer) {
  const key = (printer.connection_type || '').toLowerCase();
  const fn = PROFILES[key];
  return fn ? { key, ...fn(printer) } : null;
}
export function supportedVendors() { return Object.keys(PROFILES); }

// Enable "via connector" for a printer. Returns { ok, reason?, endpoints? }.
export async function activateRoute(userId, printerId) {
  const inst = await getInstanceForUser(userId);
  const base = engineBase(inst);
  if (!base) return { ok: false, reason: 'no engine instance' };
  if (!connectorOnline(userId)) return { ok: false, reason: 'connector offline — route saved, will activate when it connects' };

  let printer;
  try { printer = await eng(base, `/api/v1/printers/${printerId}`); }
  catch { return { ok: false, reason: 'printer not found' }; }
  const prof = profileFor(printer);
  if (!prof) return { ok: false, reason: `auto-activation not supported for ${printer.connection_type || 'this'} printers yet` };

  // Capture the real host once (don't save the relay host as "direct").
  let directHost = printer.ip_address;
  let directPort = Number(printer.moonraker_port) || null;
  if (directHost === RELAY_HOST) {
    const auto = (await getAutomation(userId))[printerId] || {};
    directHost = auto.direct_host; directPort = auto.direct_port;
    if (!directHost) return { ok: false, reason: 'lost the printer\'s real address; set it to Direct and re-add' };
  } else {
    await setRouteDirect(userId, printerId, directHost, directPort);
  }

  // One relay per endpoint (stable per-printer ports).
  const ports = {};
  prof.endpoints.forEach((ep, idx) => {
    const rp = relayPort(printerId, idx);
    openTcpRelay(userId, directHost, ep.port, rp);
    ports[ep.role] = rp;
  });
  await eng(base, `/api/v1/printers/${printerId}`, { method: 'PATCH', body: JSON.stringify(prof.apply(ports)) });
  return { ok: true, vendor: prof.key, endpoints: prof.endpoints.map((ep, i) => ({ role: ep.role, relayPort: relayPort(printerId, i) })) };
}

// Disable "via connector": restore the real address and drop every relay.
export async function deactivateRoute(userId, printerId) {
  const inst = await getInstanceForUser(userId);
  const base = engineBase(inst);
  const auto = (await getAutomation(userId))[printerId] || {};
  if (base && auto.direct_host) {
    let printer = null;
    try { printer = await eng(base, `/api/v1/printers/${printerId}`); } catch { /* */ }
    const prof = printer ? profileFor(printer) : null;
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
    const prof = printer ? profileFor(printer) : null;
    if (prof) prof.endpoints.forEach((ep, idx) => openTcpRelay(r.user_id, r.direct_host, ep.port, relayPort(r.printer_id, idx)));
    else openTcpRelay(r.user_id, r.direct_host, r.direct_port || 7125, relayPort(r.printer_id, 0));
    n++;
  }
  if (n) console.log(`[routing] reconciled ${n} via-connector printer(s)`);
}
