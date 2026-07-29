import { setInstanceFeature, setInstanceQuota } from './db.js';
// OpenPrintHQ control-plane — HTTP API
// SPDX-License-Identifier: AGPL-3.0-or-later
import Fastify from 'fastify';
import cookie from '@fastify/cookie';
import { readFileSync } from 'node:fs';
import { randomBytes, createPublicKey } from 'node:crypto';
import { migrate, upsertUser, getUserByEmail, getInstanceForUser, getCompatiblePresets,
  getCircuits, setCircuit, getAutomation, setAutomation,
  createConnector, listConnectors, deleteConnector, setConnectorClientKey,
  getSigningPublic, setSigningKey, deleteSigningKey, getBatchById,
  getIntegrationToken, setIntegrationToken, getUserByIntegrationToken,
  getAppearance, setAppearance, getOwnerUserId,
  createInvite, getValidInvite, consumeInvite, listInvites, revokeInvite,
  listUsers, listAllInstances, countUsers } from './db.js';
import { createAuthentikUser, linkAuthentikUser, authentikUserExists, authentikConfigured, OWNER_GROUP } from './authentik.js';
import { registerConnectorRoutes, connectorOnline, isConnectorOnline, proxyViaConnector, openTcpStream } from './connector.js';
import { provisionForUser } from './provisioner.js';
import { startBatch, activeBatchForUser, advanceBatch, cancelBatch, startOrchestrator } from './batch.js';
import { activateRoute, deactivateRoute, reconcileRoutes } from './routing.js';
import { generateKeyPair, encryptPrivate, invalidateSigningCache } from './signing.js';
import { ensureStream, iceServers, GO2RTC_URL } from './go2rtc.js';
import { dbg } from './debuglog.js';

// Bambu HMS error dictionary (short_code "XXXX_YYYY" -> human description),
// extracted from the engine's HMS table. Loaded once at startup and served to
// the printer detail UI so it can decode raw hms_errors into readable text.
let HMS_DESCRIPTIONS = {};
try {
  HMS_DESCRIPTIONS = JSON.parse(readFileSync(new URL('./data/hms_descriptions.json', import.meta.url), 'utf8'));
} catch (e) {
  console.error('HMS descriptions unavailable:', e.message);
}

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
  const user = await getUserByEmail(email);
  // No auto-provisioning. An authenticated identity WITHOUT an OpenPrintHQ account
  // must claim one with an invite (POST /api/instance/claim). This closes the
  // social-login bypass where any Google/Microsoft/Facebook sign-in silently
  // created (and could self-promote to owner) an account with no invite.
  if (!user) { reply.code(403).send({ error: 'no-account', needsInvite: (await countUsers()) > 0 }); return null; }
  return user;
}

// Roles come from Authentik groups, forwarded by npmplus as X-authentik-groups
// (a "|"- or ","-separated list). Owner = member of OPHQ_OWNER_GROUP; the header
// is trusted only when the gateway secret is present (same guard as identity).
function groupsOf(req) {
  const gatewayOk = !GATEWAY_SECRET || req.headers['x-ophq-gateway'] === GATEWAY_SECRET;
  if (!gatewayOk) return [];
  const raw = req.headers['x-authentik-groups'];
  return raw ? String(raw).split(/[|,]/).map((s) => s.trim()).filter(Boolean) : [];
}
// Owner = the bootstrapped first account (users.is_owner) OR a current member of
// the Authentik owner group.
function isOwner(req, user) {
  if (user?.is_owner) return true;
  return groupsOf(req).includes(OWNER_GROUP);
}
async function requireOwner(req, reply) {
  const user = await requireUser(req, reply);
  if (!user) return null;
  if (!isOwner(req, user)) { reply.code(403).send({ error: 'owner access required' }); return null; }
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
  const email = currentEmail(req);
  if (!email) { reply.code(401).send({ error: 'not authenticated' }); return; }
  const user = await getUserByEmail(email);
  if (!user) return { email, hasAccount: false, needsInvite: (await countUsers()) > 0, isOwner: false, role: 'guest' };
  const owner = isOwner(req, user);
  return { id: user.id, email: user.email, displayName: user.display_name, role: owner ? 'owner' : 'user', isOwner: owner, hasAccount: true };
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

// ---- HMS error descriptions --------------------------------------------
// Static Bambu HMS dictionary; the printer detail page decodes a machine's
// raw hms_errors (attr+code -> short_code) against this map. Small enough to
// return whole and cache client-side.
app.get('/api/hms/descriptions', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  reply.header('cache-control', 'public, max-age=86400');
  return HMS_DESCRIPTIONS;
});

// Engine image display derived from the running tier (OPHQ_ENGINE_IMAGE),
// so each environment shows its real engine tag (dev/test/prod).
const ENGINE_IMAGE = process.env.OPHQ_ENGINE_IMAGE || '';
const engineDisplay = (stored) => {
  const m = ENGINE_IMAGE.match(/:([^:/]+)$/);
  return m ? `openprinthq-engine:${m[1]}` : (stored || 'openprinthq-engine');
};

// ---- instance -----------------------------------------------------------
app.get('/api/instance', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const inst = await getInstanceForUser(user.id);
  if (!inst) return reply.code(404).send({ error: 'no-instance', status: 'not_provisioned' });
  return {
    status: inst.status, subdomain: inst.subdomain, dbName: inst.db_name,
    port: inst.port, engineVersion: engineDisplay(inst.engine_version),
    createdAt: inst.created_at, features: inst.features || {},
    genfilamentUrl: process.env.OPHQ_GENFILAMENT_URL || ''
  };
});

app.post('/api/instance/provision', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  try {
    const inst = await provisionForUser(user);
    return {
      status: inst.status, subdomain: inst.subdomain, dbName: inst.db_name,
      port: inst.port, engineVersion: engineDisplay(inst.engine_version), createdAt: inst.created_at,
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

// Claim an OpenPrintHQ account for an already-authenticated identity (e.g. a
// social login). Invite-gated exactly like /api/pub/signup, so NO login method
// can create an account without an invite. Provisions the instance on success.
app.post('/api/instance/claim', async (req, reply) => {
  const email = currentEmail(req);
  if (!email) return reply.code(401).send({ error: 'not authenticated' });
  if (await getUserByEmail(email)) return reply.code(409).send({ error: 'account already exists' });
  const code = (req.body?.code || '').toString().trim();
  const bootstrap = (await countUsers()) === 0;
  let invite = null;
  if (!bootstrap) {
    if (!code) return reply.code(400).send({ error: 'an invite code is required' });
    invite = await getValidInvite(code);
    if (!invite) return reply.code(400).send({ error: 'invalid or expired invite code' });
    if (invite.email && invite.email.toLowerCase() !== email) return reply.code(400).send({ error: 'this invite is for a different email' });
  }
  const user = await upsertUser(email, req.headers['x-authentik-name'] || null);
  if (invite) await consumeInvite(code, user.id).catch(() => {});
  try { await provisionForUser(user); } catch (e) { req.log.error({ err: e.message }, 'provision after claim failed'); }
  return { ok: true, email, owner: bootstrap };
});

// ---- owner: admin (invites, users, instances, usage) --------------------
// Every route is requireOwner-gated, so non-owners can't even enumerate the tab.
app.get('/api/admin/summary', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  return { isOwner: true, ownerGroup: OWNER_GROUP, authentik: authentikConfigured() };
});
app.get('/api/admin/invites', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  return { invites: await listInvites() };
});
app.post('/api/admin/invites', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const b = req.body || {};
  return await createInvite(owner.id, { email: b.email, note: b.note, ttlDays: 2 });
});
app.delete('/api/admin/invites/:code', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  await revokeInvite(req.params.code);
  return { ok: true };
});
app.get('/api/admin/users', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  return { users: await listUsers() };
});
const FEATURES = [
  { key: 'genfilament', name: 'GenFilament', desc: 'AI filament profile generator for OrcaSlicer / Bambu Studio', paid: true }
];
app.get('/api/admin/features', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  return { features: FEATURES };
});
app.put('/api/admin/instances/:id/features', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const key = String(req.body?.key || '');
  if (!FEATURES.some((f) => f.key === key)) return reply.code(400).send({ error: 'unknown feature' });
  const inst = await setInstanceFeature(Number(req.params.id), key, !!req.body?.enabled);
  if (!inst) return reply.code(404).send({ error: 'instance not found' });
  return { ok: true, instance: inst };
});
// Per-instance file-storage quota (MB). Empty/null body -> unlimited. Stored only.
app.put('/api/admin/instances/:id/quota', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const raw = req.body?.quotaMb;
  let quotaMb = null;
  if (raw !== null && raw !== undefined && raw !== '') {
    quotaMb = Number(raw);
    if (!Number.isInteger(quotaMb) || quotaMb < 0) return reply.code(400).send({ error: 'quotaMb must be an integer >= 0 or null' });
  }
  const inst = await setInstanceQuota(Number(req.params.id), quotaMb);
  if (!inst) return reply.code(404).send({ error: 'instance not found' });
  return { ok: true, instance: inst };
});
// All instances with live per-instance usage (best-effort; a down engine -> zeros).
app.get('/api/admin/instances', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const rows = await listAllInstances();
  const withStats = await Promise.all(rows.map(async (i) => {
    const base = engineBase(i);
    let stats = { printersTotal: 0, printersOnline: 0, activeJobs: 0 };
    if (base) {
      try {
        const parr = asArray(await fetch(base + '/api/v1/printers/').then(r => r.ok ? r.json() : []).catch(() => []));
        const statuses = await Promise.all(parr.map(p =>
          fetch(base + `/api/v1/printers/${p.id}/status`).then(r => r.ok ? r.json() : null).catch(() => null)));
        stats = {
          printersTotal: parr.length,
          printersOnline: statuses.filter(s => s && s.connected).length,
          activeJobs: statuses.filter(s => s && /print|run/i.test(String(s.state || ''))).length
        };
      } catch { /* engine down -> zeros */ }
    }
    return { ...i, stats };
  }));
  return { instances: withStats };
});
// Owner provisions an instance for an existing/new user by email.
app.post('/api/admin/instances', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const email = (req.body?.email || '').trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return reply.code(400).send({ error: 'valid email required' });
  const user = await upsertUser(email, req.body?.name || null);
  try {
    const inst = await provisionForUser(user);
    return { ok: true, subdomain: inst.subdomain, status: inst.status };
  } catch (e) {
    return reply.code(500).send({ error: 'provisioning failed: ' + e.message });
  }
});

// ---- public: signup with invite code ------------------------------------
// Exposed via npmplus /api/pub/ (NO forward-auth). Redeems an invite, creates the
// login user in Authentik, then provisions their instance. Errors are generic
// (no account/invite enumeration).
// Tells the signup page whether an invite is required yet — so the very first
// (bootstrap) account never sees an invite field that would only confuse them.
app.get('/api/pub/signup-info', async () => {
  return { inviteRequired: (await countUsers()) > 0, enabled: authentikConfigured() };
});
// Public SITE branding = the owner's branding, so the logged-out landing page can
// show the host's configured logo / site name. Unauthenticated (npmplus /api/pub/,
// no forward-auth). Returns ONLY the branding sub-object (logos / siteName /
// wordmark / tagline / trademark / contact) — never theme, a11y, or other users'
// data. Empty object if no owner exists yet.
app.get('/api/pub/branding', async () => {
  const ownerId = await getOwnerUserId();
  if (!ownerId) return { branding: {} };
  const config = await getAppearance(ownerId);
  return { branding: (config && config.branding) || {} };
});
app.post('/api/pub/signup', async (req, reply) => {
  const b = req.body || {};
  const code = (b.code || '').toString().trim();
  const email = (b.email || '').toString().trim().toLowerCase();
  const name = (b.name || '').toString().trim().slice(0, 100);
  const password = (b.password || '').toString();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || password.length < 10) {
    return reply.code(400).send({ error: 'a valid email and a 10+ character password are required' });
  }
  if (!authentikConfigured()) return reply.code(503).send({ error: 'signup is unavailable right now' });

  // Bootstrap: the very first account needs no invite and becomes the owner.
  const bootstrap = (await countUsers()) === 0;
  let invite = null;
  if (!bootstrap) {
    if (!code) return reply.code(400).send({ error: 'an invite code is required' });
    invite = await getValidInvite(code);
    if (!invite) return reply.code(400).send({ error: 'invalid or expired invite code' });
    if (invite.email && invite.email !== email) return reply.code(400).send({ error: 'this invite is for a different email' });
  }
  // A LOCAL OpenPrintHQ account for this email is a genuine duplicate — bail.
  if (await getUserByEmail(email)) {
    return reply.code(409).send({ error: 'an OpenPrintHQ account already exists for this email — sign in instead' });
  }
  // If the email already exists in Authentik (e.g. from another Authentik-backed
  // service), DON'T error — link OpenPrintHQ to that existing identity by
  // ensuring group membership (their existing password/other-service access is
  // untouched; the password typed here is ignored for a linked account). Only a
  // brand-new email gets a fresh Authentik user created. Either way we then
  // consume the invite + provision, so a failure leaves the code redeemable. The
  // first user lands in the owner group to match their bootstrapped is_owner flag.
  try {
    if (await authentikUserExists(email)) {
      await linkAuthentikUser(email, { owner: bootstrap });
    } else {
      await createAuthentikUser(email, name, password, { owner: bootstrap });
    }
  } catch (e) {
    req.log.error({ err: e.message }, 'authentik user create/link failed');
    return reply.code(502).send({ error: 'could not set up the login account' });
  }
  const user = await upsertUser(email, name || null);
  if (invite) await consumeInvite(code, user.id).catch(() => {});
  try { await provisionForUser(user); } catch (e) { req.log.error({ err: e.message }, 'provision after signup failed'); }
  return { ok: true, email, owner: bootstrap };
});

// ---- power circuits -----------------------------------------------------
// Which breaker circuit each printer sits on, used by staggered batch printing.
app.get('/api/printer-circuits', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return await getCircuits(user.id);
});
app.put('/api/printer-circuits', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const map = req.body || {};
  if (typeof map !== 'object' || Array.isArray(map)) return reply.code(400).send({ error: 'expected an object' });
  for (const [pid, circuit] of Object.entries(map)) {
    const id = Number(pid);
    if (!Number.isInteger(id)) continue;
    await setCircuit(user.id, id, circuit == null ? '' : String(circuit));
  }
  return await getCircuits(user.id);
});

// ---- printer automation (bed ejection / continuous printing, #20) -------
app.get('/api/printer-automation', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return await getAutomation(user.id);
});
app.put('/api/printer-automation', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const map = req.body || {};
  if (typeof map !== 'object' || Array.isArray(map)) return reply.code(400).send({ error: 'expected an object' });
  const routeResults = {};
  for (const [pid, cfg] of Object.entries(map)) {
    const id = Number(pid);
    if (!Number.isInteger(id) || !cfg || typeof cfg !== 'object') continue;
    const patch = {};
    if ('auto_eject' in cfg) patch.auto_eject = !!cfg.auto_eject;
    if ('eject_gcode' in cfg) patch.eject_gcode = cfg.eject_gcode;
    if ('connector_id' in cfg) patch.connector_id = cfg.connector_id;
    await setAutomation(user.id, id, patch);
    if ('connector_id' in cfg) {
      try { routeResults[id] = cfg.connector_id ? await activateRoute(user.id, id) : await deactivateRoute(user.id, id); }
      catch (e) { routeResults[id] = { ok: false, reason: e.message || 'activation failed' }; }
    }
  }
  const out = await getAutomation(user.id);
  if (Object.keys(routeResults).length) out._routes = routeResults;
  return out;
});

// ---- local connectors: outbound tunnel for LAN printers (#28/#29) --------
registerConnectorRoutes(app);
app.get('/api/connectors', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const list = await listConnectors(user.id);
  return list.map((c) => ({ ...c, online: isConnectorOnline(c.id) }));
});
app.post('/api/connectors', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const name = (req.body?.name || 'connector').toString().slice(0, 60);
  return await createConnector(user.id, name);
});
app.delete('/api/connectors/:id', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const id = Number(req.params.id);
  if (Number.isInteger(id)) await deleteConnector(user.id, id);
  return { ok: true };
});

// Register (or clear) a connector's own public key for mutual auth (SSH-style).
app.patch('/api/connectors/:id', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const id = Number(req.params.id);
  if (!Number.isInteger(id)) return reply.code(400).send({ error: 'bad id' });
  const pem = (req.body?.client_public_key ?? '').toString();
  if (pem.trim()) {
    try { createPublicKey(pem.trim()); }
    catch { return reply.code(400).send({ error: 'not a valid PEM public key' }); }
  }
  await setConnectorClientKey(user.id, id, pem);
  return { ok: true, has_client_key: !!pem.trim() };
});

// LAN printer discovery through a specific connector (site). The scan runs on
// that connector's own network — where the printers actually are — not on the
// cloud engine, so it can see LAN-only printers the cloud never could.
app.post('/api/connectors/:id/discover', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const id = Number(req.params.id);
  if (!Number.isInteger(id)) return reply.code(400).send({ error: 'bad id' });
  if (!connectorOnline(user.id, id)) {
    dbg('connector', 'discover requested but connector offline', { userId: user.id, connectorId: id });
    return { connector_online: false, devices: [] };
  }
  const windowMs = Math.min(Math.max(Number(req.body?.window_ms) || 4000, 1000), 12000);
  dbg('connector', 'discover -> connector', { userId: user.id, connectorId: id, window_ms: windowMs });
  const r = await proxyViaConnector(user.id, { kind: 'discover', window_ms: windowMs }, windowMs + 8000, id);
  dbg('connector', 'discover result', { userId: user.id, connectorId: id, found: (r.devices || []).length, error: r.error || null });
  return { connector_online: true, devices: r.devices || [], error: r.error || null };
});

app.post('/api/connectors/test', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const b = req.body || {};
  const host = (b.host || '').toString();
  const port = Number(b.port) || 80;
  if (!host) return reply.code(400).send({ error: 'host required' });
  const r = await proxyViaConnector(user.id, {
    host, port, scheme: (b.scheme || 'http').toString(),
    path: (b.path || '/').toString(), method: 'GET'
  });
  const bytes = r.body ? Buffer.from(r.body, 'base64').length : 0;
  return { status: r.status ?? null, error: r.error || null, bytes };
});

app.post('/api/connectors/tcp-test', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const b = req.body || {};
  const host = (b.host || '').toString();
  const port = Number(b.port) || 0;
  if (!host || !port) return reply.code(400).send({ error: 'host and port required' });
  const payload = b.payload_b64 ? Buffer.from(b.payload_b64, 'base64') : null;
  const chunks = [];
  const out = await new Promise((resolve) => {
    let opened = false;
    const t = openTcpStream(user.id, host, port);
    const finish = (extra) => resolve({ opened, ...extra });
    const timer = setTimeout(() => { try { t.close(); } catch {} finish({ note: 'read window elapsed' }); }, Number(b.window_ms) || 3000);
    t.on('open', () => { opened = true; if (payload) t.write(payload); });
    t.on('data', (d) => chunks.push(d));
    t.on('close', (err) => { clearTimeout(timer); finish({ error: err || null }); });
  });
  const buf = Buffer.concat(chunks);
  return { ...out, bytes: buf.length, preview: buf.subarray(0, 160).toString('utf8').replace(/[^\x20-\x7e]/g, '.') };
});

// ---- connector command-signing key (RSA-2048) ---------------------------
app.get('/api/connector/signing-key', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const k = await getSigningPublic(user.id);
  return { public_pem: k?.public_pem || null, created_at: k?.created_at || null };
});
app.post('/api/connector/signing-key', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const { publicPem, privatePem } = generateKeyPair();
  await setSigningKey(user.id, publicPem, encryptPrivate(privatePem));
  invalidateSigningCache(user.id);
  return { public_pem: publicPem };
});
app.delete('/api/connector/signing-key', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  await deleteSigningKey(user.id);
  invalidateSigningCache(user.id);
  return { ok: true };
});

// ---- temperature-staggered batch printing -------------------------------
app.post('/api/batch', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const b = req.body || {};
  const fileId = Number(b.file_id);
  const printers = Array.isArray(b.printers) ? b.printers
    .map((p) => ({ id: Number(p.id), name: p.name })).filter((p) => Number.isInteger(p.id)) : [];
  if (!Number.isInteger(fileId)) return reply.code(400).send({ error: 'file_id required' });
  if (printers.length === 0) return reply.code(400).send({ error: 'select at least one printer' });
  try {
    const batch = await startBatch(user, {
      fileId, fileName: b.file_name || null, printers,
      staggered: b.staggered !== false,
      maxPreheat: Number(b.max_preheat) || 1,
      tolerance: b.tolerance != null ? Number(b.tolerance) : 3.0,
      maxWaitSecs: b.max_wait_secs != null ? Number(b.max_wait_secs) : 900
    });
    return batch;
  } catch (e) {
    return reply.code(e.status || 500).send({ error: e.message || 'could not start batch' });
  }
});

app.get('/api/batch/active', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return (await activeBatchForUser(user)) || { status: 'none' };
});

async function ownBatch(req, reply) {
  const user = await requireUser(req, reply); if (!user) return null;
  const batch = await getBatchById(Number(req.params.id));
  if (!batch || batch.user_id !== user.id) { reply.code(404).send({ error: 'batch not found' }); return null; }
  return batch;
}
app.post('/api/batch/:id/advance', async (req, reply) => {
  const batch = await ownBatch(req, reply); if (!batch) return;
  return await advanceBatch(batch);
});
app.post('/api/batch/:id/cancel', async (req, reply) => {
  const batch = await ownBatch(req, reply); if (!batch) return;
  return await cancelBatch(batch);
});

// ---- integrations: token + public read-only endpoints -------------------
// A per-user bearer token lets external systems (Home Assistant, Homepage,
// Prometheus) read fleet status without Authentik SSO. These /api/pub/* routes
// are exposed publicly by npmplus (no forward-auth) and authenticate by token.

async function buildSummary(inst) {
  const base = engineBase(inst);
  const out = { instance: inst?.subdomain || null, status: inst?.status || 'unknown',
    printers_total: 0, printers_online: 0, active_jobs: 0, queued: 0, success_rate: null, printers: [] };
  if (!base) return out;
  const [printers, queue, pstats] = await Promise.all([
    fetch(base + '/api/v1/printers/').then(r => r.ok ? r.json() : []).catch(() => []),
    fetch(base + '/api/v1/queue/').then(r => r.ok ? r.json() : []).catch(() => []),
    fetch(base + '/api/v1/archives/stats').then(r => r.ok ? r.json() : null).catch(() => null)
  ]);
  const parr = asArray(printers), qarr = asArray(queue);
  const statuses = await Promise.all(parr.map(p =>
    fetch(base + `/api/v1/printers/${p.id}/status`).then(r => r.ok ? r.json() : null).catch(() => null)));
  out.printers = parr.map((p, i) => {
    const s = statuses[i] || {};
    const t = s.temperatures || {};
    return {
      id: p.id, name: p.name || p.model || ('Printer ' + p.id),
      connected: !!s.connected, state: (s.state || (s.connected ? 'idle' : 'offline')).toString().toLowerCase(),
      progress: s.progress ?? null,
      nozzle: t.nozzle ?? null, bed: t.bed ?? null,
      job: s.subtask_name || s.gcode_file || null
    };
  });
  out.printers_total = parr.length;
  out.printers_online = out.printers.filter(p => p.connected).length;
  out.active_jobs = out.printers.filter(p => /print|run/.test(p.state)).length;
  out.queued = qarr.length;
  const total = pstats?.total_prints ?? 0;
  out.success_rate = total > 0 ? Math.round((pstats.successful_prints / total) * 100) : null;
  return out;
}

// Resolve the token → user → instance for public requests.
async function pubInstance(req, reply) {
  const token = (req.query?.token || (req.headers['authorization'] || '').replace(/^Bearer\s+/i, '') || '').toString();
  const user = await getUserByIntegrationToken(token);
  if (!user) { reply.code(401).send({ error: 'invalid or missing token' }); return null; }
  return await getInstanceForUser(user.id);
}

app.get('/api/pub/summary', async (req, reply) => {
  const inst = await pubInstance(req, reply); if (inst === null) return;
  reply.header('access-control-allow-origin', '*');
  return await buildSummary(inst);
});

app.get('/api/pub/metrics', async (req, reply) => {
  const inst = await pubInstance(req, reply); if (inst === null) return;
  const s = await buildSummary(inst);
  const esc = (v) => String(v).replace(/[\\"\n]/g, '_');
  const lines = [
    '# HELP ophq_printers_total Printers configured', '# TYPE ophq_printers_total gauge',
    `ophq_printers_total ${s.printers_total}`,
    '# HELP ophq_printers_online Printers currently connected', '# TYPE ophq_printers_online gauge',
    `ophq_printers_online ${s.printers_online}`,
    '# HELP ophq_active_jobs Printers currently printing', '# TYPE ophq_active_jobs gauge',
    `ophq_active_jobs ${s.active_jobs}`,
    '# HELP ophq_queued Jobs waiting in the queue', '# TYPE ophq_queued gauge',
    `ophq_queued ${s.queued}`
  ];
  if (s.success_rate != null) {
    lines.push('# HELP ophq_success_rate Print success rate percent', '# TYPE ophq_success_rate gauge', `ophq_success_rate ${s.success_rate}`);
  }
  lines.push('# HELP ophq_printer_online Per-printer connected (1/0)', '# TYPE ophq_printer_online gauge');
  for (const p of s.printers) lines.push(`ophq_printer_online{printer="${esc(p.name)}",id="${p.id}"} ${p.connected ? 1 : 0}`);
  lines.push('# HELP ophq_printer_progress Per-printer job progress percent', '# TYPE ophq_printer_progress gauge');
  for (const p of s.printers) if (p.progress != null) lines.push(`ophq_printer_progress{printer="${esc(p.name)}",id="${p.id}"} ${Math.round(p.progress)}`);
  lines.push('# HELP ophq_printer_nozzle_temp Per-printer nozzle temperature C', '# TYPE ophq_printer_nozzle_temp gauge');
  for (const p of s.printers) if (p.nozzle != null) lines.push(`ophq_printer_nozzle_temp{printer="${esc(p.name)}",id="${p.id}"} ${p.nozzle}`);
  lines.push('# HELP ophq_printer_bed_temp Per-printer bed temperature C', '# TYPE ophq_printer_bed_temp gauge');
  for (const p of s.printers) if (p.bed != null) lines.push(`ophq_printer_bed_temp{printer="${esc(p.name)}",id="${p.id}"} ${p.bed}`);
  reply.header('content-type', 'text/plain; version=0.0.4');
  return lines.join('\n') + '\n';
});

// Token management (authenticated). Returns the token + ready-to-use URLs.
app.get('/api/integration-token', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  let token = await getIntegrationToken(user.id);
  if (!token) token = await setIntegrationToken(user.id, 'ophq_' + randomBytes(24).toString('hex'));
  return { token };
});
app.post('/api/integration-token/regenerate', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const token = await setIntegrationToken(user.id, 'ophq_' + randomBytes(24).toString('hex'));
  return { token };
});

// ---- appearance (Look & Feel) ------------------------------------------
// Per-user theme + branding. Stored as one JSON blob; never shared between
// users, so one account's theme can't affect another's.
const MAX_IMG = 512 * 1024;            // per image (each logo / favicon) data-URI cap
const MAX_APPEARANCE = 4 * 1024 * 1024; // whole-config cap (3 logos + favicon + colours)
function validImageDataUri(s) {
  return typeof s === 'string' && (s === '' || (/^data:image\/(png|jpeg|jpg|svg\+xml|webp|gif|x-icon|vnd\.microsoft\.icon);base64,/.test(s) && s.length <= MAX_IMG));
}
app.get('/api/appearance', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const config = await getAppearance(user.id);
  return { config: config || null };
});
app.put('/api/appearance', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const config = req.body;
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    return reply.code(400).send({ error: 'config object required' });
  }
  const b = config.branding || {};
  if (!validImageDataUri(b.logo || '')) return reply.code(400).send({ error: 'logo must be an image data-URI under 512 KB' });
  if (!validImageDataUri(b.favicon || '')) return reply.code(400).send({ error: 'favicon must be an image data-URI under 512 KB' });
  const logos = (b.logos && typeof b.logos === 'object') ? b.logos : {};
  for (const slot of ['light', 'dark', 'accessible']) {
    if (!validImageDataUri(logos[slot] || '')) return reply.code(400).send({ error: `${slot} logo must be an image data-URI under 512 KB` });
  }
  // Light sanitation of per-user nav customization (best-effort coercion, not
  // rejection): order/hidden become string arrays; links become {label,url} with
  // http(s) urls only. Other config keys pass through unchanged.
  if (config.nav !== undefined) {
    const n = (config.nav && typeof config.nav === 'object' && !Array.isArray(config.nav)) ? config.nav : {};
    const strArr = (a) => (Array.isArray(a) ? a.filter((x) => typeof x === 'string') : []);
    const links = Array.isArray(n.links) ? n.links : [];
    config.nav = {
      order: strArr(n.order),
      hidden: strArr(n.hidden),
      links: links
        .filter((l) => l && typeof l === 'object' && typeof l.url === 'string' && /^https?:\/\//i.test(l.url.trim()))
        .map((l) => ({ label: typeof l.label === 'string' ? l.label : '', url: l.url.trim() }))
    };
  }
  if (JSON.stringify(config).length > MAX_APPEARANCE) return reply.code(413).send({ error: 'appearance config too large' });
  await setAppearance(user.id, config);
  return { ok: true };
});

// ---- camera WebRTC signaling (video never touches the control-plane) -------
// The browser negotiates a WebRTC session with go2rtc (co-located with the
// printers) through this tiny SDP passthrough. ONLY the offer/answer handshake
// flows here; the video then streams peer-to-peer browser<->go2rtc, so the
// control-plane never carries camera traffic.
// Fresh ICE servers (STUN + short-lived Cloudflare TURN) for the browser.
app.get('/api/camera/ice', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return { iceServers: await iceServers() };
});
app.post('/api/camera/webrtc/:printerId', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const pid = String(req.params.printerId).replace(/[^0-9]/g, '');
  if (!pid) return reply.code(400).send({ error: 'bad printer id' });
  const inst = await getInstanceForUser(user.id);
  if (!inst) return reply.code(409).send({ error: 'no running instance' });
  // Register (idempotent) the printer's camera as a go2rtc stream, read straight
  // from this user's engine DB. Returns null for non-RTSP printers -> 404 -> the
  // browser falls back to snapshot polling. Also scopes access to the user's own
  // engine, so no separate ownership check is needed.
  const name = await ensureStream(inst, user.id, pid);
  if (!name) return reply.code(404).send({ error: 'no WebRTC camera stream for this printer' });
  // Relay the SDP offer to go2rtc and return its answer verbatim.
  try {
    const body = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);
    const res = await fetch(`${GO2RTC_URL}/api/webrtc?src=${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: { 'content-type': req.headers['content-type'] || 'application/json' },
      body
    });
    reply.code(res.status);
    const ct = res.headers.get('content-type'); if (ct) reply.header('content-type', ct);
    return reply.send(Buffer.from(await res.arrayBuffer()));
  } catch (e) {
    return reply.code(502).send({ error: 'signaling relay failed: ' + e.message });
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
  // Trace: engine calls go to the user's CLOUD engine, not their LAN connector.
  // A LAN-discovery scan hitting this route (e.g. /discovery/scan) is therefore
  // scanning the cloud engine's network, not the user's — visible here.
  if (/discover|scan/i.test(enginePath)) {
    dbg('engine', 'LAN-discovery request routed to CLOUD engine (not connector)', { userId: user.id, method, enginePath, engineBase: base, connectorOnline: connectorOnline(user.id) });
  } else {
    dbg('engine', 'proxy -> cloud engine', { userId: user.id, method, enginePath });
  }
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
  reconcileRoutes().catch((e) => console.error('reconcileRoutes', e.message));
  // Resume + drive any running temperature-staggered batches.
  startOrchestrator(8000);
  await app.listen({ host: '0.0.0.0', port: PORT });
  app.log.info(`control-plane listening on ${PORT}`);
} catch (err) {
  app.log.error(err);
  process.exit(1);
}
