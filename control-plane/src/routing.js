// OpenPrintHQ control-plane — connector auto-activation (routing)
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Copied to control-plane/src/routing.js. When a printer is set "via connector"
// this module stands up a stable local TCP relay (openTcpRelay) that tunnels to
// the printer through the connector, and repoints that printer's engine
// connection at the relay. Setting it back to "Direct" restores the saved real
// address and tears the relay down. Only single-endpoint Klipper/Moonraker
// printers are auto-activated today (Bambu uses several ports — a follow-up).
import { getInstanceForUser, getAutomation, setRouteDirect, listActiveRoutes } from './db.js';
import { openTcpRelay, closeRelay, connectorOnline, RELAY_HOST, relayPortForPrinter } from './connector.js';

const ENGINE_PORT = 8000;
const engineBase = (inst) => (inst && inst.subdomain ? `http://ophq-${inst.subdomain}:${ENGINE_PORT}` : null);

async function eng(base, path, opts = {}) {
  const headers = { accept: 'application/json' };
  if (opts.body !== undefined) headers['content-type'] = 'application/json';
  const res = await fetch(base + path, { ...opts, headers });
  if (!res.ok) throw Object.assign(new Error(`engine ${res.status}`), { status: res.status });
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

// Enable "via connector" for a printer. Returns { ok, reason?, relayPort? }.
export async function activateRoute(userId, printerId) {
  const inst = await getInstanceForUser(userId);
  const base = engineBase(inst);
  if (!base) return { ok: false, reason: 'no engine instance' };
  if (!connectorOnline(userId)) return { ok: false, reason: 'connector offline — route saved, will activate when it connects' };

  let printer;
  try { printer = await eng(base, `/api/v1/printers/${printerId}`); }
  catch { return { ok: false, reason: 'printer not found' }; }
  if ((printer.connection_type || '').toLowerCase() !== 'klipper') {
    return { ok: false, reason: 'auto-activation currently supports Klipper printers only' };
  }

  const relayPort = relayPortForPrinter(printerId);
  // Capture the real address the first time (don't save the relay host as "direct").
  let directHost = printer.ip_address, directPort = printer.moonraker_port || 7125;
  if (directHost === RELAY_HOST) {
    const auto = (await getAutomation(userId))[printerId] || {};
    directHost = auto.direct_host; directPort = auto.direct_port;
    if (!directHost) return { ok: false, reason: 'lost the printer\'s real address; set it to Direct and re-add' };
  } else {
    await setRouteDirect(userId, printerId, directHost, directPort);
  }

  openTcpRelay(userId, directHost, directPort, relayPort);
  // Repoint the engine at the relay (this disconnects+reconnects the printer).
  await eng(base, `/api/v1/printers/${printerId}`, {
    method: 'PATCH', body: JSON.stringify({ ip_address: RELAY_HOST, moonraker_port: relayPort })
  });
  return { ok: true, relayPort };
}

// Disable "via connector": restore the real address and drop the relay.
export async function deactivateRoute(userId, printerId) {
  const inst = await getInstanceForUser(userId);
  const base = engineBase(inst);
  const auto = (await getAutomation(userId))[printerId] || {};
  if (base && auto.direct_host) {
    try {
      await eng(base, `/api/v1/printers/${printerId}`, {
        method: 'PATCH', body: JSON.stringify({ ip_address: auto.direct_host, moonraker_port: auto.direct_port || 7125 })
      });
    } catch { /* best effort */ }
  }
  closeRelay(relayPortForPrinter(printerId));
  return { ok: true };
}

// On boot, re-open relays for every printer still routed via a connector. The
// engine's stored address (RELAY_HOST:stablePort) is unchanged, so only the
// relay listener needs recreating.
export async function reconcileRoutes() {
  let rows = [];
  try { rows = await listActiveRoutes(); } catch { return; }
  for (const r of rows) {
    if (r.direct_host) openTcpRelay(r.user_id, r.direct_host, r.direct_port || 7125, relayPortForPrinter(r.printer_id));
  }
  if (rows.length) console.log(`[routing] reconciled ${rows.length} via-connector printer(s)`);
}
