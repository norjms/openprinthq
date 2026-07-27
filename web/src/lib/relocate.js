// OpenPrintHQ — offline printer relocate + relink.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// When a printer shows offline, rescan the network for it. Identity is matched
// against the DATABASE record (the source of truth):
//   • Bambu   → the discovered device serial == printer.serial_number.
//   • Klipper → the discovered device MAC == printer.mac_address (the stable
//     hardware key; Klipper's serial_number is a random synthetic id). The MAC
//     is backfilled onto the record whenever a printer is seen at its known IP,
//     so once a printer has been online under this build it can be re-identified
//     across an IP change.
// If a matching device is found at a NEW IP we don't just "edit the IP" — we
// RELINK the existing printer record to the device at its new address (and
// reconnect). If nothing matches, the printer is truly offline.

const norm = (m) => String(m || '').trim().toLowerCase();

// Derive a /24 scan subnet from an IPv4 address.
export function subnetOf(ip) {
  const m = String(ip || '').match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.\d{1,3}$/);
  return m ? `${m[1]}.${m[2]}.${m[3]}.0/24` : '10.10.10.0/24';
}

const isKlipper = (p) => String(p?.connection_type || p?.vendor || '').toLowerCase() === 'klipper';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Kick off the right scan for this platform and wait for it to finish.
async function runScan(api, connectionType, subnet) {
  const klip = String(connectionType || '').toLowerCase() === 'klipper';
  if (klip) await api.discoverKlipperScan(subnet, 1.5);
  else await api.discoverScan(subnet, 1.5);
  for (let i = 0; i < 40; i++) {
    await sleep(1500);
    const s = await (klip ? api.discoverKlipperScanStatus() : api.discoverScanStatus());
    if (!s?.running) break;
  }
  return (klip ? api.discoveredKlipperPrinters() : api.discoveredPrinters()) || [];
}

// Record a device's MAC onto any stored Klipper printer that is currently at the
// device's IP but has no MAC yet — so identity is known before it ever moves.
async function backfillMacs(api, printers, discovered) {
  const changed = [];
  for (const d of discovered) {
    if (!d?.mac) continue;
    const at = printers.find(
      (p) => isKlipper(p) && p.ip_address === d.ip_address && !p.mac_address
    );
    if (at) {
      try {
        await api.updatePrinter(at.id, { mac_address: norm(d.mac) });
        at.mac_address = norm(d.mac);
        changed.push(at.id);
      } catch { /* best effort */ }
    }
  }
  return changed;
}

/**
 * Relocate one printer. Returns:
 *   { status: 'found',     device, newIp, changed }  identity confirmed
 *   { status: 'candidate', devices }                 Klipper, MAC not yet known
 *   { status: 'offline' }                            nothing matched
 */
export async function relocate(target, api) {
  const subnet = subnetOf(target.ip_address);
  const discovered = await runScan(api, target.connection_type, subnet);
  let printers = [];
  try { printers = (await api.printers()) || []; } catch { printers = [target]; }
  await backfillMacs(api, printers, discovered);

  const me = printers.find((p) => p.id === target.id) || target;

  if (isKlipper(me)) {
    if (me.mac_address) {
      const device = discovered.find((d) => d.mac && norm(d.mac) === norm(me.mac_address));
      if (device) return { status: 'found', device, newIp: device.ip_address, changed: device.ip_address !== me.ip_address };
      return { status: 'offline' };
    }
    // MAC not yet recorded — offer the Klipper devices not already claimed by
    // another printer as candidates for the user to confirm.
    const claimed = new Set(printers.filter((p) => p.id !== me.id && p.mac_address).map((p) => norm(p.mac_address)));
    const devices = discovered.filter((d) => d.mac && !claimed.has(norm(d.mac)));
    return devices.length ? { status: 'candidate', devices } : { status: 'offline' };
  }

  // Bambu (and other serial-identified platforms): match the real device serial.
  const serial = String(me.serial_number || '').toUpperCase();
  const device = discovered.find((d) => String(d.serial || '').toUpperCase() === serial);
  if (device) return { status: 'found', device, newIp: device.ip_address, changed: device.ip_address !== me.ip_address };
  return { status: 'offline' };
}

/**
 * Relink an existing printer record to a rediscovered device at its (possibly
 * new) IP, then reconnect. Records the device MAC for Klipper so future moves
 * auto-match. Does not touch the printer's identity/serial.
 */
export async function relink(target, device, api) {
  const body = { ip_address: device.ip_address };
  if (isKlipper(target) && device.mac) body.mac_address = norm(device.mac);
  await api.updatePrinter(target.id, body); // engine reconnects on ip change
  if (!body.ip_address || body.ip_address === target.ip_address) {
    // Same IP (or none) — just re-establish the connection.
    try { await api.printerAction(target.id, 'connect'); } catch { /* */ }
  }
  return body.ip_address;
}

// ---- auto-relocate rate limit (transient UI state, not a record) ----
const RL_KEY = (id) => `ophq_relocate_at_${id}`;
const COOLDOWN_MS = 10 * 60 * 1000; // don't auto-rescan the same printer more than every 10 min

export function canAutoRelocate(id) {
  try {
    const t = Number(localStorage.getItem(RL_KEY(id))) || 0;
    return Date.now() - t > COOLDOWN_MS;
  } catch { return true; }
}
export function markAutoRelocate(id) {
  try { localStorage.setItem(RL_KEY(id), String(Date.now())); } catch { /* */ }
}
