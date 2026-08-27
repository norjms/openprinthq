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
import { pool, adminPool, getInstanceForUser, getTenantStorage } from './db.js';

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

// Host directory where the tenant object-store buckets are exposed, one
// subdirectory per bucket, by a host-side rclone mount. Each engine gets a bind
// mount of its OWN bucket only, read-only, so the plate a slicer wrote appears
// in the library with no transfer at all.
//
// Unset disables the whole feature, which is how a deployment without the mount
// (the test tier, or any self-hoster) keeps working unchanged.
const BUCKET_MOUNT_ROOT = process.env.OPHQ_ENGINE_BUCKET_MOUNT_ROOT || '';
// Where it lands inside the engine. Also the external_path registered with the
// engine's library, so the two must agree.
const BUCKET_MOUNT_TARGET = process.env.OPHQ_ENGINE_BUCKET_MOUNT_TARGET || '/app/external/bucket';
const BUCKET_FOLDER_NAME = process.env.OPHQ_ENGINE_BUCKET_FOLDER_NAME || 'OpenPrintHQ Files';

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

// Build the per-tenant engine DATABASE_URL. The engine is DB-agnostic and reads
// DATABASE_URL (SQLAlchemy async → the `postgresql+asyncpg` scheme); with it set,
// the engine keeps ALL state in its dedicated Postgres database instead of a local
// SQLite file. Postgres is the single source of truth for every tenant.
function engineDatabaseUrl(dbName) {
  if (!PG_PASS) return null; // no PG password configured → cannot wire Postgres
  const u = encodeURIComponent(PG_USER);
  const p = encodeURIComponent(PG_PASS);
  return `postgresql+asyncpg://${u}:${p}@${PG_HOST}:${PG_PORT}/${dbName}`;
}

async function startEngineContainer({ subdomain, port, dbName, bucketDir = null }) {
  if (!ENGINE_IMAGE) return { started: false, reason: 'no-engine-image' };
  const name = `ophq-${subdomain}`;
  const databaseUrl = engineDatabaseUrl(dbName);
  if (!databaseUrl) return { started: false, reason: 'no-pg-config' };
  // The engine (Bambuddy-derived) listens on PORT (default 8000). Its database is
  // the dedicated tenant Postgres (DATABASE_URL); /app/data holds only file state
  // (uploads, thumbnails, gcode, logs). Each tenant gets an isolated container +
  // named volumes, on the internal network only. (Host networking — needed for LAN
  // printer discovery — is a per-tenant hardening follow-up; see issue #8.)
  try {
    await exec('docker', ['rm', '-f', name]).catch(() => {});
    await exec('docker', [
      'run', '-d', '--name', name, '--restart', 'unless-stopped',
      '--network', DOCKER_NETWORK,
      '-e', 'PORT=8000',
      '-e', `TZ=${process.env.OPHQ_TZ || 'America/Chicago'}`,
      '-e', `SLICER_API_URL=${SLICER_URL}`,
      '-e', `DATABASE_URL=${databaseUrl}`,
      '-v', `ophq_${subdomain}_data:/app/data`,
      '-v', `ophq_${subdomain}_logs:/app/logs`,
      // rslave, because the source is a FUSE mount made on the host AFTER this
      // directory exists. A plain bind captures the empty directory as it was at
      // container start and never sees the filesystem that later appears there.
      // readonly is not belt-and-braces: writes go to the store via presigned
      // URLs, and a writable mount would let the engine mutate a tenant bucket
      // through a key that is shared across tenants.
      // The engine refuses to register an external folder unless the path is on
      // its allowlist, and answers with a 400 that reads like a deployment
      // misconfiguration rather than a missing argument. Set it alongside the
      // mount so the two can never disagree.
      ...(bucketDir
        ? ['-e', `BAMBUDDY_EXTERNAL_ROOTS=${BUCKET_MOUNT_TARGET}`,
           '--mount', `type=bind,source=${bucketDir},target=${BUCKET_MOUNT_TARGET},readonly,bind-propagation=rslave`]
        : []),
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
/**
 * The host directory holding this tenant's bucket, or null if the feature is
 * off or the tenant has no bucket yet.
 *
 * Deliberately does not create the directory: it is produced by the rclone
 * mount, and creating it ourselves would make a missing or dead mount look like
 * an empty library instead of failing visibly.
 */
async function bucketDirFor(userId) {
  if (!BUCKET_MOUNT_ROOT) return null;
  try {
    const row = await getTenantStorage(userId);
    if (!row?.bucket) return null;
    return `${BUCKET_MOUNT_ROOT.replace(/\/+$/, '')}/${row.bucket}`;
  } catch { return null; }
}

/**
 * Register the bind-mounted bucket with the engine's library as a read-only
 * external folder, so a plate written straight to object storage shows up
 * without anything moving it.
 *
 * Idempotent by name: provisioning is retried, and the engine has no upsert.
 */
async function registerBucketFolder(subdomain) {
  const base = `http://ophq-${subdomain}:8000`;
  try {
    const list = await fetch(base + '/api/v1/library/folders', { headers: { accept: 'application/json' } });
    if (list.ok) {
      const d = await list.json().catch(() => []);
      const arr = Array.isArray(d) ? d : (d.folders || d.items || []);
      if (arr.some((f) => f?.name === BUCKET_FOLDER_NAME)) return true;
    }
    const created = await fetch(base + '/api/v1/library/folders/external', {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({
        name: BUCKET_FOLDER_NAME,
        external_path: BUCKET_MOUNT_TARGET,
        // Never writable. The store is written via presigned URLs; the mount
        // uses a key shared across tenants and must not be a write path.
        readonly: true,
        show_hidden: false
      })
    });
    if (!created.ok) return false;
    // Register alone leaves the folder empty: the engine indexes on scan, not on
    // open. Key prefixes become child folders, so a plate under plates/ lands in
    // a plates subfolder rather than at the root.
    const folder = await created.json().catch(() => null);
    if (folder?.id != null) {
      await fetch(`${base}/api/v1/library/folders/${folder.id}/scan`, { method: 'POST' }).catch(() => {});
    }
    return true;
  } catch { return false; }
}

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
  const bucketDir = await bucketDirFor(user.id);
  const engine = await startEngineContainer({ subdomain, port, dbName, bucketDir });
  if (engine.started) {
    configureEngine(subdomain); // fire-and-forget slicer setup
    if (bucketDir) registerBucketFolder(subdomain);
  }

  const status = engine.started ? 'running' : 'provisioned';
  const { rows: upd } = await pool.query(
    `UPDATE instances SET port = $1, status = $2 WHERE id = $3 RETURNING *`,
    [port, status, inst.id]
  );
  return { ...upd[0], engine };
}
