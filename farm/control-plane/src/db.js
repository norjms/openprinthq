// OpenPrintHQ control-plane — database access + migrations
// SPDX-License-Identifier: AGPL-3.0-or-later
import pg from 'pg';
import { readFileSync } from 'node:fs';

const { Pool } = pg;

// Control-plane DB (accounts + instance registry)
export const pool = new Pool({
  connectionString:
    process.env.DATABASE_URL ||
    'postgresql://ophq_app:changeme@127.0.0.1:5432/ophq_control',
  max: 8
});

// A second pool to the "postgres" maintenance DB so we can CREATE DATABASE
// for each tenant (CREATE DATABASE cannot run inside a transaction / on the
// currently-connected control DB reliably).
export const adminPool = new Pool({
  connectionString: (
    process.env.DATABASE_URL ||
    'postgresql://ophq_app:changeme@127.0.0.1:5432/ophq_control'
  ).replace(/\/[^/]+$/, '/postgres'),
  max: 2
});

export async function migrate() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS users (
      id          SERIAL PRIMARY KEY,
      email       TEXT UNIQUE NOT NULL,
      display_name TEXT,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);
  // Owner/admin flag. The first-ever account is always owner (bootstrapped
  // below), plus anyone in the Authentik owner group at request time.
  await pool.query(`ALTER TABLE users ADD COLUMN IF NOT EXISTS is_owner BOOLEAN NOT NULL DEFAULT false;`);
  await pool.query(`
    CREATE TABLE IF NOT EXISTS instances (
      id             SERIAL PRIMARY KEY,
      user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      subdomain      TEXT UNIQUE NOT NULL,
      db_name        TEXT NOT NULL,
      status         TEXT NOT NULL DEFAULT 'provisioning',
      port           INTEGER,
      engine_version TEXT DEFAULT 'openprinthq-engine:dev',
      created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (user_id)
    );
  `);
  // Slicer preset compatibility: which OrcaSlicer process/filament presets are
  // compatible with which printer preset. Extracted from OrcaSlicer's stock
  // profile JSONs (each carries an explicit `compatible_printers` list — no
  // condition expressions), so filtering by the selected printer is a real join
  // instead of a name-matching heuristic. Data is static (stock profiles), so a
  // single shared table is seeded once.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS slicer_compat (
      kind         TEXT NOT NULL,          -- 'process' | 'filament'
      preset_name  TEXT NOT NULL,
      printer_name TEXT NOT NULL
    );
  `);
  await pool.query(`CREATE INDEX IF NOT EXISTS idx_slicer_compat_printer ON slicer_compat (printer_name, kind);`);

  // Power circuits: which physical breaker circuit each printer sits on, so a
  // temperature-staggered batch only serialises heat-up within a circuit and
  // lets printers on different circuits preheat in parallel. Keyed by
  // (user, engine printer id). A printer with no row is its own circuit.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS printer_circuits (
      user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      printer_id INTEGER NOT NULL,
      circuit    TEXT NOT NULL,
      PRIMARY KEY (user_id, printer_id)
    );
  `);

  // Temperature-staggered batch runs. The orchestrator holds each printer's
  // queue item (manual_start) and releases the next on a circuit only once a
  // preheating slot frees (previous printer reached bed+chamber target) or the
  // per-printer timeout fires. Persisted so a control-plane restart resumes.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS batch_runs (
      id            SERIAL PRIMARY KEY,
      user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      file_id       INTEGER,
      file_name     TEXT,
      staggered     BOOLEAN NOT NULL DEFAULT true,
      max_preheat   INTEGER NOT NULL DEFAULT 1,
      tolerance     REAL NOT NULL DEFAULT 3.0,
      max_wait_secs INTEGER NOT NULL DEFAULT 900,
      steps         JSONB NOT NULL DEFAULT '[]'::jsonb,
      status        TEXT NOT NULL DEFAULT 'running',
      created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);
  await pool.query(`CREATE INDEX IF NOT EXISTS idx_batch_runs_status ON batch_runs (status);`);

  // Integration tokens: a per-user bearer token for external read-only access
  // (Home Assistant, Homepage dashboard, Prometheus) that can't do Authentik SSO.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS integration_tokens (
      user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      token      TEXT UNIQUE NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS printer_automation (
      user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      printer_id  INTEGER NOT NULL,
      auto_eject  BOOLEAN NOT NULL DEFAULT false,
      eject_gcode TEXT NOT NULL DEFAULT '',
      PRIMARY KEY (user_id, printer_id)
    );
  `);
  await pool.query(`ALTER TABLE printer_automation ADD COLUMN IF NOT EXISTS connector_id INTEGER;`);
  await pool.query(`ALTER TABLE printer_automation ADD COLUMN IF NOT EXISTS direct_host TEXT;`);
  await pool.query(`ALTER TABLE printer_automation ADD COLUMN IF NOT EXISTS direct_port INTEGER;`);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS connectors (
      id         SERIAL PRIMARY KEY,
      user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      name       TEXT NOT NULL DEFAULT 'connector',
      token      TEXT UNIQUE NOT NULL,
      last_seen  TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);
  await pool.query(`ALTER TABLE connectors ADD COLUMN IF NOT EXISTS client_public_pem TEXT;`);
  await pool.query(`ALTER TABLE connectors ADD COLUMN IF NOT EXISTS host_cidr TEXT;`);

  // Platform-wide learned mapping from whatever identifier a vendor happens to
  // expose during discovery to the name a human actually uses. For Bambu that
  // identifier is the SSDP devmodel ("O1D" -> "H2D"); other manufacturers may
  // expose something else, or nothing at all, so `code` is "whatever we can key
  // on", not "a devmodel".
  //
  // Filled when a user names a printer on add (fill-when-empty), and lockable by
  // a global admin so a curated name can't be overwritten and one user's typo
  // can't propagate. Nothing is seeded here: the user is the source.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS printer_model_names (
      vendor       TEXT NOT NULL,
      code         TEXT NOT NULL,
      friendly_name TEXT NOT NULL,
      locked       BOOLEAN NOT NULL DEFAULT false,
      updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (vendor, code)
    );
  `);

  // A tenant's own log destination. Separate from app_settings, which is
  // global: these logs belong to one instance and go only where that tenant
  // says. Never populated by the platform operator.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS user_log_sinks (
      user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      url        TEXT NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS signing_keys (
      user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      public_pem  TEXT NOT NULL,
      private_enc TEXT NOT NULL,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);

  // Per-user appearance (Settings -> Look & Feel): theme mode, colour overrides,
  // text scale, accessibility toggles, and branding (site name, tagline, logo /
  // favicon data-URIs). A single JSON blob per user keeps it schema-light and
  // isolated - one user's theme never touches another's.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS appearance (
      user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      config     JSONB NOT NULL DEFAULT '{}'::jsonb,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);

  // Global (deployment-wide) key/value settings — e.g. deployment_mode.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS app_settings (
      key        TEXT PRIMARY KEY,
      value      TEXT,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);

  // Invite codes: an owner mints single-use codes (2-day TTL). Redeeming a code
  // on the public signup page creates the login user in Authentik and provisions
  // that person their own instance. created_by / used_by reference users(id) but
  // are nullable + ON DELETE SET NULL so account cleanup never wipes the audit row.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS invite_codes (
      code       TEXT PRIMARY KEY,
      created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
      email      TEXT,
      note       TEXT,
      expires_at TIMESTAMPTZ NOT NULL,
      used_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
      used_at    TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);
  await pool.query(`CREATE INDEX IF NOT EXISTS idx_invite_codes_expires ON invite_codes (expires_at);`);

  // Bootstrap: if no owner exists yet, promote the earliest account (covers DBs
  // created before the is_owner column existed).
  await pool.query(`
    UPDATE users SET is_owner = true
    WHERE id = (SELECT id FROM users ORDER BY id LIMIT 1)
      AND NOT EXISTS (SELECT 1 FROM users WHERE is_owner)`);

  await pool.query(`ALTER TABLE instances ADD COLUMN IF NOT EXISTS features JSONB NOT NULL DEFAULT '{}'::jsonb;`);
  await pool.query(`ALTER TABLE instances ADD COLUMN IF NOT EXISTS storage_quota_mb INTEGER;`);
  await seedSlicerCompat();
}

// ---- appearance (Look & Feel) -------------------------------------------
export async function getAppearance(userId) {
  const { rows } = await pool.query('SELECT config FROM appearance WHERE user_id = $1', [userId]);
  return rows[0]?.config || null;
}
export async function setAppearance(userId, config) {
  await pool.query(
    `INSERT INTO appearance (user_id, config) VALUES ($1, $2)
     ON CONFLICT (user_id) DO UPDATE SET config = EXCLUDED.config, updated_at = now()`,
    [userId, config]);
  return config;
}
// The site owner's user id (earliest owner account); null if there is none yet.
// Used to expose the owner's branding as the public SITE branding.
export async function getOwnerUserId() {
  const { rows } = await pool.query(
    'SELECT id FROM users WHERE is_owner = true ORDER BY id LIMIT 1');
  return rows[0]?.id ?? null;
}

// ---- integration tokens -------------------------------------------------
export async function getIntegrationToken(userId) {
  const { rows } = await pool.query('SELECT token FROM integration_tokens WHERE user_id = $1', [userId]);
  return rows[0]?.token || null;
}
export async function setIntegrationToken(userId, token) {
  await pool.query(
    `INSERT INTO integration_tokens (user_id, token) VALUES ($1, $2)
     ON CONFLICT (user_id) DO UPDATE SET token = EXCLUDED.token, created_at = now()`,
    [userId, token]);
  return token;
}
export async function getUserByIntegrationToken(token) {
  if (!token) return null;
  const { rows } = await pool.query(
    `SELECT u.* FROM users u JOIN integration_tokens t ON t.user_id = u.id WHERE t.token = $1`, [token]);
  return rows[0] || null;
}

// ---- power circuits -----------------------------------------------------
export async function getCircuits(userId) {
  const { rows } = await pool.query(
    'SELECT printer_id, circuit FROM printer_circuits WHERE user_id = $1', [userId]);
  const out = {};
  for (const r of rows) out[r.printer_id] = r.circuit;
  return out;
}

export async function setCircuit(userId, printerId, circuit) {
  const c = (circuit || '').toString().trim();
  if (!c) {
    await pool.query('DELETE FROM printer_circuits WHERE user_id = $1 AND printer_id = $2', [userId, printerId]);
    return;
  }
  await pool.query(
    `INSERT INTO printer_circuits (user_id, printer_id, circuit) VALUES ($1, $2, $3)
     ON CONFLICT (user_id, printer_id) DO UPDATE SET circuit = EXCLUDED.circuit`,
    [userId, printerId, c]);
}

// ---- printer automation (bed ejection / continuous printing, #20) -------
export async function getAutomation(userId) {
  const { rows } = await pool.query(
    'SELECT printer_id, auto_eject, eject_gcode, connector_id, direct_host, direct_port FROM printer_automation WHERE user_id = $1', [userId]);
  const out = {};
  for (const r of rows) out[r.printer_id] = { auto_eject: r.auto_eject, eject_gcode: r.eject_gcode || '', connector_id: r.connector_id ?? null, direct_host: r.direct_host ?? null, direct_port: r.direct_port ?? null };
  return out;
}
export async function setAutomation(userId, printerId, patch) {
  const cur = (await pool.query(
    'SELECT auto_eject, eject_gcode, connector_id FROM printer_automation WHERE user_id = $1 AND printer_id = $2',
    [userId, printerId])).rows[0] || {};
  const auto_eject = patch.auto_eject !== undefined ? !!patch.auto_eject : (cur.auto_eject ?? false);
  const eject_gcode = patch.eject_gcode !== undefined ? (patch.eject_gcode || '').toString() : (cur.eject_gcode ?? '');
  const connector_id = patch.connector_id !== undefined
    ? (patch.connector_id === null || patch.connector_id === '' ? null : Number(patch.connector_id))
    : (cur.connector_id ?? null);
  await pool.query(
    `INSERT INTO printer_automation (user_id, printer_id, auto_eject, eject_gcode, connector_id) VALUES ($1, $2, $3, $4, $5)
     ON CONFLICT (user_id, printer_id) DO UPDATE SET auto_eject = EXCLUDED.auto_eject, eject_gcode = EXCLUDED.eject_gcode, connector_id = EXCLUDED.connector_id`,
    [userId, printerId, auto_eject, eject_gcode, connector_id]);
}

// ---- local connectors (outbound tunnel, #28/#29) ------------------------
import crypto from 'node:crypto';
export async function createConnector(userId, name) {
  const token = 'ophqc_' + crypto.randomBytes(24).toString('hex');
  const { rows } = await pool.query(
    `INSERT INTO connectors (user_id, name, token) VALUES ($1, $2, $3)
     RETURNING id, name, token, created_at`, [userId, (name || 'connector').slice(0, 60), token]);
  return rows[0];
}
// ---- connector host CIDR (P2: reported host LAN subnet) ------------------
export async function setConnectorHostCidr(connectorId, cidr) {
  await pool.query('UPDATE connectors SET host_cidr = $2 WHERE id = $1', [connectorId, cidr || null]);
}

// ---- printer model-name mapping (P4/P5) ---------------------------------
function normModelKey(vendor, code) {
  return [String(vendor || '').trim().toLowerCase(), String(code || '').trim().toUpperCase()];
}
export async function listModelNames() {
  const { rows } = await pool.query('SELECT vendor, code, friendly_name, locked, updated_at FROM printer_model_names ORDER BY vendor, code');
  return rows;
}
export async function getModelName(vendor, code) {
  const [v, c] = normModelKey(vendor, code);
  if (!v || !c) return null;
  const { rows } = await pool.query('SELECT friendly_name, locked FROM printer_model_names WHERE vendor = $1 AND code = $2', [v, c]);
  return rows[0] || null;
}
// Fill-when-empty: only inserts a mapping if none exists (and never overwrites a
// locked one). Returns the effective row. Admin edits use upsertModelNameForce.
// Translate a vendor wire code to the name the user actually knows. Falls back
// to the code itself so an unmapped model degrades to "shows something odd"
// rather than "shows nothing".
export async function friendlyModelName(vendor, code) {
  if (!code) return code || '';
  try {
    const row = await getModelName(vendor, code);
    return (row && row.friendly_name) || code;
  } catch { return code; }
}

// Learn a model name from what a user typed on add. Deliberately NOT seeded
// from any shipped list: the name comes from the person who owns the printer,
// and a global admin curates or locks it afterwards so one typo doesn't
// propagate to everyone who scans next.
//
// Returns null when the vendor gave us nothing to key on. Not every
// manufacturer exposes a model identifier during discovery, and when there is
// no shared key there is nothing to share — the printer still gets the name the
// user typed, it just stays local to that printer instead of teaching the
// platform. Callers must treat null as "not shareable", not as failure.
export async function learnModelName(vendor, code, friendly) {
  const [v, c] = normModelKey(vendor, code);
  const f = String(friendly || '').trim();
  if (!v || !c || !f) return null;
  const { rows } = await pool.query(
    `INSERT INTO printer_model_names (vendor, code, friendly_name)
     VALUES ($1, $2, $3)
     ON CONFLICT (vendor, code) DO NOTHING
     RETURNING vendor, code, friendly_name, locked`, [v, c, f]);
  if (rows[0]) return rows[0];
  return (await getModelName(v, c));
}
export async function upsertModelNameForce(vendor, code, friendly, locked) {
  const [v, c] = normModelKey(vendor, code);
  const f = String(friendly || '').trim();
  if (!v || !c || !f) return null;
  const { rows } = await pool.query(
    `INSERT INTO printer_model_names (vendor, code, friendly_name, locked, updated_at)
     VALUES ($1, $2, $3, $4, now())
     ON CONFLICT (vendor, code) DO UPDATE SET friendly_name = EXCLUDED.friendly_name, locked = EXCLUDED.locked, updated_at = now()
     RETURNING vendor, code, friendly_name, locked`, [v, c, f, !!locked]);
  return rows[0];
}
export async function setModelNameLock(vendor, code, locked) {
  const [v, c] = normModelKey(vendor, code);
  const { rows } = await pool.query('UPDATE printer_model_names SET locked = $3, updated_at = now() WHERE vendor = $1 AND code = $2 RETURNING vendor, code, friendly_name, locked', [v, c, !!locked]);
  return rows[0] || null;
}
export async function deleteModelName(vendor, code) {
  const [v, c] = normModelKey(vendor, code);
  await pool.query('DELETE FROM printer_model_names WHERE vendor = $1 AND code = $2', [v, c]);
}

export async function listConnectors(userId) {
  const { rows } = await pool.query(
    'SELECT id, name, last_seen, created_at, host_cidr, (client_public_pem IS NOT NULL) AS has_client_key FROM connectors WHERE user_id = $1 ORDER BY id', [userId]);
  return rows;
}
export async function deleteConnector(userId, id) {
  await pool.query('DELETE FROM connectors WHERE user_id = $1 AND id = $2', [userId, id]);
}
export async function getConnectorByToken(token) {
  if (!token) return null;
  const { rows } = await pool.query('SELECT * FROM connectors WHERE token = $1', [token]);
  return rows[0] || null;
}
export async function setConnectorClientKey(userId, id, pem) {
  await pool.query('UPDATE connectors SET client_public_pem = $3 WHERE user_id = $1 AND id = $2',
    [userId, id, pem && pem.trim() ? pem.trim() : null]);
}

export async function touchConnector(id) {
  await pool.query('UPDATE connectors SET last_seen = now() WHERE id = $1', [id]);
}

// ---- global app settings (deployment-wide key/value) --------------------
export async function getAppSetting(key, fallback = null) {
  const { rows } = await pool.query('SELECT value FROM app_settings WHERE key = $1', [key]);
  return rows.length ? rows[0].value : fallback;
}
// Secrets stored in app_settings (e.g. the Cloudflare TURN API token) are
// encrypted at rest with a key derived from SESSION_SECRET, so a database dump
// or a stray pg_dump in a backup directory does not hand over live credentials.
// AES-256-GCM; the tag is stored alongside so tampering is detected on read.
const SETTINGS_KEY = crypto.hkdfSync(
  'sha256',
  Buffer.from(process.env.SESSION_SECRET || 'dev-insecure-secret-change-me-in-prod-000000'),
  Buffer.from('ophq-app-settings-v1'),
  Buffer.from('aes-256-gcm'),
  32
);
function encryptSetting(plain) {
  const iv = crypto.randomBytes(12);
  const c = crypto.createCipheriv('aes-256-gcm', Buffer.from(SETTINGS_KEY), iv);
  const ct = Buffer.concat([c.update(String(plain), 'utf8'), c.final()]);
  return `enc:v1:${iv.toString('base64')}:${c.getAuthTag().toString('base64')}:${ct.toString('base64')}`;
}
function decryptSetting(stored) {
  if (typeof stored !== 'string' || !stored.startsWith('enc:v1:')) return stored; // plaintext legacy value
  try {
    const [, , iv, tag, ct] = stored.split(':');
    const d = crypto.createDecipheriv('aes-256-gcm', Buffer.from(SETTINGS_KEY), Buffer.from(iv, 'base64'));
    d.setAuthTag(Buffer.from(tag, 'base64'));
    return Buffer.concat([d.update(Buffer.from(ct, 'base64')), d.final()]).toString('utf8');
  } catch {
    // Wrong key (SESSION_SECRET rotated) or tampered ciphertext. Treat as unset
    // rather than throwing, so one bad row can't take the control-plane down.
    return null;
  }
}
export async function getSecretSetting(key) {
  const raw = await getAppSetting(key, null);
  return raw == null ? null : decryptSetting(raw);
}
export async function setSecretSetting(key, value) {
  if (value == null || value === '') return setAppSetting(key, null);
  return setAppSetting(key, encryptSetting(value));
}

export async function setAppSetting(key, value) {
  await pool.query(
    `INSERT INTO app_settings (key, value, updated_at) VALUES ($1, $2, now())
     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()`,
    [key, value == null ? null : String(value)]);
}

export async function setRouteDirect(userId, printerId, host, port) {
  await pool.query(
    `INSERT INTO printer_automation (user_id, printer_id, direct_host, direct_port) VALUES ($1, $2, $3, $4)
     ON CONFLICT (user_id, printer_id) DO UPDATE SET direct_host = EXCLUDED.direct_host, direct_port = EXCLUDED.direct_port`,
    [userId, printerId, host, port]);
}
export async function listActiveRoutes() {
  const { rows } = await pool.query(
    'SELECT user_id, printer_id, connector_id, direct_host, direct_port FROM printer_automation WHERE connector_id IS NOT NULL');
  return rows;
}

// ---- connector command-signing keys (RSA-2048) --------------------------
export async function getSigningPublic(userId) {
  const { rows } = await pool.query('SELECT public_pem, created_at FROM signing_keys WHERE user_id = $1', [userId]);
  return rows[0] || null;
}
export async function getSigningPrivateEnc(userId) {
  const { rows } = await pool.query('SELECT private_enc FROM signing_keys WHERE user_id = $1', [userId]);
  return rows[0]?.private_enc || null;
}
export async function setSigningKey(userId, publicPem, privateEnc) {
  await pool.query(
    `INSERT INTO signing_keys (user_id, public_pem, private_enc, created_at) VALUES ($1, $2, $3, now())
     ON CONFLICT (user_id) DO UPDATE SET public_pem = EXCLUDED.public_pem, private_enc = EXCLUDED.private_enc, created_at = now()`,
    [userId, publicPem, privateEnc]);
}
export async function deleteSigningKey(userId) {
  await pool.query('DELETE FROM signing_keys WHERE user_id = $1', [userId]);
}

// ---- batch runs ---------------------------------------------------------
export async function createBatchRun(userId, b) {
  const { rows } = await pool.query(
    `INSERT INTO batch_runs (user_id, file_id, file_name, staggered, max_preheat, tolerance, max_wait_secs, steps)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) RETURNING *`,
    [userId, b.fileId ?? null, b.fileName ?? null, b.staggered !== false,
     b.maxPreheat ?? 1, b.tolerance ?? 3.0, b.maxWaitSecs ?? 900, JSON.stringify(b.steps || [])]);
  return rows[0];
}

export async function getBatchById(id) {
  const { rows } = await pool.query('SELECT * FROM batch_runs WHERE id = $1', [id]);
  return rows[0] || null;
}

export async function getActiveBatchForUser(userId) {
  const { rows } = await pool.query(
    `SELECT * FROM batch_runs WHERE user_id = $1 AND status = 'running'
     ORDER BY created_at DESC LIMIT 1`, [userId]);
  return rows[0] || null;
}

export async function listRunningBatches() {
  const { rows } = await pool.query(`SELECT * FROM batch_runs WHERE status = 'running' ORDER BY id`);
  return rows;
}

export async function updateBatchRun(id, { steps, status }) {
  const sets = ['updated_at = now()'];
  const params = [];
  let i = 1;
  if (steps !== undefined) { sets.push(`steps = $${i++}::jsonb`); params.push(JSON.stringify(steps)); }
  if (status !== undefined) { sets.push(`status = $${i++}`); params.push(status); }
  params.push(id);
  const { rows } = await pool.query(
    `UPDATE batch_runs SET ${sets.join(', ')} WHERE id = $${i} RETURNING *`, params);
  return rows[0] || null;
}

async function seedSlicerCompat() {
  const { rows } = await pool.query('SELECT COUNT(*)::int AS n FROM slicer_compat');
  if (rows[0].n > 0) return; // already seeded
  let data;
  try {
    data = JSON.parse(readFileSync(new URL('./data/slicer_compat.json', import.meta.url), 'utf8'));
  } catch (e) {
    console.warn('slicer_compat seed skipped (dataset missing):', e.message);
    return;
  }
  if (!Array.isArray(data) || data.length === 0) return;
  // Bulk insert in batches (3 params/row; stay well under the 65535 param cap).
  const BATCH = 1000;
  for (let i = 0; i < data.length; i += BATCH) {
    const chunk = data.slice(i, i + BATCH);
    const values = [];
    const params = [];
    chunk.forEach((r, j) => {
      const b = j * 3;
      values.push(`($${b + 1}, $${b + 2}, $${b + 3})`);
      params.push(r[0], r[1], r[2]);
    });
    await pool.query(
      `INSERT INTO slicer_compat (kind, preset_name, printer_name) VALUES ${values.join(', ')}`,
      params
    );
  }
  console.log(`slicer_compat seeded: ${data.length} rows`);
}

// Compatible process + filament preset names for a given printer preset name.
export async function getCompatiblePresets(printerName) {
  const { rows } = await pool.query(
    'SELECT kind, preset_name FROM slicer_compat WHERE printer_name = $1',
    [printerName]
  );
  const out = { process: [], filament: [] };
  for (const r of rows) {
    if (out[r.kind]) out[r.kind].push(r.preset_name);
  }
  return out;
}

export async function upsertUser(email, displayName) {
  // The first-ever account is bootstrapped as owner/admin.
  const first = (await pool.query('SELECT 1 FROM users LIMIT 1')).rowCount === 0;
  const { rows } = await pool.query(
    `INSERT INTO users (email, display_name, is_owner) VALUES ($1, $2, $3)
     ON CONFLICT (email) DO UPDATE SET display_name = COALESCE(EXCLUDED.display_name, users.display_name)
     RETURNING *`,
    [email, displayName || null, first]
  );
  return rows[0];
}

export async function getUserByEmail(email) {
  const { rows } = await pool.query('SELECT * FROM users WHERE email = $1', [email]);
  return rows[0] || null;
}

export async function countUsers() {
  const { rows } = await pool.query('SELECT COUNT(*)::int AS n FROM users');
  return rows[0].n;
}

export async function getInstanceForUser(userId) {
  const { rows } = await pool.query('SELECT * FROM instances WHERE user_id = $1', [userId]);
  return rows[0] || null;
}

// ---- invite codes -------------------------------------------------------
export async function createInvite(createdBy, { email, note, ttlDays = 2 } = {}) {
  const code = 'ophq-' + crypto.randomBytes(9).toString('base64url'); // ~12 url-safe chars
  const { rows } = await pool.query(
    `INSERT INTO invite_codes (code, created_by, email, note, expires_at)
     VALUES ($1, $2, $3, $4, now() + ($5 || ' days')::interval) RETURNING *`,
    [code, createdBy || null, (email || '').trim().toLowerCase() || null, (note || '').slice(0, 200) || null, String(ttlDays)]
  );
  return rows[0];
}
// A code is redeemable only if it exists, is unused, and hasn't expired.
export async function getValidInvite(code) {
  if (!code) return null;
  const { rows } = await pool.query(
    `SELECT * FROM invite_codes WHERE code = $1 AND used_at IS NULL AND expires_at > now()`,
    [String(code).trim()]
  );
  return rows[0] || null;
}
// Mark used; the WHERE guard makes redemption atomic (no double-spend race).
export async function consumeInvite(code, usedBy) {
  const { rows } = await pool.query(
    `UPDATE invite_codes SET used_by = $2, used_at = now()
     WHERE code = $1 AND used_at IS NULL AND expires_at > now() RETURNING *`,
    [String(code).trim(), usedBy]
  );
  return rows[0] || null;
}
export async function listInvites() {
  const { rows } = await pool.query(
    `SELECT c.code, c.email, c.note, c.expires_at, c.used_at, c.created_at,
            cu.email AS created_by_email, uu.email AS used_by_email,
            CASE WHEN c.used_at IS NOT NULL THEN 'used'
                 WHEN c.expires_at <= now() THEN 'expired' ELSE 'active' END AS status
     FROM invite_codes c
     LEFT JOIN users cu ON cu.id = c.created_by
     LEFT JOIN users uu ON uu.id = c.used_by
     ORDER BY c.created_at DESC`);
  return rows;
}
export async function revokeInvite(code) {
  await pool.query('DELETE FROM invite_codes WHERE code = $1 AND used_at IS NULL', [String(code).trim()]);
}

// ---- admin: users + instances (owner-only reads) ------------------------
export async function listUsers() {
  const { rows } = await pool.query(
    `SELECT u.id, u.email, u.display_name, u.created_at,
            i.subdomain, i.status AS instance_status, i.port
     FROM users u LEFT JOIN instances i ON i.user_id = u.id
     ORDER BY u.id`);
  return rows;
}
export async function setInstanceFeature(instanceId, key, enabled) {
  const { rows } = await pool.query(
    `UPDATE instances SET features = jsonb_set(coalesce(features, '{}'::jsonb), $2::text[], $3::jsonb)
     WHERE id = $1 RETURNING id, subdomain, features`,
    [instanceId, [key], JSON.stringify(!!enabled)]);
  return rows[0] || null;
}
// Per-instance file-storage quota in MB (NULL = unlimited). Store + display only.
export async function setInstanceQuota(instanceId, mb) {
  const val = (mb === null || mb === undefined) ? null : Number(mb);
  const { rows } = await pool.query(
    `UPDATE instances SET storage_quota_mb = $2
     WHERE id = $1 RETURNING id, subdomain, storage_quota_mb`,
    [instanceId, val]);
  return rows[0] || null;
}

export async function listAllInstances() {
  const { rows } = await pool.query(
    `SELECT i.id, i.user_id, i.subdomain, i.status, i.port, i.engine_version, i.features, i.storage_quota_mb, i.created_at,
            u.email AS user_email
     FROM instances i JOIN users u ON u.id = i.user_id
     ORDER BY i.id`);
  return rows;
}

// ---- per-tenant log destinations ----------------------------------------
export async function getUserLogSink(userId) {
  const { rows } = await pool.query('SELECT url FROM user_log_sinks WHERE user_id = $1', [userId]);
  return rows[0]?.url || '';
}
export async function setUserLogSink(userId, url) {
  const u = String(url || '').trim();
  if (!u) { await pool.query('DELETE FROM user_log_sinks WHERE user_id = $1', [userId]); return ''; }
  await pool.query(
    `INSERT INTO user_log_sinks (user_id, url) VALUES ($1, $2)
     ON CONFLICT (user_id) DO UPDATE SET url = EXCLUDED.url, updated_at = now()`, [userId, u]);
  return u;
}
export async function listUserLogSinks() {
  const { rows } = await pool.query('SELECT user_id, url FROM user_log_sinks');
  return rows;
}
