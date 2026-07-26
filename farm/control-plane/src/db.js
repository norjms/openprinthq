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

  await pool.query(`
    CREATE TABLE IF NOT EXISTS signing_keys (
      user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      public_pem  TEXT NOT NULL,
      private_enc TEXT NOT NULL,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  `);

  await seedSlicerCompat();
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
export async function listConnectors(userId) {
  const { rows } = await pool.query(
    'SELECT id, name, last_seen, created_at, (client_public_pem IS NOT NULL) AS has_client_key FROM connectors WHERE user_id = $1 ORDER BY id', [userId]);
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
  const { rows } = await pool.query(
    `INSERT INTO users (email, display_name) VALUES ($1, $2)
     ON CONFLICT (email) DO UPDATE SET display_name = COALESCE(EXCLUDED.display_name, users.display_name)
     RETURNING *`,
    [email, displayName || null]
  );
  return rows[0];
}

export async function getUserByEmail(email) {
  const { rows } = await pool.query('SELECT * FROM users WHERE email = $1', [email]);
  return rows[0] || null;
}

export async function getInstanceForUser(userId) {
  const { rows } = await pool.query('SELECT * FROM instances WHERE user_id = $1', [userId]);
  return rows[0] || null;
}
