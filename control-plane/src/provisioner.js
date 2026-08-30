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
import { vaultAuthSecret, vaultServiceHeaders } from './vault-auth.js';
import { randomBytes } from 'node:crypto';

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

// The per-tenant model library: our fork of GyroidVault (AGPL-3.0), which
// takes its identity from the headers we assert rather than holding accounts of
// its own. Unset
// disables it and the Files tab falls back to the engine library, which is what
// a deployment without it should do rather than showing a broken tab.
//
// The upstream public image is run as-is and themed at start by copying one
// stylesheet in. An earlier version built a derived image, which worked but
// published to a package GitHub only lets you make public through the web UI,
// so every host would have needed registry credentials to pull it. Theming in
// place removes the artifact, the credential and the visibility problem, and
// upstream upgrades become a tag change.
const VAULT_IMAGE = process.env.OPHQ_VAULT_IMAGE || '';
// Where the tenant bucket appears inside the library container.
const VAULT_LIBRARY_PATH = '/library';
export function vaultEnabled() { return !!VAULT_IMAGE; }
export function vaultBase(subdomain) { return `http://ophq-vault-${subdomain}:3000`; }

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
async function registerBucketFolder(subdomain, attempts = 20) {
  const base = `http://ophq-${subdomain}:8000`;
  // Retry, because both callers reach here against an engine that has just been
  // started: first provision, and the reconcile immediately after it recreates a
  // container. A single attempt races the engine's boot and loses silently, and
  // the symptom is an empty library folder rather than an error anywhere. This
  // mirrors configureEngine, which retries beside it for the same reason.
  for (let i = 0; i < attempts; i++) {
    if (await tryRegisterBucketFolder(base)) return true;
    await new Promise((res) => setTimeout(res, 1500));
  }
  return false;
}

async function tryRegisterBucketFolder(base) {
  try {
    const list = await fetch(base + '/api/v1/library/folders', { headers: { accept: 'application/json' } });
    if (list.ok) {
      const d = await list.json().catch(() => []);
      const arr = Array.isArray(d) ? d : (d.folders || d.items || []);
      if (arr.some((f) => f?.name === BUCKET_FOLDER_NAME)) return true;
    } else {
      // Engine is up but not ready to answer; let the caller retry rather than
      // POSTing into a half-started service.
      return false;
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

/**
 * Make an EXISTING engine carry its tenant's bucket mount.
 *
 * The mount is applied when a container is created, but storage is provisioned
 * on first use, which is usually later. Without this, a tenant who existed
 * before storage did would get a bucket that never appears in their library and
 * nothing would report a problem.
 *
 * Idempotent: it inspects first and returns untouched when the mount is already
 * there, so it is safe to call on every storage lookup.
 */
export async function ensureEngineBucketMount(userId, subdomain) {
  const bucketDir = await bucketDirFor(userId);
  if (!bucketDir) return { changed: false, reason: 'no-bucket-or-disabled' };
  const name = `ophq-${subdomain}`;

  let inspect;
  try {
    const { stdout } = await exec('docker', ['inspect', name]);
    inspect = JSON.parse(stdout)[0];
  } catch { return { changed: false, reason: 'no-container' }; }

  const already = (inspect?.Mounts || []).some(
    (m) => m.Type === 'bind' && m.Destination === BUCKET_MOUNT_TARGET);
  if (already) {
    // Register anyway: the mount can outlive a library whose folder row was
    // never created, and registration is itself idempotent.
    await registerBucketFolder(subdomain);
    return { changed: false, reason: 'already-mounted' };
  }

  // The source has to exist before the container starts. A missing directory
  // binds as an empty one rather than failing, which would look like an empty
  // library instead of a broken mount.
  try { await exec('test', ['-d', bucketDir]); }
  catch { return { changed: false, reason: 'mount-root-missing' }; }

  const image = inspect.Config.Image;
  const network = Object.keys(inspect.NetworkSettings?.Networks || {})[0] || DOCKER_NETWORK;
  const volumes = (inspect.Mounts || [])
    .filter((m) => m.Type === 'volume')
    .flatMap((m) => ['-v', `${m.Name}:${m.Destination}`]);
  const labels = Object.entries(inspect.Config?.Labels || {})
    .filter(([k]) => k.startsWith('openprinthq.'))
    .flatMap(([k, v]) => ['--label', `${k}=${v}`]);

  // Env goes through a file, never argv: it carries the tenant database
  // password and argv is world-readable in the process list.
  const envFile = `/tmp/ophq-reconcile-${subdomain}-${Date.now()}.env`;
  const env = (inspect.Config?.Env || []).filter((e) => !e.startsWith('BAMBUDDY_EXTERNAL_ROOTS='));
  const { writeFile, unlink } = await import('node:fs/promises');
  await writeFile(envFile, env.join('\n') + '\n', { mode: 0o600 });

  // Keep the old container under a dated name rather than deleting it, so a
  // failed start is a rename away from being undone.
  const backup = `${name}-pre-mount-${Date.now()}`;
  try {
    await exec('docker', ['stop', name]);
    await exec('docker', ['rename', name, backup]);
    await exec('docker', [
      'run', '-d', '--name', name, '--restart', 'unless-stopped',
      '--network', network,
      '--env-file', envFile,
      '-e', `BAMBUDDY_EXTERNAL_ROOTS=${BUCKET_MOUNT_TARGET}`,
      ...volumes, ...labels,
      '--mount', `type=bind,source=${bucketDir},target=${BUCKET_MOUNT_TARGET},readonly,bind-propagation=rslave`,
      image
    ]);
  } catch (e) {
    await exec('docker', ['rm', '-f', name]).catch(() => {});
    await exec('docker', ['rename', backup, name]).catch(() => {});
    await exec('docker', ['start', name]).catch(() => {});
    await unlink(envFile).catch(() => {});
    return { changed: false, reason: 'recreate-failed: ' + e.message };
  }
  await unlink(envFile).catch(() => {});
  await registerBucketFolder(subdomain);
  return { changed: true, backup };
}

/**
 * Start a tenant's library container.
 *
 * The bucket is mounted READ-ONLY on purpose. The library only ever reads: it
 * scans and indexes, and its own state lives in a separate volume. Uploads go
 * through the presigned path instead, so the shared reader key backing this
 * mount can never become a cross-tenant write path.
 */
async function startVaultContainer({ subdomain, bucketDir }) {
  if (!VAULT_IMAGE || !bucketDir) return { started: false, reason: 'disabled-or-no-bucket' };
  const name = `ophq-vault-${subdomain}`;
  try { await exec('test', ['-d', bucketDir]); }
  catch { return { started: false, reason: 'mount-root-missing' }; }
  try {
    await exec('docker', ['rm', '-f', name]).catch(() => {});

    // A DEDICATED, INTERNAL network per tenant library, not the shared one.
    //
    // GyroidVault leaves a number of read routes ungated, including
    // /api/files/:id/download, which serves a file to an unauthenticated
    // caller. Our edge keeps that off the internet and the proxy only ever
    // resolves a tenant to their own container, so no tenant can reach
    // another's through the app. But on a shared network ANY container could
    // read ANY tenant's library directly, so the isolation is done at the
    // network rather than relying on the application's own checks.
    //
    // --internal also denies the library outbound access, which it does not
    // need: everything it reads is a local mount.
    const net = `ophq-vault-net-${subdomain}`;
    await exec('docker', ['network', 'create', '--internal', net]).catch(() => {});
    await exec('docker', [
      'run', '-d', '--name', name, '--restart', 'unless-stopped',
      '--network', net,
      '-e', 'NODE_ENV=production', '-e', 'PORT=3000',
      '-e', `LIBRARY_PATH=${VAULT_LIBRARY_PATH}`,
      '-e', `OPHQ_AUTH_SECRET=${vaultAuthSecret()}`,
      '-e', `OPHQ_LOGOUT_URL=${process.env.OPHQ_VAULT_LOGOUT_URL || ''}`,
      '-e', 'OPHQ_IDP_NAME=OpenPrintHQ',
      '-v', `ophq_${subdomain}_vault:/app/data`,
      '--mount', `type=bind,source=${bucketDir},target=${VAULT_LIBRARY_PATH},readonly,bind-propagation=rslave`,
      '--label', `openprinthq.tenant=${subdomain}`,
      '--label', 'openprinthq.role=vault',
      VAULT_IMAGE
    ]);
    await joinVaultNetwork(subdomain);
    return { started: true };
  } catch (e) { return { started: false, reason: e.message }; }
}

/**
 * Get a freshly started library ready to be used.
 *
 * There is no account to claim any more: the library trusts the identity we
 * assert on every request, so all that is left is waiting for it to answer and
 * pointing it at our print host.
 *
 * Readiness is polled on /api/health, the one route that needs no identity,
 * so a container that is still opening its database cannot be mistaken for one
 * that is refusing us.
 */
export async function bootstrapVault(userId, subdomain, email, attempts = 20) {
  const base = vaultBase(subdomain);
  let ready = false;
  for (let i = 0; i < attempts; i++) {
    try {
      const r = await fetch(base + '/api/health');
      if (r.ok) { ready = true; break; }
    } catch { /* still booting */ }
    await new Promise((r) => setTimeout(r, 1500));
  }
  if (!ready) return false;
  await seedVaultPrinter(base, userId);
  return true;
}

/**
 * Point the library's "printer" at our print host.
 *
 * It speaks Moonraker, so rather than teaching it our API we expose a
 * Moonraker-shaped upload endpoint and configure it as an ordinary printer.
 * The api_key is a print-host token, the same credential a slicer session uses.
 */
async function seedVaultPrinter(base, userId) {
  const { createPrintHostToken, purgePrintHostTokens } = await import('./db.js');
  const token = randomBytes(24).toString('base64url');
  await purgePrintHostTokens(userId).catch(() => {});
  await createPrintHostToken(userId, token, 'vault', null);
  // The INTERNAL address, not the public one. The library container sits on the
  // same docker network as the control-plane, so sending a plate to the public
  // hostname would hairpin out to the internet and back, and on a deployment
  // behind a CDN it does not even arrive: the edge answers the container with a
  // challenge page and the upload fails with an HTML body that looks nothing
  // like a Moonraker error.
  const printers = [{
    id: 'openprinthq',
    name: 'OpenPrintHQ Queue',
    url: (process.env.OPHQ_INTERNAL_URL || 'http://control-plane:8080') + '/printhost',
    api_key: token
  }];
  await fetch(base + '/api/settings/system', {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...vaultServiceHeaders() },
    // Scan hourly rather than daily: files arrive from outside this container,
    // so its own watcher never sees the write that created them.
    body: JSON.stringify({ printers: JSON.stringify(printers), auto_scan_interval: '1' })
  }).catch(() => {});
}

/** Trigger a library scan. Called after an upload, since the store is written
 *  from outside this container and nothing tells it a file appeared. */
export async function vaultScan(subdomain) {
  const r = await fetch(vaultBase(subdomain) + '/api/library/scan', {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...vaultServiceHeaders() },
    body: '{}'
  }).catch(() => null);
  return !!r && r.ok;
}

/**
 * Attach the control-plane to a tenant library's network.
 *
 * Idempotent, and called on EVERY reconcile rather than only at container
 * creation. `docker network connect` is runtime state that compose knows
 * nothing about, so recreating the control-plane, which every promotion does,
 * silently drops the attachment and the proxy can no longer reach any library.
 * The symptom is "library unreachable: fetch failed" with both containers
 * healthy, which points at the library rather than at the network.
 *
 * The alias matters too: on a user-defined network a container answers to its
 * own name, not its compose service name, so the library's configured
 * print-host URL would not resolve without it.
 */
export async function joinVaultNetwork(subdomain) {
  const self = process.env.HOSTNAME || '';
  if (!self) return false;
  const net = `ophq-vault-net-${subdomain}`;
  try {
    await exec('docker', ['network', 'connect', '--alias', 'control-plane', net, self]);
    return true;
  } catch (e) {
    // Already attached is the normal case and not an error.
    return /already exists|already connected/i.test(e.message || '');
  }
}

/**
 * Is this container still the one we would start today?
 *
 * Two ways it can be stale, and both present as something other than staleness:
 *
 *   Secret. A library started with a different OPHQ_AUTH_SECRET refuses every
 *   request we make, so it looks broken rather than out of date. A container
 *   with no secret at all is left alone: that is a deployment running without a
 *   gateway secret, not a mismatch.
 *
 *   Image. This is compared by resolved image ID, not by the tag string,
 *   because promotion RETAGS: :test and :prod are moving tags pointed at new
 *   bytes, so a tag comparison would call a year-old container current. The
 *   container's .Image is already an ID; the desired one is whatever
 *   OPHQ_VAULT_IMAGE resolves to locally right now.
 *
 * If the desired image cannot be resolved at all, this answers "matches" and
 * leaves the container alone. That is the case where the image was never
 * pre-pulled onto this host, and recreating would tear down a working library
 * to start a container whose image cannot be fetched: the control-plane holds
 * no registry credentials. Stale beats absent.
 *
 * Returns a reason so a recreate says WHY in the logs, since "the library
 * restarted" with no cause is the thing that wastes an afternoon later.
 */
async function vaultMatches(name) {
  const want = vaultAuthSecret();
  if (want) {
    try {
      const { stdout } = await exec('docker', [
        'inspect', '-f', '{{range .Config.Env}}{{println .}}{{end}}', name
      ]);
      const line = stdout.split('\n').find((l) => l.startsWith('OPHQ_AUTH_SECRET='));
      if (!line || line.slice('OPHQ_AUTH_SECRET='.length) !== want) {
        return { ok: false, reason: 'secret-changed' };
      }
    } catch { return { ok: true, reason: 'uninspectable' }; }
  }

  let wantId = '';
  try {
    const { stdout } = await exec('docker', ['image', 'inspect', '-f', '{{.Id}}', VAULT_IMAGE]);
    wantId = stdout.trim();
  } catch { return { ok: true, reason: 'image-unresolvable' }; }
  if (!wantId) return { ok: true, reason: 'image-unresolvable' };

  try {
    const { stdout } = await exec('docker', ['inspect', '-f', '{{.Image}}', name]);
    if (stdout.trim() !== wantId) return { ok: false, reason: 'image-changed' };
  } catch { return { ok: true, reason: 'uninspectable' }; }

  return { ok: true, reason: 'current' };
}

/** Ensure the tenant has a library container, idempotently. */
export async function ensureVault(userId, subdomain, email) {
  if (!VAULT_IMAGE) return { changed: false, reason: 'disabled' };
  const bucketDir = await bucketDirFor(userId);
  if (!bucketDir) return { changed: false, reason: 'no-bucket' };
  const name = `ophq-vault-${subdomain}`;
  let stale = '';
  try {
    const { stdout } = await exec('docker', ['inspect', '-f', '{{.State.Running}}', name]);
    if (stdout.trim() === 'true') {
      const m = await vaultMatches(name);
      if (m.ok) {
        // Re-attach first: the container can be running while the control-plane
        // has lost its route to it, which is exactly the state a promotion leaves.
        await joinVaultNetwork(subdomain);
        await bootstrapVault(userId, subdomain, email, 3);
        return { changed: false, reason: 'already-running' };
      }
      stale = m.reason;
    }
  } catch { /* not present */ }
  const r = await startVaultContainer({ subdomain, bucketDir });
  if (!r.started) return { changed: false, reason: r.reason };
  await bootstrapVault(userId, subdomain, email);
  return { changed: true, reason: stale || 'created' };
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
  // Fire-and-forget: a slow library must not hold up provisioning, and it is
  // reconciled on the storage path anyway.
  ensureVault(user.id, subdomain, user.email).catch(() => {});

  const status = engine.started ? 'running' : 'provisioned';
  const { rows: upd } = await pool.query(
    `UPDATE instances SET port = $1, status = $2 WHERE id = $3 RETURNING *`,
    [port, status, inst.id]
  );
  return { ...upd[0], engine };
}
