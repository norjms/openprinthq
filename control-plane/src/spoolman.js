// OpenPrintHQ control-plane - per-tenant Spoolman
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Filament inventory lives in Spoolman, one instance per tenant. The engine
// already speaks Spoolman natively; this module gives each tenant their own
// Spoolman and moves the engine onto it.
//
// Isolation. Spoolman has no authentication at all: anything that can reach it
// can read and rewrite that tenant's inventory. So it never joins the shared
// network. Each tenant gets a dedicated INTERNAL network holding exactly three
// members: their Spoolman, their engine, and Postgres. No other tenant's engine
// can resolve or route to it, the edge never proxies to it, and --internal
// denies it outbound access it has no need for.
//
// Database. A dedicated Postgres database AND role per tenant, so the Spoolman
// container never holds the shared application credential. The role password
// is derived from the Postgres admin password with an HMAC, so it needs no
// storage and is reproduced identically on every reconcile.
//
// Reconcile, not provision-once. A promotion recreates the engine container and
// the recreate carries only its primary network, which silently cuts the engine
// off from its Spoolman. `ensureSpoolman` is therefore idempotent and cheap on
// the happy path, and is called from the engine gateway (throttled) and at boot,
// not only when a tenant is created.

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createHmac } from 'node:crypto';
import { adminPool } from './db.js';

const exec = promisify(execFile);

const IMAGE = process.env.OPHQ_SPOOLMAN_IMAGE || '';
const PG_HOST = process.env.OPHQ_PG_HOST || '10.10.10.254';
const PG_PORT = process.env.OPHQ_PG_PORT || '5432';
const PG_PASS = process.env.OPHQ_PG_PASS || '';
// The Postgres container to attach to each tenant network. Found by its compose
// service label when unset, which is right for the standard deploy stack.
const PG_CONTAINER = process.env.OPHQ_PG_CONTAINER || '';
const RECONCILE_MS = Number(process.env.OPHQ_SPOOLMAN_RECONCILE_MS || 120000);

export function spoolmanEnabled() { return !!IMAGE && !!PG_PASS; }

const slug = (subdomain) => subdomain.replace(/-/g, '_');
export const spoolmanName = (subdomain) => `ophq-spoolman-${subdomain}`;
export const spoolmanNet = (subdomain) => `ophq-spoolman-net-${subdomain}`;
export const spoolmanUrl = (subdomain) => `http://${spoolmanName(subdomain)}:8000`;
const dbName = (subdomain) => `spoolman_${slug(subdomain)}`;
const roleName = (subdomain) => `spoolman_${slug(subdomain)}`;
const rolePassword = (subdomain) => createHmac('sha256', PG_PASS).update('spoolman:' + subdomain).digest('hex');

function assertIdent(s) {
  if (!/^[a-z][a-z0-9_]{2,62}$/.test(s)) throw new Error(`unsafe identifier: ${s}`);
  return s;
}

async function ensureDatabase(subdomain) {
  const role = assertIdent(roleName(subdomain));
  const db = assertIdent(dbName(subdomain));
  const pw = rolePassword(subdomain); // hex only, safe to inline
  const { rows } = await adminPool.query('SELECT 1 FROM pg_roles WHERE rolname = $1', [role]);
  if (rows.length === 0) {
    await adminPool.query(`CREATE ROLE "${role}" LOGIN PASSWORD '${pw}'`);
  } else {
    // Cheap, and it heals a role whose password predates a PG_PASS change.
    await adminPool.query(`ALTER ROLE "${role}" LOGIN PASSWORD '${pw}'`);
  }
  try {
    await adminPool.query(`CREATE DATABASE "${db}" OWNER "${role}"`);
  } catch (e) {
    if (!/already exists/i.test(e.message)) throw e;
  }
  // Only its owner connects. PUBLIC gets CONNECT on every new database by
  // default, which would let any other tenant's role open this one.
  await adminPool.query(`REVOKE CONNECT ON DATABASE "${db}" FROM PUBLIC`);
}

async function pgContainer() {
  if (PG_CONTAINER) return PG_CONTAINER;
  const { stdout } = await exec('docker', ['ps', '-q', '--filter', 'label=com.docker.compose.service=postgres']);
  const id = stdout.trim().split('\n')[0];
  if (!id) throw new Error('postgres container not found (set OPHQ_PG_CONTAINER)');
  return id;
}

async function connect(net, container, alias) {
  const args = ['network', 'connect'];
  if (alias) args.push('--alias', alias);
  args.push(net, container);
  try {
    await exec('docker', args);
    return true;
  } catch (e) {
    if (/already exists|already connected/i.test(e.message || '')) return false;
    throw e;
  }
}

async function containerState(name) {
  try {
    const { stdout } = await exec('docker', [
      'inspect', '-f', '{{.State.Running}}|{{.Image}}|{{range .Config.Env}}{{println .}}{{end}}', name
    ]);
    const [running, image, ...env] = stdout.split('|');
    return { exists: true, running: running === 'true', image: image.trim(), env: env.join('|') };
  } catch {
    return { exists: false };
  }
}

async function wantImageId() {
  try {
    const { stdout } = await exec('docker', ['image', 'inspect', '-f', '{{.Id}}', IMAGE]);
    return stdout.trim();
  } catch {
    await exec('docker', ['pull', IMAGE]);
    const { stdout } = await exec('docker', ['image', 'inspect', '-f', '{{.Id}}', IMAGE]);
    return stdout.trim();
  }
}

async function ensureContainer(subdomain) {
  const name = spoolmanName(subdomain);
  const net = spoolmanNet(subdomain);
  const pw = rolePassword(subdomain);
  const st = await containerState(name);
  const imageId = await wantImageId();
  const current = st.exists && st.running && st.image === imageId && st.env.includes(`SPOOLMAN_DB_PASSWORD=${pw}`);
  if (current) return { changed: false };

  // Spoolman's state is entirely in Postgres, so a recreate loses nothing.
  // Stop first anyway so an in-flight write completes.
  if (st.exists) {
    await exec('docker', ['stop', '-t', '20', name]).catch(() => {});
    await exec('docker', ['rm', '-f', name]).catch(() => {});
  }
  await exec('docker', [
    'run', '-d', '--name', name, '--restart', 'unless-stopped',
    '--network', net,
    '-e', 'SPOOLMAN_DB_TYPE=postgres',
    '-e', `SPOOLMAN_DB_HOST=${PG_HOST}`,
    '-e', `SPOOLMAN_DB_PORT=${PG_PORT}`,
    '-e', `SPOOLMAN_DB_NAME=${dbName(subdomain)}`,
    '-e', `SPOOLMAN_DB_USERNAME=${roleName(subdomain)}`,
    '-e', `SPOOLMAN_DB_PASSWORD=${pw}`,
    // Backups are Postgres dumps of the tenant DB; Spoolman's own would write
    // SQLite-shaped files into a container layer that a recreate discards.
    '-e', 'SPOOLMAN_AUTOMATIC_BACKUP=FALSE',
    '-e', `TZ=${process.env.OPHQ_TZ || 'America/Chicago'}`,
    '--label', `openprinthq.tenant=${subdomain}`,
    '--label', 'openprinthq.role=spoolman',
    IMAGE
  ]);
  return { changed: true };
}

async function engineJson(subdomain, path, init = {}) {
  const res = await fetch(`http://ophq-${subdomain}:8000${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init.headers || {}) },
    signal: AbortSignal.timeout(init.timeoutMs || 15000)
  });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  return { status: res.status, body };
}

/**
 * Move the engine onto its Spoolman. A no-op once done.
 *
 * The engine's migration endpoint copies any built-in spools across and then
 * switches mode, so an existing tenant keeps their inventory and a new tenant
 * simply lands in Spoolman mode with nothing to copy. It refuses to switch if
 * Spoolman is unreachable or any spool fails, so retrying is always safe.
 */
async function ensureEngineMode(subdomain, attempts) {
  const url = spoolmanUrl(subdomain);
  let last = null;
  for (let i = 0; i < attempts; i++) {
    try {
      const s = await engineJson(subdomain, '/api/v1/spoolman/migration');
      if (s.status === 404) return { mode: 'internal', reason: 'engine-without-migration' };
      if (s.status === 200 && s.body?.spoolman_enabled && s.body.spoolman_url === url) {
        return { mode: 'spoolman', migrated: false };
      }
      if (s.status === 200) {
        const r = await engineJson(subdomain, '/api/v1/spoolman/migration', {
          method: 'POST',
          body: JSON.stringify({ spoolman_url: url }),
          timeoutMs: 120000
        });
        if (r.status === 200 && r.body?.mode_enabled) return { mode: 'spoolman', migrated: true, report: r.body };
        last = r.body?.detail || r.body || `HTTP ${r.status}`;
      } else {
        last = `engine HTTP ${s.status}`;
      }
    } catch (e) {
      last = e.message;
    }
    await new Promise((res) => setTimeout(res, 3000));
  }
  return { mode: 'internal', reason: 'migration-not-complete', detail: last };
}

const inflight = new Map(); // subdomain -> Promise
const lastOk = new Map();   // subdomain -> timestamp of last success
const lastTry = new Map();  // subdomain -> timestamp of last attempt
const RETRY_MS = 5 * 60 * 1000;

/**
 * Ensure the tenant has a running Spoolman on its private network, that the
 * engine is attached to it, and that the engine is in Spoolman mode.
 *
 * `force` skips the throttle. Concurrent calls for one tenant share a promise.
 */
export async function ensureSpoolman(subdomain, { force = false, attempts = 20 } = {}) {
  if (!spoolmanEnabled()) return { mode: 'internal', reason: 'disabled' };
  if (!subdomain) return { mode: 'internal', reason: 'no-instance' };
  if (!force && Date.now() - (lastOk.get(subdomain) || 0) < RECONCILE_MS) return { mode: 'spoolman', cached: true };
  if (inflight.has(subdomain)) return inflight.get(subdomain);
  lastTry.set(subdomain, Date.now());

  const p = (async () => {
    const net = spoolmanNet(subdomain);
    await exec('docker', ['network', 'create', '--internal', net]).catch(() => {});
    await ensureDatabase(subdomain);
    await connect(net, await pgContainer(), PG_HOST);
    const c = await ensureContainer(subdomain);
    const engineAttached = await connect(net, `ophq-${subdomain}`);
    const result = await ensureEngineMode(subdomain, attempts);
    if (result.mode === 'spoolman') lastOk.set(subdomain, Date.now());
    return { ...result, containerChanged: c.changed, engineReattached: engineAttached };
  })().finally(() => inflight.delete(subdomain));

  inflight.set(subdomain, p);
  return p;
}

/** Throttled fire-and-forget form for hot paths. */
export function touchSpoolman(subdomain, log) {
  if (!spoolmanEnabled() || !subdomain) return;
  const now = Date.now();
  if (now - (lastOk.get(subdomain) || 0) < RECONCILE_MS || inflight.has(subdomain)) return;
  // A tenant whose reconcile is failing is retried every few minutes, not on
  // every request: one attempt can take a minute of polling.
  if (now - (lastTry.get(subdomain) || 0) < RETRY_MS) return;
  ensureSpoolman(subdomain)
    .then((r) => {
      if (r.migrated || r.containerChanged || r.engineReattached || r.mode !== 'spoolman') {
        log?.info?.({ subdomain, ...r, report: undefined }, 'spoolman reconcile');
      }
    })
    .catch((e) => log?.warn?.({ subdomain, err: e.message }, 'spoolman reconcile failed'));
}
