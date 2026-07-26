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
const ENGINE_HOST = process.env.OPHQ_ENGINE_HOST || '10.10.10.109';
const SLICER_URL = process.env.OPHQ_SLICER_URL || 'http://orca-slicer-api:3000'; // bundled OrcaSlicer API service
// Tenant engines join this internal Docker network (same as the control-plane)
// and are NOT published to the host — reachable only by container name from
// inside the network, never from the LAN.
const DOCKER_NETWORK = process.env.OPHQ_DOCKER_NETWORK || 'openprinthq_default';
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

async function startEngineContainer({ subdomain, port }) {
  if (!ENGINE_IMAGE) return { started: false, reason: 'no-engine-image' };
  const name = `ophq-${subdomain}`;
  // The engine (Bambuddy-derived) listens on PORT (default 8000) and keeps its
  // state in /app/data. Each tenant gets an isolated container + named volumes,
  // bridged with a unique host port. (Host networking — needed for LAN printer
  // discovery — is a per-tenant hardening follow-up; see issue #8.)
  try {
    await exec('docker', ['rm', '-f', name]).catch(() => {});
    await exec('docker', [
      'run', '-d', '--name', name, '--restart', 'unless-stopped',
      '--network', DOCKER_NETWORK,
      '-e', 'PORT=8000',
      '-e', `TZ=${process.env.OPHQ_TZ || 'America/Chicago'}`,
      '-e', `SLICER_API_URL=${SLICER_URL}`,
      '-v', `ophq_${subdomain}_data:/app/data`,
      '-v', `ophq_${subdomain}_logs:/app/logs`,
      '--label', `openprinthq.tenant=${subdomain}`,
      ENGINE_IMAGE
    ]);
    return { started: true, port };
  } catch (e) {
    return { started: false, reason: e.message };
  }
}

// After the engine boots, enable the built-in OrcaSlicer (shared sidecar).
// Best-effort: never fail provisioning if the engine is slow or the call errors.
async function configureEngine(subdomain) {
  const base = `http://ophq-${subdomain}:8000`;
  for (let i = 0; i < 20; i++) {
    try {
      const r = await fetch(base + '/api/v1/settings', { method: 'GET' });
      if (r.ok) {
        await fetch(base + '/api/v1/settings', {
          method: 'PATCH',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            use_slicer_api: true,
            preferred_slicer: 'orcaslicer',
            orcaslicer_api_url: SLICER_URL
          })
        }).catch(() => {});
        return true;
      }
    } catch { /* engine still booting */ }
    await new Promise((res) => setTimeout(res, 1500));
  }
  return false;
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
  const engine = await startEngineContainer({ subdomain, port });
  if (engine.started) configureEngine(subdomain); // fire-and-forget slicer setup

  const status = engine.started ? 'running' : 'provisioned';
  const { rows: upd } = await pool.query(
    `UPDATE instances SET port = $1, status = $2 WHERE id = $3 RETURNING *`,
    [port, status, inst.id]
  );
  return { ...upd[0], engine };
}
