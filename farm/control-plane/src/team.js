// ArtisanHub Farm control-plane - team mode
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// An ArtisanHub installation is one team. In team mode (OPHQ_TEAM_MODE=1, with
// one engine for everyone, OPHQ_ENGINE_URL) the farm is one farm for that team:
// every person signed in works on the owner's printers, queue and library, and
// never gets a farm of their own.
//
// Which printers each person may see and use is decided in the hub, which owns
// the list and pushes it here (PUT /api/team/printer-access):
//
//   { "version": 1,
//     "people": { "<email>": "all" | [<printer id>, ...] },
//     "owners": ["<email>", ...] }
//
// Owners always have every printer. Anyone else gets exactly what the list says,
// and a person missing from it gets no printers at all (fail closed). This module
// is pure: it reads and checks, and server.js does the I/O.
//
// Read lazily, so tests can set the environment after import.

export function teamMode() {
  return /^(1|true|yes)$/i.test(String(process.env.OPHQ_TEAM_MODE || '').trim());
}

export const ACCESS_SETTING = 'team_printer_access';

const ALL = 'all';

const normEmail = (e) => String(e || '').trim().toLowerCase();

function toId(v) {
  const n = typeof v === 'number' ? v : Number(String(v ?? '').trim());
  return Number.isInteger(n) && n > 0 ? n : null;
}

/**
 * Check and normalise a pushed list. Returns { map } on success or { error }.
 * The stored form is { version, people: { email: 'all' | [ids] }, owners: [emails] }.
 */
export function parseAccessMap(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return { error: 'a JSON object is required' };
  if (body.version !== 1) return { error: 'version must be 1' };
  const people = body.people;
  if (!people || typeof people !== 'object' || Array.isArray(people)) return { error: 'people must be an object' };
  const out = {};
  const keys = Object.keys(people);
  if (keys.length > 5000) return { error: 'too many people' };
  for (const k of keys) {
    const email = normEmail(k);
    if (!/^[^@\s]+@[^@\s]+$/.test(email)) return { error: `not an email: ${k}` };
    const v = people[k];
    if (v === ALL) { out[email] = ALL; continue; }
    if (!Array.isArray(v)) return { error: `the printers for ${email} must be "all" or a list of printer ids` };
    if (v.length > 10000) return { error: `too many printers for ${email}` };
    const ids = [];
    for (const x of v) {
      const id = toId(x);
      if (id === null) return { error: `not a printer id for ${email}: ${x}` };
      if (!ids.includes(id)) ids.push(id);
    }
    out[email] = ids;
  }
  const owners = Array.isArray(body.owners) ? body.owners.map(normEmail).filter((e) => /^[^@\s]+@[^@\s]+$/.test(e)) : [];
  if (!Array.isArray(body.owners) && body.owners !== undefined) return { error: 'owners must be a list' };
  return { map: { version: 1, people: out, owners: [...new Set(owners)] } };
}

/**
 * What one person may use: ALL, or a Set of printer ids (possibly empty).
 * `isOwner` is the farm's own owner check (the bootstrapped account or the owner
 * group); owners in the pushed list count too.
 */
export function accessFor(map, email, isOwner) {
  if (isOwner) return ALL;
  const e = normEmail(email);
  if (map?.owners?.includes(e)) return ALL;
  const v = map?.people?.[e];
  if (v === ALL) return ALL;
  return new Set(Array.isArray(v) ? v : []);
}

/** Is this email known to the team (listed, or listed as an owner)? */
export function inTeam(map, email) {
  const e = normEmail(email);
  return !!(map && (Object.prototype.hasOwnProperty.call(map.people || {}, e) || (map.owners || []).includes(e)));
}

export const isAll = (access) => access === ALL;

export function allows(access, printerId) {
  if (access === ALL) return true;
  const id = toId(printerId);
  return id !== null && access instanceof Set && access.has(id);
}

/** For a person limited to a list: their single printer, when they have exactly one. */
export function onlyPrinter(access) {
  return access instanceof Set && access.size === 1 ? [...access][0] : null;
}

/** The printer an engine path is about, e.g. /api/v1/printers/12/status -> 12. */
export function printerIdFromPath(path) {
  const p = String(path || '').split('?')[0];
  const m = /^\/api\/v1\/printers\/(\d+)(?:\/|$)/.exec(p);
  return m ? Number(m[1]) : null;
}

/** Is this engine path the printers collection itself (list or create)? */
export function isPrintersCollection(path) {
  return /^\/api\/v1\/printers\/?$/.test(String(path || '').split('?')[0]);
}

const ID_KEYS = ['printer_id', 'target_printer_id', 'assigned_printer_id'];
const LIST_KEYS = ['printer_ids', 'target_printer_ids'];

function idsInQuery(search) {
  const out = [];
  const q = new URLSearchParams(search || '');
  for (const k of [...ID_KEYS, ...LIST_KEYS]) {
    for (const v of q.getAll(k)) for (const part of String(v).split(',')) {
      const t = part.trim();
      if (t !== '' && t !== 'null') out.push(t);
    }
  }
  return out;
}

function idsInJson(body, depth = 0, out = []) {
  if (depth > 4 || body == null || typeof body !== 'object') return out;
  if (Array.isArray(body)) { for (const x of body) idsInJson(x, depth + 1, out); return out; }
  for (const [k, v] of Object.entries(body)) {
    if (ID_KEYS.includes(k)) { if (v !== null && v !== undefined && v !== '') out.push(v); }
    else if (LIST_KEYS.includes(k) && Array.isArray(v)) out.push(...v);
    else if (v && typeof v === 'object') idsInJson(v, depth + 1, out);
  }
  return out;
}

function idsInMultipart(buf) {
  const out = [];
  const text = buf.toString('latin1');
  const re = /name="(printer_id|target_printer_id|printer_ids)"\r?\n\r?\n([^\r\n]*)/gi;
  let m;
  while ((m = re.exec(text))) for (const part of m[2].split(',')) if (part.trim() && part.trim() !== 'null') out.push(part.trim());
  return out;
}

/**
 * May this person make this request to the engine? Owners and people with every
 * printer always may. Otherwise:
 *  - creating, changing or deleting a printer's own configuration is for owners;
 *  - a path about one printer needs that printer;
 *  - a printer named in the query or the body needs that printer;
 *  - adding to the queue with no printer (the scheduler would pick any) is pinned
 *    to their one printer, or refused when they have several or none.
 * Returns { ok: true, body? } (body when it was rewritten) or { ok: false, status, error }.
 */
export function checkEngineRequest(access, { method, path, search, body, isMultipart }) {
  if (access === ALL) return { ok: true };
  const m = String(method || 'GET').toUpperCase();
  const write = !['GET', 'HEAD', 'OPTIONS'].includes(m);
  const pid = printerIdFromPath(path);
  const notFound = { ok: false, status: 404, error: 'printer not found' };
  if (write && isPrintersCollection(path)) return { ok: false, status: 403, error: 'only an owner adds printers' };
  if (pid !== null) {
    if (!allows(access, pid)) return notFound;
    const rest = String(path).split('?')[0].replace(/^\/api\/v1\/printers\/\d+\/?/, '');
    if (write && rest === '' && ['PUT', 'PATCH', 'DELETE'].includes(m)) {
      return { ok: false, status: 403, error: "only an owner changes a printer's settings" };
    }
  }
  for (const id of idsInQuery(search)) if (!allows(access, id)) return notFound;
  if (write && body !== undefined && body !== null) {
    if (Buffer.isBuffer(body)) {
      if (isMultipart) for (const id of idsInMultipart(body)) if (!allows(access, id)) return notFound;
    } else {
      let json = body;
      if (typeof body === 'string') { try { json = JSON.parse(body); } catch { json = null; } }
      if (json && typeof json === 'object') {
        for (const id of idsInJson(json)) if (!allows(access, id)) return notFound;
        if (/\/add-to-queue\/?$|^\/api\/v1\/queue\/?$/.test(String(path).split('?')[0]) && !Array.isArray(json)) {
          const named = json.printer_id ?? json.target_printer_id ?? null;
          if (named === null || named === undefined || named === '') {
            const only = onlyPrinter(access);
            if (only === null) return { ok: false, status: 400, error: 'choose one of your printers for this job' };
            return { ok: true, body: { ...json, printer_id: only } };
          }
        }
      }
    }
  }
  return { ok: true };
}

/**
 * Remove what a person may not see from an engine answer. The printers list is
 * filtered by id; anything else is filtered wherever an object names a printer
 * (printer_id and friends), in arrays and in maps keyed by printer id, a few
 * levels deep. Items that name no printer (an unassigned queue item, a file)
 * stay. Returns the filtered value.
 */
export function filterEngineAnswer(access, path, json) {
  if (access === ALL) return json;
  if (isPrintersCollection(path)) {
    if (Array.isArray(json)) return json.filter((p) => allows(access, p?.id));
    if (json && typeof json === 'object') {
      const out = { ...json };
      for (const k of ['printers', 'items', 'results']) if (Array.isArray(out[k])) out[k] = out[k].filter((p) => allows(access, p?.id));
      return out;
    }
    return json;
  }
  return strip(access, json, 0);
}

function namesForbiddenPrinter(access, obj) {
  for (const k of ID_KEYS) {
    const v = obj[k];
    if (v !== null && v !== undefined && v !== '' && !allows(access, v)) return true;
  }
  return false;
}

function strip(access, v, depth) {
  if (depth > 5 || v == null || typeof v !== 'object') return v;
  if (Array.isArray(v)) {
    return v.filter((x) => !(x && typeof x === 'object' && !Array.isArray(x) && namesForbiddenPrinter(access, x)))
      .map((x) => strip(access, x, depth + 1));
  }
  const keys = Object.keys(v);
  const keyedById = keys.length > 0 && keys.every((k) => /^\d+$/.test(k));
  const out = {};
  for (const k of keys) {
    if (keyedById && !allows(access, k)) continue;
    out[k] = strip(access, v[k], depth + 1);
  }
  return out;
}

/** A single answer that is itself about a printer the person may not see (for a 404). */
export function answerHidden(access, json) {
  return access !== ALL && !!json && typeof json === 'object' && !Array.isArray(json) && namesForbiddenPrinter(access, json);
}

/** Filter a plain list of printers (objects with `id`). */
export function filterPrinters(access, arr) {
  return access === ALL ? arr : (Array.isArray(arr) ? arr.filter((p) => allows(access, p?.id)) : arr);
}
