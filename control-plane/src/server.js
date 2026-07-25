// OpenPrintHQ control-plane — HTTP API
// SPDX-License-Identifier: AGPL-3.0-or-later
import Fastify from 'fastify';
import cookie from '@fastify/cookie';
import { migrate, upsertUser, getUserByEmail, getInstanceForUser, getCompatiblePresets } from './db.js';
import { provisionForUser } from './provisioner.js';

const PORT = Number(process.env.PORT || 8080);
const SESSION_SECRET = process.env.SESSION_SECRET || 'dev-insecure-secret-change-me-in-prod-000000';
const COOKIE = 'ophq_sess';
// Host on which per-tenant engine containers publish their ports (CT201 LAN IP).
const ENGINE_HOST = process.env.OPHQ_ENGINE_HOST || '10.10.10.109';
// Shared secret injected by npmplus on authenticated requests. When set, the
// Authentik identity headers are only trusted if this secret is present — so a
// LAN host reaching the app directly can't forge X-authentik-* identity.
const GATEWAY_SECRET = process.env.OPHQ_GATEWAY_SECRET || '';
// Dev sign-in is disabled unless explicitly enabled (production = SSO only).
const ALLOW_DEV_LOGIN = !!process.env.OPHQ_ALLOW_DEV_LOGIN;

function engineBase(inst) {
  // Engines are on the internal Docker network, not published to the host —
  // reach them by container name, never via a LAN-exposed port.
  return inst && inst.subdomain ? `http://ophq-${inst.subdomain}:8000` : null;
}

const app = Fastify({ logger: true, trustProxy: true, bodyLimit: 1024 * 1024 * 1024 });
await app.register(cookie, { secret: SESSION_SECRET });

// Buffer non-JSON bodies raw so the engine gateway can forward file uploads
// (multipart) and binary verbatim, preserving the original content-type/boundary.
app.addContentTypeParser(/^multipart\/form-data/, { parseAs: 'buffer' }, (req, body, done) => done(null, body));
app.addContentTypeParser('application/octet-stream', { parseAs: 'buffer' }, (req, body, done) => done(null, body));

function setSession(reply, email) {
  reply.setCookie(COOKIE, email, {
    path: '/', httpOnly: true, sameSite: 'lax', signed: true,
    maxAge: 60 * 60 * 24 * 30
  });
}

function currentEmail(req) {
  // The app is only reachable through the trusted proxy (npmplus), which runs
  // Authentik forward-auth and injects the authenticated identity as headers
  // (and strips any client-supplied copies). Trust X-authentik-email when set;
  // otherwise fall back to the signed dev-login session cookie.
  const ak = req.headers['x-authentik-email'];
  const gatewayOk = !GATEWAY_SECRET || req.headers['x-ophq-gateway'] === GATEWAY_SECRET;
  if (gatewayOk && ak && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(ak)) return String(ak).toLowerCase();
  const raw = req.cookies?.[COOKIE];
  if (!raw) return null;
  const un = app.unsignCookie(raw);
  return un.valid ? un.value : null;
}

async function requireUser(req, reply) {
  const email = currentEmail(req);
  if (!email) { reply.code(401).send({ error: 'not authenticated' }); return null; }
  let user = await getUserByEmail(email);
  // First SSO login: auto-create the account from the Authentik identity.
  if (!user) user = await upsertUser(email, req.headers['x-authentik-name'] || null);
  return user;
}

// ---- health -------------------------------------------------------------
app.get('/api/health', async () => ({ ok: true, service: 'control-plane', ts: Date.now() }));

// ---- auth ---------------------------------------------------------------
// Production: Authentik OIDC. Wiring tracked as a Gitea issue.
app.get('/api/auth/login', async (req, reply) => {
  if (!process.env.OIDC_ISSUER) {
    return reply.code(501).send({ error: 'SSO (Authentik OIDC) not configured yet. Use dev sign-in.' });
  }
  // TODO: begin OIDC authorization-code flow with PKCE.
  return reply.code(501).send({ error: 'OIDC flow not implemented yet' });
});

app.post('/api/auth/dev-login', async (req, reply) => {
  if (!ALLOW_DEV_LOGIN) {
    return reply.code(403).send({ error: 'dev sign-in is disabled — use SSO' });
  }
  const email = (req.body?.email || '').trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return reply.code(400).send({ error: 'valid email required' });
  }
  const user = await upsertUser(email);
  setSession(reply, user.email);
  return { ok: true, user: { id: user.id, email: user.email } };
});

app.post('/api/auth/logout', async (req, reply) => {
  reply.clearCookie(COOKIE, { path: '/' });
  return { ok: true };
});

// ---- account ------------------------------------------------------------
app.get('/api/me', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return { id: user.id, email: user.email, displayName: user.display_name };
});

// ---- slicer preset compatibility ---------------------------------------
// Returns the process + filament preset names compatible with a printer preset,
// joined from the seeded slicer_compat table. The slice UI uses this to narrow
// its (otherwise ~1000-entry) preset pickers to the selected printer.
app.get('/api/slicer/compatible', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const printer = (req.query?.printer || '').toString().trim();
  if (!printer) return { process: [], filament: [] };
  try {
    return await getCompatiblePresets(printer);
  } catch (e) {
    req.log.error(e);
    return { process: [], filament: [] };
  }
});

// ---- instance -----------------------------------------------------------
app.get('/api/instance', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const inst = await getInstanceForUser(user.id);
  if (!inst) return reply.code(404).send({ error: 'no-instance', status: 'not_provisioned' });
  return {
    status: inst.status, subdomain: inst.subdomain, dbName: inst.db_name,
    port: inst.port, engineVersion: inst.engine_version,
    createdAt: inst.created_at
  };
});

app.post('/api/instance/provision', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  try {
    const inst = await provisionForUser(user);
    return {
      status: inst.status, subdomain: inst.subdomain, dbName: inst.db_name,
      port: inst.port, engineVersion: inst.engine_version, createdAt: inst.created_at,
      engine: inst.engine
    };
  } catch (e) {
    req.log.error(e);
    return reply.code(500).send({ error: 'provisioning failed: ' + e.message });
  }
});

// Real fleet stats, pulled live from the user's engine.
function asArray(x) {
  if (Array.isArray(x)) return x;
  if (x && typeof x === 'object') return x.printers || x.items || x.queue || x.results || [];
  return [];
}
app.get('/api/instance/stats', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const inst = await getInstanceForUser(user.id);
  const base = engineBase(inst);
  if (!base) return { printersOnline: 0, activeJobs: 0, queued: 0, successRate: null, printersTotal: 0 };
  try {
    const [printers, queue, pstats] = await Promise.all([
      fetch(base + '/api/v1/printers/').then(r => r.ok ? r.json() : []).catch(() => []),
      fetch(base + '/api/v1/queue/').then(r => r.ok ? r.json() : []).catch(() => []),
      fetch(base + '/api/v1/archives/stats').then(r => r.ok ? r.json() : null).catch(() => null)
    ]);
    const parr = asArray(printers), qarr = asArray(queue);
    // The /printers/ list carries only static config — live state (connected,
    // printing) is only on the per-printer /status endpoint, so fetch those.
    const statuses = await Promise.all(
      parr.map(p => fetch(base + `/api/v1/printers/${p.id}/status`)
        .then(r => r.ok ? r.json() : null).catch(() => null))
    );
    const online = statuses.filter(s => s && s.connected).length;
    const active = statuses.filter(s => s && /print|run/i.test(String(s.state || ''))).length;
    const total = pstats?.total_prints ?? 0;
    return {
      printersTotal: parr.length,
      printersOnline: online,
      activeJobs: active,
      queued: qarr.length,
      totalPrints: total,
      successRate: total > 0 ? Math.round((pstats.successful_prints / total) * 100) : null
    };
  } catch {
    return { printersOnline: 0, activeJobs: 0, queued: 0, successRate: null, printersTotal: 0 };
  }
});

// Authenticated gateway: proxy the logged-in user's request to THEIR engine.
// Frontend calls /api/engine/<engine-path>; we resolve the user's instance and
// forward. (JSON + GET today; multipart upload proxying is a follow-up.)
app.all('/api/engine/*', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const inst = await getInstanceForUser(user.id);
  const base = engineBase(inst);
  if (!base) return reply.code(409).send({ error: 'no running instance for this account' });
  const enginePath = req.url.replace(/^\/api\/engine/, '') || '/';
  const method = req.method;
  const headers = { accept: req.headers['accept'] || 'application/json' };
  let body;
  if (!['GET', 'HEAD'].includes(method) && req.body !== undefined) {
    if (Buffer.isBuffer(req.body)) {
      // raw passthrough (multipart / binary) — keep the original content-type + boundary
      headers['content-type'] = req.headers['content-type'] || 'application/octet-stream';
      body = req.body;
    } else {
      headers['content-type'] = 'application/json';
      body = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);
    }
  }
  try {
    const res = await fetch(base + enginePath, { method, headers, body });
    reply.code(res.status);
    const ct = res.headers.get('content-type');
    if (ct) reply.header('content-type', ct);
    return reply.send(Buffer.from(await res.arrayBuffer()));
  } catch (e) {
    return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
  }
});

// ---- boot ---------------------------------------------------------------
try {
  await migrate();
  await app.listen({ host: '0.0.0.0', port: PORT });
  app.log.info(`control-plane listening on ${PORT}`);
} catch (err) {
  app.log.error(err);
  process.exit(1);
}
