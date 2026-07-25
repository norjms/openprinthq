// OpenPrintHQ control-plane — per-user instance provisioner
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Instance-per-user model: each account gets its own isolated engine instance
// with a dedicated PostgreSQL database. This module allocates the subdomain,
// creates the tenant database, and (when an engine image is configured) starts
// a dedicated engine container. Container orchestration requires the Docker
// socket to be available to the control-plane (mounted in docker-compose).

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { pool, adminPool, getInstanceForUser } from './db.js';

const exec = promisify(execFile);

const BASE_PORT = Number(process.env.OPHQ_BASE_PORT || 39000);
const ENGINE_IMAGE = process.env.OPHQ_ENGINE_IMAGE || ''; // e.g. internal.example.com/openprinthq/openprinthq-engine:dev
const PG_HOST = process.env.OPHQ_PG_HOST || '10.10.10.254';
const PG_PORT = process.env.OPHQ_PG_PORT || '5432';
const PG_USER = process.env.OPHQ_PG_USER || 'ophq_app';
const PG_PASS = process.env.OPHQ_PG_PASS || '';

function slugify(email) {
  return (email.split('@')[0] || 'user')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 24) || 'user';
}

async function uniqueSubdomain(base) {
  let sub = base;
  for (let i = 0; i < 50; i++) {
    const { rows } = await pool.query('SELECT 1 FROM instances WHERE subdomain = $1', [sub]);
    if (rows.length === 0) return sub;
    sub = `${base}-${Math.floor(Math.random() * 900 + 100)}`;
  }
  return `${base}-${Date.now().toString(36)}`;
}

async function createTenantDb(dbName) {
  // dbName is derived from a slug we control, but validate hard before it ever
  // reaches a non-parameterizable CREATE DATABASE statement.
  if (!/^[a-z][a-z0-9_]{2,62}$/.test(dbName)) {
    throw new Error(`unsafe tenant db name: ${dbName}`);
  }
  try {
    await adminPool.query(`CREATE DATABASE "${dbName}" OWNER "${PG_USER}"`);
  } catch (e) {
    if (!/already exists/i.test(e.message)) throw e;
  }
}

async function startEngineContainer({ subdomain, dbName, port }) {
  if (!ENGINE_IMAGE) return { started: false, reason: 'no-engine-image' };
  const name = `ophq-${subdomain}`;
  const dbUrl = `postgresql://${PG_USER}:${PG_PASS}@${PG_HOST}:${PG_PORT}/${dbName}`;
  try {
    await exec('docker', ['rm', '-f', name]).catch(() => {});
    await exec('docker', [
      'run', '-d', '--name', name, '--restart', 'unless-stopped',
      '-p', `${port}:8080`,
      '-e', `DATABASE_URL=${dbUrl}`,
      '-e', `OPHQ_SUBDOMAIN=${subdomain}`,
      '--label', 'openprinthq.tenant=' + subdomain,
      ENGINE_IMAGE
    ]);
    return { started: true };
  } catch (e) {
    return { started: false, reason: e.message };
  }
}

export async function provisionForUser(user) {
  const existing = await getInstanceForUser(user.id);
  if (existing) return existing;

  const base = slugify(user.email);
  const subdomain = await uniqueSubdomain(base);
  const dbName = `tenant_${subdomain.replace(/-/g, '_')}`;

  const { rows } = await pool.query(
    `INSERT INTO instances (user_id, subdomain, db_name, status) VALUES ($1, $2, $3, 'provisioning') RETURNING *`,
    [user.id, subdomain, dbName]
  );
  const inst = rows[0];
  const port = BASE_PORT + inst.id;

  await createTenantDb(dbName);
  const engine = await startEngineContainer({ subdomain, dbName, port });

  const status = engine.started ? 'running' : 'provisioned';
  const { rows: upd } = await pool.query(
    `UPDATE instances SET port = $1, status = $2 WHERE id = $3 RETURNING *`,
    [port, status, inst.id]
  );
  return { ...upd[0], engine };
}
