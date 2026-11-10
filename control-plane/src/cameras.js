// OpenPrintHQ control-plane - printer cameras as named go2rtc streams
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Every printer has a list of named cameras:
//
//   - its built-in camera, worked out from what the engine knows about the
//     printer: a Bambu Lab printer's RTSPS feed, a Klipper printer's Moonraker
//     webcam, an OctoPrint webcam;
//   - the camera the engine itself has for it (external_camera_url), when set;
//   - any further camera added here by URL, such as a chamber camera.
//
// Each one is a go2rtc stream. In single-tenant mode (tenancy.js) they are all
// registered when the control-plane starts and again whenever the list changes,
// and their health is checked in the background, so a camera is ready before
// anyone opens it and its state can be shown without opening it. The list and
// single frames are served through this control-plane, so a program holding a
// read key (the ArtisanHub hub, say) can show a printer's camera without ever
// reaching go2rtc or the printer.
//
// A source URL can carry a password (a Bambu access code, a webcam login). It is
// only ever sent to go2rtc on the internal network; listings show it with the
// credentials removed.

import { pool } from './db.js';
import { singleTenant } from './tenancy.js';

export const GO2RTC_URL = (process.env.OPHQ_GO2RTC_URL || 'http://openprinthq-go2rtc:1984').replace(/\/+$/, '');
const BAMBU_RTSP_PORT = 322;
const HEALTH_MS = Number(process.env.OPHQ_CAMERA_HEALTH_MS || 5 * 60 * 1000);
const FRAME_TIMEOUT_MS = 15000;

// ---- names -----------------------------------------------------------------

/**
 * The go2rtc stream name of a printer's built-in camera. Single-tenant mode has
 * one engine, so printer ids are unique on their own; otherwise they are
 * namespaced by account, as before, so tenants never collide on a shared go2rtc.
 */
export function builtinStreamName(userId, printerId) {
  return singleTenant() ? `p${Number(printerId)}` : `u${Number(userId)}_p${Number(printerId)}`;
}
export function engineCameraStreamName(userId, printerId) { return `${builtinStreamName(userId, printerId)}_ext`; }
export function extraStreamName(userId, printerId, cameraId) {
  return `${builtinStreamName(userId, printerId)}_c${Number(cameraId)}`;
}

/** Which rows of the cameras table an account sees: everyone's, in single-tenant mode. */
export function scopeFor(inst) {
  if (singleTenant()) return 'single';
  return inst?.subdomain ? `t:${inst.subdomain}` : null;
}

// ---- sources ---------------------------------------------------------------

// Mirror of the engine's supports_rtsp(): which Bambu models expose an RTSP
// camera (vs the A1/P1 chamber-image protocol, which isn't a go2rtc source).
export function supportsRtsp(model) {
  if (!model) return false;
  const m = String(model).toUpperCase();
  if (/^(X1|X2|H2|P2)/.test(m)) return true;
  return ['BL-P001', 'C13', 'N6', 'O1D', 'O1C', 'O1C2', 'O1S', 'O1E', 'O2D', 'N7'].includes(m);
}

const hostOf = (p) => String(p?.ip_address || '').trim();
const bracket = (h) => (h.includes(':') && !h.startsWith('[') ? `[${h}]` : h);

/** Absolute URL of a webcam path Moonraker reported, relative to the printer. */
export function absoluteWebcamUrl(host, url) {
  const u = String(url || '').trim();
  if (!u) return null;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(u)) return u;
  return `http://${bracket(host)}${u.startsWith('/') ? '' : '/'}${u}`;
}

/**
 * The printer's built-in camera source, or null when it has none go2rtc can use.
 * `webcams` is Moonraker's webcam list for a Klipper printer, when known.
 */
export function builtinSource(p, webcams = null) {
  const host = hostOf(p);
  if (!host) return null;
  const type = String(p.connection_type || '').toLowerCase();
  if (type === 'bambu') {
    if (!p.access_code || !supportsRtsp(p.model)) return null;
    return `rtsps://bblp:${encodeURIComponent(p.access_code)}@${bracket(host)}:${BAMBU_RTSP_PORT}/streaming/live/1`;
  }
  if (type === 'klipper' || type === 'moonraker') {
    const cams = Array.isArray(webcams) ? webcams.filter((w) => w && w.enabled !== false) : [];
    const first = cams.find((w) => w.stream_url) || null;
    return absoluteWebcamUrl(host, first?.stream_url) || `http://${bracket(host)}/webcam/?action=stream`;
  }
  if (type === 'octoprint') return `http://${bracket(host)}/webcam/?action=stream`;
  return null;
}

/** The camera the engine itself holds for this printer, as a go2rtc source. */
export function engineCameraSource(p) {
  if (!p?.external_camera_enabled) return null;
  const url = String(p.external_camera_url || '').trim();
  return /^(rtsps?|https?|rtmp):\/\//i.test(url) ? url : null;
}

/** Accept only URLs go2rtc can pull from, never a go2rtc-internal source (exec:, ffmpeg:, ...). */
export function validSource(url) {
  const u = String(url || '').trim();
  if (u.length > 1000 || !/^(rtsps?|https?|rtmp):\/\/[^\s]+$/i.test(u)) return null;
  try { new URL(u); } catch { return null; }
  return u;
}

/** A source as it may be shown: the scheme, host and path, never a user name or password. */
export function redact(url) {
  try {
    const u = new URL(url);
    u.username = ''; u.password = '';
    return u.toString();
  } catch { return null; }
}

// ---- store -----------------------------------------------------------------

export async function migrateCameras() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS cameras (
      id          SERIAL PRIMARY KEY,
      scope       TEXT NOT NULL,
      printer_id  INTEGER NOT NULL,
      kind        TEXT NOT NULL DEFAULT 'extra',   -- 'extra' (has a source) | 'builtin' | 'engine' (settings only)
      name        TEXT NOT NULL,
      source      TEXT,
      enabled     BOOLEAN NOT NULL DEFAULT true,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);
  await pool.query(`CREATE INDEX IF NOT EXISTS cameras_scope_printer ON cameras (scope, printer_id);`);
  // One settings row per printer for each of its own cameras.
  await pool.query(`CREATE UNIQUE INDEX IF NOT EXISTS cameras_one_setting
                    ON cameras (scope, printer_id, kind) WHERE kind <> 'extra';`);
}

async function rows(scope) {
  const { rows: r } = await pool.query('SELECT * FROM cameras WHERE scope = $1 ORDER BY printer_id, id', [scope]);
  return r;
}

export async function addCamera(scope, printerId, name, source) {
  const { rows: r } = await pool.query(
    `INSERT INTO cameras (scope, printer_id, kind, name, source) VALUES ($1, $2, 'extra', $3, $4) RETURNING *`,
    [scope, printerId, name, source]);
  return r[0];
}

export async function updateCamera(scope, id, { name, source, enabled }) {
  const { rows: r } = await pool.query(
    `UPDATE cameras SET name = COALESCE($3, name), source = COALESCE($4, source), enabled = COALESCE($5, enabled)
     WHERE scope = $1 AND id = $2 AND kind = 'extra' RETURNING *`,
    [scope, id, name ?? null, source ?? null, enabled ?? null]);
  return r[0] || null;
}

export async function deleteCamera(scope, id) {
  const { rows: r } = await pool.query(
    `DELETE FROM cameras WHERE scope = $1 AND id = $2 AND kind = 'extra' RETURNING *`, [scope, id]);
  return r[0] || null;
}

/** Name or switch off one of a printer's own cameras ('builtin' or 'engine'). */
export async function setOwnCamera(scope, printerId, kind, { name, enabled }) {
  const { rows: r } = await pool.query(
    `INSERT INTO cameras (scope, printer_id, kind, name, enabled) VALUES ($1, $2, $3, $4, $5)
     ON CONFLICT (scope, printer_id, kind) WHERE kind <> 'extra'
     DO UPDATE SET name = COALESCE($6, cameras.name), enabled = COALESCE($7, cameras.enabled)
     RETURNING *`,
    [scope, printerId, kind, name || defaultName(kind), enabled ?? true, name ?? null, enabled ?? null]);
  return r[0];
}

const defaultName = (kind) => (kind === 'engine' ? 'External camera' : 'Printer camera');

// ---- the list --------------------------------------------------------------

const state = new Map(); // stream name -> { state: 'ONLINE'|'OFFLINE'|'UNKNOWN', checked_at, error }

export function healthOf(stream) { return state.get(stream) || { state: 'UNKNOWN', checked_at: null, error: null }; }
export function noteHealth(stream, ok, error = null) {
  state.set(stream, { state: ok ? 'ONLINE' : 'OFFLINE', checked_at: new Date().toISOString(), error: ok ? null : error });
}

async function engineJson(base, path, timeoutMs = 8000) {
  const r = await fetch(base + path, { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(timeoutMs) });
  if (!r.ok) throw new Error(`engine HTTP ${r.status}`);
  return r.json();
}

async function moonrakerWebcams(p) {
  const host = hostOf(p);
  if (!host) return null;
  const port = Number(p.moonraker_port) || 7125;
  try {
    const headers = p.moonraker_api_key ? { 'x-api-key': p.moonraker_api_key } : {};
    const r = await fetch(`http://${bracket(host)}:${port}/server/webcams/list`, { headers, signal: AbortSignal.timeout(3000) });
    if (!r.ok) return null;
    const j = await r.json();
    return j?.result?.webcams || null;
  } catch { return null; }
}

/**
 * Every camera of every printer this account can see, with the source each one
 * pulls from. `withSource` is for the registration path only: what leaves this
 * process through the API carries a redacted source.
 */
export async function cameraList({ base, inst, userId }, { withSource = false } = {}) {
  const scope = scopeFor(inst);
  if (!base || !scope) return [];
  const list = await engineJson(base, '/api/v1/printers/');
  const printers = Array.isArray(list) ? list : (list?.printers || list?.items || []);
  const saved = await rows(scope);
  const out = [];
  for (const summary of printers) {
    const pid = Number(summary.id);
    if (!pid) continue;
    // The list omits secrets such as the access code; the printer's own record has them.
    let p = summary;
    try { p = { ...summary, ...(await engineJson(base, `/api/v1/printers/${pid}`)) }; } catch { /* keep the summary */ }
    const webcams = String(p.connection_type).toLowerCase() === 'klipper' ? await moonrakerWebcams(p) : null;
    const mine = saved.filter((r) => r.printer_id === pid);
    const setting = (kind) => mine.find((r) => r.kind === kind) || null;
    const entry = (kind, stream, name, source, enabled, id = null) => {
      const h = healthOf(stream);
      const row = {
        id, printer_id: pid, printer_name: p.name || `Printer ${pid}`, kind,
        camera_name: name, stream_name: stream, enabled,
        state: enabled ? h.state : 'DISABLED', checked_at: h.checked_at,
        source: redact(source)
      };
      if (withSource) row._source = source;
      return row;
    };
    const b = builtinSource(p, webcams);
    if (b) {
      const s = setting('builtin');
      out.push(entry('builtin', builtinStreamName(userId, pid), s?.name || defaultName('builtin'), b, s ? s.enabled : true));
    }
    const e = engineCameraSource(p);
    if (e) {
      const s = setting('engine');
      out.push(entry('engine', engineCameraStreamName(userId, pid), s?.name || defaultName('engine'), e, s ? s.enabled : true));
    }
    for (const r of mine.filter((x) => x.kind === 'extra')) {
      out.push(entry('extra', extraStreamName(userId, pid, r.id), r.name, r.source, r.enabled, r.id));
    }
  }
  return out;
}

/** The list as the API serves it, a chamber camera first within each printer. */
export function forApi(list) {
  const chamber = (c) => (/chamber/i.test(c.camera_name) ? 0 : 1);
  return list
    .map(({ _source, ...rest }) => rest)
    .sort((a, b) => a.printer_id - b.printer_id || chamber(a) - chamber(b) || (a.id ?? 0) - (b.id ?? 0));
}

// ---- go2rtc ----------------------------------------------------------------

export async function registerStream(name, src) {
  const r = await fetch(`${GO2RTC_URL}/api/streams?name=${encodeURIComponent(name)}&src=${encodeURIComponent(src)}`,
    { method: 'PUT', signal: AbortSignal.timeout(5000) });
  return r.ok;
}

export async function removeStream(name) {
  try {
    await fetch(`${GO2RTC_URL}/api/streams?src=${encodeURIComponent(name)}`, { method: 'DELETE', signal: AbortSignal.timeout(5000) });
  } catch { /* already gone, or go2rtc down: nothing to remove */ }
}

/** One JPEG from a registered stream, or null. Records the stream's health either way. */
export async function frame(name, timeoutMs = FRAME_TIMEOUT_MS) {
  try {
    const r = await fetch(`${GO2RTC_URL}/api/frame.jpeg?src=${encodeURIComponent(name)}`, { signal: AbortSignal.timeout(timeoutMs) });
    const buf = Buffer.from(await r.arrayBuffer());
    const ok = r.ok && buf.length > 2 && buf[0] === 0xff && buf[1] === 0xd8;
    noteHealth(name, ok, ok ? null : `go2rtc HTTP ${r.status}`);
    return ok ? buf : null;
  } catch (e) {
    noteHealth(name, false, e.message);
    return null;
  }
}

/**
 * Make go2rtc hold exactly the enabled cameras in the list: register each one,
 * and drop streams of cameras that were switched off or removed. Answers how many
 * were registered.
 */
export async function syncStreams(list, previous = new Set()) {
  let n = 0;
  const want = new Set();
  for (const c of list) {
    if (!c.enabled || !c._source) continue;
    want.add(c.stream_name);
    if (await registerStream(c.stream_name, c._source).catch(() => false)) n++;
  }
  for (const name of previous) if (!want.has(name)) await removeStream(name);
  return { registered: n, streams: want };
}

// ---- single-tenant background work -----------------------------------------

let known = new Set();
let timer = null;
let running = null;

/**
 * Register every camera, then check each one's health. Runs at start, every few
 * minutes, and after any change to the list. Only in single-tenant mode, where
 * there is one engine and its printers are on this network; in multi-tenant mode
 * cameras are registered when someone opens them, as before.
 */
export function startCameraSync({ engine, log = console }) {
  if (!singleTenant() || timer) return;
  const pass = () => resync({ engine, log }).catch((e) => log.error?.(`[cameras] ${e.message}`));
  setTimeout(pass, 5000);
  timer = setInterval(pass, HEALTH_MS);
  timer.unref?.();
}

let again = false;

export async function resync({ engine, log = console, health = true }) {
  // A change while a pass runs may have missed it: run once more after.
  if (running) { again = true; return running; }
  running = (async () => {
    const ctx = await engine();
    if (!ctx) return { registered: 0 };
    const list = await cameraList(ctx, { withSource: true });
    const r = await syncStreams(list, known);
    known = r.streams;
    if (health) for (const name of r.streams) await frame(name);
    log.info?.(`[cameras] ${r.registered} stream(s) registered`);
    return r;
  })().finally(() => {
    running = null;
    if (again) { again = false; resync({ engine, log, health }).catch(() => {}); }
  });
  return running;
}
