// OrcaSlicer sends to the print queue (Q9). Two ways, both through the OrcaSlicer add-on (OpenPrintHQ Print
// Queue, ./orcaslicer-addon, AGPL-3.0), which prints to the farm's queue for every kind of printer, Bambu Lab
// included:
//
// - OrcaSlicer on a person's computer: the Slice page offers the add-on as a download, declared for this farm's
//   address so OrcaSlicer asks about the address once, and a key to paste on its Config page.
// - OrcaSlicer in the browser (the ArtisanHub installer's desktop): the farm writes a connection file,
//   ".farm.json", into the desktop's inbox (desktop.js). The desktop takes it when OrcaSlicer is not running:
//   it installs the add-on, gives it the address and key, and adds one printer per farm printer, then deletes the
//   file (it holds the key) and leaves ".farm-applied", without the key, which the Slice page reads.
//
// SPDX-License-Identifier: AGPL-3.0-or-later
import { readFile, writeFile, rename, mkdir, unlink } from 'node:fs/promises';
import { join } from 'node:path';

export const ADDON_PATH = new URL('../orcaslicer-addon/plugin/openprinthq_print_queue.py', import.meta.url);
export const ADDON_FILE_NAME = 'openprinthq_print_queue.py';
export const CONNECTION_FILE = '.farm.json';
export const APPLIED_FILE = '.farm-applied';
export const PRESET_SUFFIX = ' - ArtisanHub Farm';
export const DESKTOP_KEY_LABEL = 'OrcaSlicer in the browser';
export const ADDON_KEY_LABEL = 'OrcaSlicer add-on';
// The one line the add-on declares its hosts on (permissions OrcaSlicer asks about once, at load).
const DECLARED = 'DECLARED_NETWORK = ("https://openprinthq.com",)';

/** scheme://host[:port] of an http(s) address, or null. */
export function originOf(url) {
  try {
    const u = new URL(String(url || ''));
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.origin : null;
  } catch { return null; }
}

/** The add-on's version, from its VERSION line. */
export function addonVersion(source) {
  const m = /^VERSION = "([^"]+)"$/m.exec(String(source || ''));
  return m ? m[1] : null;
}

/**
 * The add-on as this farm hands it out: this farm's address declared beside openprinthq.com, so OrcaSlicer asks
 * about it once when the add-on loads instead of for every address it calls. Throws when the add-on no longer has
 * the line, so a changed add-on fails the tests instead of shipping undeclared.
 */
export function addonFor(source, publicUrl) {
  const text = String(source || '');
  const at = text.indexOf(DECLARED);
  if (at < 0 || text.indexOf(DECLARED, at + 1) >= 0) throw new Error('the add-on has no single DECLARED_NETWORK line');
  const origin = originOf(publicUrl);
  if (!origin || origin === 'https://openprinthq.com') return text;
  const line = `DECLARED_NETWORK = ("https://openprinthq.com", ${JSON.stringify(origin)})`;
  return text.replace(DECLARED, line);
}

export async function readAddon(path = ADDON_PATH) {
  return readFile(path, 'utf8');
}

// OrcaSlicer's own printer for a farm printer: the system preset its "ArtisanHub Farm" printer inherits. Only
// names OrcaSlicer ships; a printer that matches none is listed as skipped and set up by hand (choose its printer
// in OrcaSlicer and set its printer agent to OpenPrintHQ).
const BAMBU = {
  H2D: 'Bambu Lab H2D 0.4 nozzle', 'H2D PRO': 'Bambu Lab H2D Pro 0.4 nozzle', H2C: 'Bambu Lab H2C 0.4 nozzle',
  H2S: 'Bambu Lab H2S 0.4 nozzle', X1C: 'Bambu Lab X1 Carbon 0.4 nozzle', 'X1 CARBON': 'Bambu Lab X1 Carbon 0.4 nozzle',
  X1E: 'Bambu Lab X1E 0.4 nozzle', X1: 'Bambu Lab X1 0.4 nozzle', P1S: 'Bambu Lab P1S 0.4 nozzle',
  P1P: 'Bambu Lab P1P 0.4 nozzle', P2S: 'Bambu Lab P2S 0.4 nozzle', A1: 'Bambu Lab A1 0.4 nozzle',
  'A1 MINI': 'Bambu Lab A1 mini 0.4 nozzle'
};
const OTHERS = [
  [/\bMK4S\b/i, 'Prusa MK4S 0.4 nozzle'],
  [/\bMK4\b/i, 'Prusa MK4 0.4 nozzle'],
  [/\bMK3\.5\b/i, 'Prusa MK3.5 0.4 nozzle'],
  [/\bMK3S\+?(?![\w.])/i, 'Prusa MK3S 0.4 nozzle'],
  [/\bcore\s*one\b/i, 'Prusa CORE One 0.4 nozzle'],
  [/\bvoron\s*2\.4\s*(250|300|350)\b/i, (m) => `Voron 2.4 ${m[1]} 0.4 nozzle`]
];

export function orcaPrinterPreset(printer) {
  const model = String(printer?.model || '').trim();
  const name = String(printer?.name || '').trim();
  if (String(printer?.connection_type || '').toLowerCase() === 'bambu') {
    const key = model.toUpperCase().replace(/^BAMBU\s*LAB\s*/, '').replace(/\s+/g, ' ');
    if (BAMBU[key]) return BAMBU[key];
  }
  for (const text of [model, name]) {
    for (const [re, preset] of OTHERS) {
      const m = re.exec(text);
      if (m) return typeof preset === 'function' ? preset(m) : preset;
    }
  }
  return null;
}

/** A printer name as a preset name: no characters a file name cannot hold, at most 60 long. */
export function presetName(printerName) {
  const n = String(printerName || '').replace(/[\u0000-\u001f\u007f/\\:*?"<>|]+/g, '-').replace(/\s+/g, ' ').trim()
    .replace(/^[.\s]+/, '').slice(0, 60).trim();
  return n ? n + PRESET_SUFFIX : null;
}

/** The farm's printers as OrcaSlicer printers for the desktop, and the ones it cannot match. */
export function desktopPrinters(printers) {
  const out = { printers: [], skipped: [] };
  for (const p of printers || []) {
    if (!p || p.is_active === false) continue;
    const base = orcaPrinterPreset(p);
    const name = presetName(p.name);
    if (base && name) out.printers.push({ id: p.id, name: p.name, preset: name, inherits: base });
    else out.skipped.push({ id: p.id, name: p.name });
  }
  return out;
}

/** Write the desktop's connection file, readable by its owner only, and moved into place whole. */
export async function writeConnection(dir, connection) {
  await mkdir(dir, { recursive: true });
  const tmp = join(dir, `.farm.json.${process.pid}.${Date.now()}`);
  await writeFile(tmp, JSON.stringify({ schema: 1, ...connection }, null, 1) + '\n', { mode: 0o600 });
  await rename(tmp, join(dir, CONNECTION_FILE));
}

async function readJson(path) {
  try { return JSON.parse(await readFile(path, 'utf8')); } catch { return null; }
}

/**
 * Where the desktop stands: connected (it took a connection, when, with which printers), or waiting for
 * OrcaSlicer to close so it can take one. Also the key the last connection used, so a new one can revoke it.
 */
export async function desktopConnection(dir) {
  if (!dir) return null;
  const pending = await readJson(join(dir, CONNECTION_FILE));
  const applied = await readJson(join(dir, APPLIED_FILE));
  const last = pending || applied;
  return {
    connected: Boolean(applied && applied.schema === 1 && !applied.error),
    pending: Boolean(pending),
    error: (!pending && applied?.error) ? String(applied.error).slice(0, 300) : null,
    at: applied?.applied_at || null,
    printers: (last?.printers || []).map((p) => ({ name: p.name, preset: p.preset })),
    skipped: (last?.skipped || []).map((p) => ({ name: p.name })),
    scope: last?.scope || null,
    key: last && last.actor_id != null && last.token_id ? { actorId: last.actor_id, tokenId: last.token_id } : null
  };
}

/** Remove a connection the desktop has not taken yet (its key is revoked by the caller). */
export async function dropPendingConnection(dir) {
  try { await unlink(join(dir, CONNECTION_FILE)); } catch { /* none */ }
}
