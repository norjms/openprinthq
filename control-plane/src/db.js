// OpenPrintHQ control-plane — database access + migrations
// SPDX-License-Identifier: AGPL-3.0-or-later
import pg from 'pg';

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
