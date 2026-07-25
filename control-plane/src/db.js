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
  await seedSlicerCompat();
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
