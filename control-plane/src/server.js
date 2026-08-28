import { setInstanceFeature, setInstanceQuota } from './db.js';
// OpenPrintHQ control-plane — HTTP API
// SPDX-License-Identifier: AGPL-3.0-or-later
import Fastify from 'fastify';
import cookie from '@fastify/cookie';
import websocket from '@fastify/websocket';
import { readFileSync } from 'node:fs';
import { randomBytes, createPublicKey, createHmac, timingSafeEqual } from 'node:crypto';
import { migrate, upsertUser, getUserByEmail, getUserById, getInstanceForUser, getCompatiblePresets,
  getCircuits, setCircuit, getAutomation, setAutomation,
  createConnector, listConnectors, deleteConnector, setConnectorClientKey,
  getModelName, learnModelName, listModelNames, upsertModelNameForce, setModelNameLock, deleteModelName,
  getSigningPublic, setSigningKey, deleteSigningKey, getBatchById,
  getIntegrationToken, setIntegrationToken, getUserByIntegrationToken,
  getAppearance, setAppearance, getOwnerUserId,
  createInvite, getValidInvite, consumeInvite, listInvites, revokeInvite,
  listUsers, listAllInstances, countUsers,
  getAppSetting, setAppSetting, setSecretSetting, friendlyModelName,
  getUserLogSink, setUserLogSink, listUserLogSinks,
  getKasmRow, setKasmIdentity, setKasmSession, clearKasmSession,
  createPrintHostToken, resolvePrintHostToken, purgePrintHostTokens,
  listPrintHostTokens, revokePrintHostToken, countPrintHostTokens,
  purgeExpiredPrintHostTokens, userByKasmId,
  getTenantStorage, setTenantStorage, setTenantQuota, listTenantStorage } from './db.js';
import { createAuthentikUser, linkAuthentikUser, authentikUserExists, authentikConfigured, OWNER_GROUP } from './authentik.js';
import { garageConfigured, ensureTenantStorage, usage as storageUsage,
  setQuota as setBucketQuota, defaultQuotaBytes, clusterHealth as storageHealth,
  s3EndpointPublic, s3EndpointLan, s3EndpointEngine, s3PathPrefix, s3Region, presignTtl } from './garage.js';
import { presign } from './s3sign.js';
import { kasmConfigured, kasmEngines, kasmImageFor, ensureKasmUser, ensureSession as ensureKasmSession,
  findSession as findKasmSession, destroySession as destroyKasmSession,
  sessionStatus as kasmSessionStatus } from './kasm.js';
import { registerConnectorRoutes, connectorOnline, isConnectorOnline, proxyViaConnector, openTcpStream, connectorEvictionCount, connectorHasDuplicateAgents, connectorClientIdentity } from './connector.js';
import { provisionForUser, ensureEngineBucketMount, ensureVault, vaultScan, vaultBase, vaultEnabled, joinVaultNetwork } from './provisioner.js';
import { vaultUserHeaders } from './vault-auth.js';
import { startBatch, activeBatchForUser, advanceBatch, cancelBatch, startOrchestrator } from './batch.js';
import { activateRoute, deactivateRoute, reconcileRoutes } from './routing.js';
import { generateKeyPair, encryptPrivate, invalidateSigningCache, ensureKeyPair, fingerprint } from './signing.js';
import { ensureStream, iceServers, turnCredentials, mintTurn, supportsRtsp, GO2RTC_URL } from './go2rtc.js';
import { setSink, sinkConfigured, shipServer } from './logship.js';
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
// Every non-empty string is truthy in JS, so a plain !! here treats
// OPHQ_ALLOW_DEV_LOGIN=0 (and "false", and "off") as ENABLED. That is the one
// value an operator would reach for to switch it off, so it is parsed properly.
const ALLOW_DEV_LOGIN = /^(1|true|yes|on)$/i.test(
  String(process.env.OPHQ_ALLOW_DEV_LOGIN ?? '').trim()
);
// Optional shared secret for dev-login. When set, /api/auth/dev-login also
// requires a matching `x-ophq-dev-login` header, so enabling dev sign-in on a
// reachable non-prod tier is "bypass WITH a test key" rather than wide open.
// Empty = no extra gate (backward compatible). Never enable dev-login on prod.
const DEV_LOGIN_SECRET = process.env.OPHQ_DEV_LOGIN_SECRET || '';

// Public base URL of this deployment, used to tell a slicer where to send
// finished plates. It must be the internet-facing name: the slicer session may
// be reaching us from a different network than the control-plane sits on.
const PUBLIC_URL = (process.env.OPHQ_PUBLIC_URL || '').replace(/\/+$/, '');

function engineBase(inst) {
  // Engines are on the internal Docker network, not published to the host —
  // reach them by container name, never via a LAN-exposed port.
  return inst && inst.subdomain ? `http://ophq-${inst.subdomain}:8000` : null;
}

const app = Fastify({ logger: true, trustProxy: true, bodyLimit: 1024 * 1024 * 1024 });
await app.register(cookie, { secret: SESSION_SECRET });
// Needed for the connector's multiplexed tunnel (/api/connector/ws). Registered
// before routes so the upgrade handler is in place when they're declared.
await app.register(websocket, { options: { maxPayload: 4 * 1024 * 1024 } });

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

// ---- in-browser slicer (Kasm) -------------------------------------------
// The Slice tab embeds a containerised desktop slicer. The control-plane owns
// the Kasm identity for each user and the lifecycle of their session, so the
// browser never sees an API key and never picks its own image.
function slicerEngine(raw) {
  const engines = kasmEngines();
  const want = String(raw || '').toLowerCase();
  return engines.find((e) => e.toLowerCase() === want) || engines[0] || null;
}

app.get('/api/slicer/engines', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return { configured: kasmConfigured(), engines: kasmEngines() };
});

// Current session without starting anything. Lets the tab render a live iframe
// on reload instead of cold-starting a second container.
app.get('/api/slicer/session', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  if (!kasmConfigured()) return { configured: false, running: false };
  const row = await getKasmRow(user.id);
  const engine = slicerEngine(req.query?.engine || row?.engine);
  const imageId = engine && kasmImageFor(engine);
  if (!row?.kasm_user_id || !row?.kasm_id || !imageId) return { configured: true, running: false, engine };
  try {
    const live = await findKasmSession(row.kasm_user_id, imageId);
    if (!live || String(live.kasm_id).replace(/-/g, '') !== String(row.kasm_id).replace(/-/g, '')) {
      await clearKasmSession(user.id);
      return { configured: true, running: false, engine };
    }
    const s = await ensureKasmSession(row.kasm_user_id, imageId, { kasmId: row.kasm_id, sessionToken: row.session_token });
    return { configured: true, running: true, engine, status: s.status, url: s.url };
  } catch (e) {
    req.log.error({ err: e.message }, 'slicer session lookup failed');
    return { configured: true, running: false, engine, error: e.message };
  }
});

// Start or reattach. Idempotent from the caller's point of view even though
// Kasm's own request_kasm is not.
app.post('/api/slicer/session', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  if (!kasmConfigured()) { reply.code(503).send({ error: 'slicer not configured' }); return; }
  const engine = slicerEngine(req.body?.engine);
  const imageId = engine && kasmImageFor(engine);
  if (!imageId) { reply.code(400).send({ error: 'unknown slicer engine' }); return; }

  const row = await getKasmRow(user.id);
  try {
    const acct = await ensureKasmUser(user.email, { existingId: row?.kasm_user_id || null });
    if (acct.userId !== row?.kasm_user_id) await setKasmIdentity(user.id, acct.userId, acct.username);

    // Switching engines means a different image, so retire the old session
    // rather than leaving it parked on the host.
    if (row?.kasm_id && row.engine && row.engine !== engine) {
      try { await destroyKasmSession(acct.userId, row.kasm_id); } catch { /* best effort */ }
      await clearKasmSession(user.id);
    }

    // Mint the print-host token BEFORE launching, because environment can only
    // be injected at container creation. Old tokens are dropped first: a session
    // is the lifetime, so leaving previous ones valid would quietly accumulate
    // long-lived credentials for a desktop the user controls.
    let printHost = null;
    let environment = null;
    try {
      await purgePrintHostTokens(user.id);
      const token = randomBytes(24).toString('base64url');
      const expires = new Date(Date.now() + 12 * 60 * 60 * 1000);
      await createPrintHostToken(user.id, token, 'slicer:' + engine, expires);
      printHost = { url: PUBLIC_URL + '/printhost', apiKey: token, expiresAt: expires.toISOString() };
      environment = {
        OPHQ_PRINTHOST_URL: printHost.url,
        OPHQ_PRINTHOST_KEY: token,
        OPHQ_URL: PUBLIC_URL
      };
      // Opening straight into a model: the session fetches this file into its
      // Uploads folder at startup, so the user is not hunting through a file
      // dialog that cannot see their own machine.
      const openFile = req.body?.fileId ?? req.body?.file_id;
      if (openFile != null && String(openFile).trim() !== '') {
        environment.OPHQ_OPEN_FILE_ID = String(openFile);
      }
    } catch (e) {
      // A missing print host makes the slicer read-only, which is worth logging
      // but not worth failing the launch over.
      req.log.error({ err: e.message }, 'could not mint print-host token');
    }

    const fresh = await getKasmRow(user.id);
    const s = await ensureKasmSession(acct.userId, imageId,
      fresh?.kasm_id ? { kasmId: fresh.kasm_id, sessionToken: fresh.session_token } : null,
      environment);
    await setKasmSession(user.id, engine, s.kasmId, s.sessionToken);

    // A reused session was created before this token existed, so its injected
    // environment is stale. Say so rather than letting the caller assume the
    // slicer inside it is configured.
    return { engine, status: s.status, url: s.url, reused: s.reused, provisioned: acct.created,
             printHost, envInjected: !!environment && !s.reused };
  } catch (e) {
    req.log.error({ err: e.message }, 'slicer session start failed');
    reply.code(502).send({ error: e.message });
  }
});

app.delete('/api/slicer/session', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const row = await getKasmRow(user.id);
  if (!row?.kasm_user_id || !row?.kasm_id) return { ok: true, stopped: false };
  try { await destroyKasmSession(row.kasm_user_id, row.kasm_id); } catch (e) {
    req.log.warn({ err: e.message }, 'destroy_kasm failed');
  }
  await clearKasmSession(user.id);
  return { ok: true, stopped: true };
});

// ---- print host (OctoPrint-compatible) ----------------------------------
// A containerised slicer needs somewhere to send a finished plate. Rather than
// giving it LAN access and printer credentials, it uploads here and OpenPrintHQ
// dispatches through the connector as it does for anything else.
//
// The protocol is OctoPrint's upload API, because every slicer worth supporting
// already speaks it: OrcaSlicer, PrusaSlicer and Cura all ship an OctoPrint
// host type. That buys the whole feature with no plugin and no slicer patch.
//
// Auth is a per-user bearer token in X-Api-Key, minted when a slicer session
// starts and expiring with it. It carries no printer secrets: the worst a stolen
// token can do is add a job to the owner's own queue.
async function printHostUser(req, reply) {
  const key = req.headers['x-api-key'] ||
    (req.headers['authorization'] || '').replace(/^Bearer\s+/i, '');
  if (!key) { reply.code(401).send({ error: 'missing API key' }); return null; }
  const who = await resolvePrintHostToken(String(key).trim());
  if (!who) { reply.code(401).send({ error: 'invalid or expired API key' }); return null; }
  return who;
}

// A containerised slicer bootstraps itself here instead of us pushing a token
// into the image. The session already knows two things Kasm gave it: its own
// session id (KASM_ID) and a Kasm-issued JWT. It presents the session id; we
// look up which OpenPrintHQ user that session belongs to from our own records,
// then confirm with Kasm that the session is genuinely running before handing
// anything over.
//
// This keeps per-user, per-session credentials out of workspace images entirely,
// which is the whole point: an image is shared by every tenant.
app.post('/printhost/bootstrap', async (req, reply) => {
  const kasmId = String(req.body?.kasm_id || '').trim();
  if (!kasmId) return reply.code(400).send({ error: 'kasm_id required' });

  const row = await userByKasmId(kasmId);
  // Deliberately the same answer for "unknown session" and "not running", so
  // this cannot be used to probe which session ids exist.
  const deny = () => reply.code(403).send({ error: 'unrecognised session' });
  if (!row?.user_id || !row?.kasm_user_id) return deny();

  try {
    const st = await kasmSessionStatus(row.kasm_user_id, kasmId);
    if (!st || String(st.operational_status || '').toLowerCase() !== 'running') return deny();
  } catch { return deny(); }

  const token = randomBytes(24).toString('base64url');
  const expires = new Date(Date.now() + 12 * 60 * 60 * 1000);
  await purgePrintHostTokens(row.user_id);
  await createPrintHostToken(row.user_id, token, 'slicer-bootstrap', expires);
  req.log.info({ userId: row.user_id, kasmId }, 'print-host bootstrap issued');
  return {
    url: PUBLIC_URL + '/printhost',
    api_key: token,
    host_type: 'octoprint',
    printer_name: 'OpenPrintHQ',
    expires_at: expires.toISOString()
  };
});

// Slicers probe this to decide whether the host is real before offering Send.
// Returning OctoPrint's shape is what makes the connection test pass.
app.get('/printhost/api/version', async (req, reply) => {
  const who = await printHostUser(req, reply); if (!who) return;
  return { api: '0.1', server: '1.3.10', text: 'OctoPrint 1.3.10 (OpenPrintHQ)' };
});

// PrusaSlicer/Orca also probe this one on some host types.
app.get('/printhost/api/settings', async (req, reply) => {
  const who = await printHostUser(req, reply); if (!who) return;
  return { feature: { sdSupport: false }, webcam: { flipH: false, flipV: false } };
});

// ---- library access for a slicer session ---------------------------------
// The file dialog inside a containerised slicer sees the container filesystem,
// not the user's machine, so a model has to get in somehow. These let a session
// pull from the user's OpenPrintHQ library using the token it already holds,
// which closes the other half of the loop: models in, plates out.

app.get('/printhost/files', async (req, reply) => {
  const who = await printHostUser(req, reply); if (!who) return;
  const inst = await getInstanceForUser(who.userId);
  const base = engineBase(inst);
  if (!base) return reply.code(409).send({ error: 'no running instance for this account' });
  try {
    const r = await fetch(base + '/api/v1/library/files', { headers: { accept: 'application/json' } });
    const text = await r.text();
    if (!r.ok) return reply.code(502).send({ error: 'engine rejected the listing' });
    const d = text ? JSON.parse(text) : [];
    const arr = Array.isArray(d) ? d : (d.files || d.items || []);
    // Only what a slicer needs to choose a file. The library carries more.
    return arr.map((f) => ({
      id: f.id ?? f.file_id,
      name: f.name ?? f.filename,
      size: f.size ?? f.file_size ?? null
    })).filter((f) => f.id != null);
  } catch (e) {
    return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
  }
});

app.get('/printhost/files/:id/download', async (req, reply) => {
  const who = await printHostUser(req, reply); if (!who) return;
  const inst = await getInstanceForUser(who.userId);
  const base = engineBase(inst);
  if (!base) return reply.code(409).send({ error: 'no running instance for this account' });
  const id = encodeURIComponent(String(req.params.id));
  try {
    const r = await fetch(base + '/api/v1/library/files/' + id + '/download');
    if (!r.ok) return reply.code(r.status === 404 ? 404 : 502).send({ error: 'file not available' });
    reply.code(200);
    for (const h of ['content-type', 'content-disposition', 'content-length']) {
      const v = r.headers.get(h);
      if (v) reply.header(h, v);
    }
    return reply.send(Buffer.from(await r.arrayBuffer()));
  } catch (e) {
    return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
  }
});

app.post('/printhost/api/files/local', async (req, reply) => {
  const who = await printHostUser(req, reply); if (!who) return;
  const inst = await getInstanceForUser(who.userId);
  const base = engineBase(inst);
  if (!base) return reply.code(409).send({ error: 'no running instance for this account' });
  if (!Buffer.isBuffer(req.body)) {
    return reply.code(400).send({ error: 'expected a multipart upload' });
  }
  const ct = req.headers['content-type'] || '';

  // Hand the multipart body to the engine library untouched, exactly as the web
  // uploader does, so there is one upload path rather than two that can drift.
  let uploaded;
  try {
    const res = await fetch(base + '/api/v1/library/files', {
      method: 'POST',
      headers: { 'content-type': ct, accept: 'application/json' },
      body: req.body
    });
    const text = await res.text();
    if (!res.ok) {
      req.log.error({ status: res.status, text: text.slice(0, 300) }, 'print-host upload rejected by engine');
      return reply.code(502).send({ error: 'engine rejected the upload' });
    }
    uploaded = text ? JSON.parse(text) : {};
  } catch (e) {
    return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
  }

  const fileId = uploaded.id ?? uploaded.file_id ?? uploaded?.file?.id ?? null;
  const name = uploaded.name ?? uploaded.filename ?? 'upload';

  // OctoPrint clients set print=true to mean "and start it". We treat that as
  // "queue it" rather than "print right now": the scheduler owns dispatch, and a
  // slicer has no idea which printer is free or whether one is mid-job.
  // The `print` field is an ordinary multipart part and clients are free to put
  // it AFTER the file, so scanning a prefix of the body would miss it. Parse the
  // part itself, on a latin1 view so binary payloads cannot corrupt the search.
  const wantsPrint = (() => {
    const m = /name="(print|select)"\r?\n\r?\n([^\r\n]*)/i.exec(
      req.body.toString('latin1')
    );
    return !!m && /^(true|1|yes)$/i.test(m[2].trim());
  })();

  let queued = false, queueError = null;
  if (fileId && wantsPrint) {
    try {
      const qr = await fetch(base + '/api/v1/library/files/add-to-queue', {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ file_ids: [fileId] })
      });
      const qj = await qr.json().catch(() => ({}));
      if (qr.ok && !(qj?.errors?.length)) queued = true;
      else queueError = qj?.errors?.[0]?.error || ('queue rejected (' + qr.status + ')');
    } catch (e) { queueError = e.message; }
  }
  if (queueError) req.log.warn({ fileId, queueError }, 'print-host queue failed');

  // OctoPrint's documented 201 response shape. Slicers parse `done`.
  reply.code(201);
  return {
    done: true,
    files: {
      local: { name, path: name, origin: 'local' }
    },
    openprinthq: { file_id: fileId, queued, queue_error: queueError }
  };
});

// A Moonraker-compatible upload endpoint, sitting beside the OctoPrint one.
//
// Same reasoning as /printhost/api/files/local: speak a protocol the tool
// already implements rather than asking the tool to learn ours. GyroidVault
// (and anything else that can target Klipper) sends a plate here believing it
// is talking to Moonraker, and it lands in the queue like any other job.
//
// Auth is the same per-user print-host token, presented as X-Api-Key, which is
// exactly what Moonraker clients already send.
app.post('/printhost/server/files/upload', async (req, reply) => {
  const who = await printHostUser(req, reply); if (!who) return;
  const inst = await getInstanceForUser(who.userId);
  const base = engineBase(inst);
  if (!base) return reply.code(409).send({ error: 'no running instance for this account' });
  if (!Buffer.isBuffer(req.body)) {
    return reply.code(400).send({ error: 'expected a multipart upload' });
  }

  let uploaded;
  try {
    const res = await fetch(base + '/api/v1/library/files', {
      method: 'POST',
      headers: { 'content-type': req.headers['content-type'] || '', accept: 'application/json' },
      body: req.body
    });
    const text = await res.text();
    if (!res.ok) {
      req.log.error({ status: res.status, text: text.slice(0, 300) }, 'moonraker upload rejected by engine');
      return reply.code(502).send({ error: 'engine rejected the upload' });
    }
    uploaded = text ? JSON.parse(text) : {};
  } catch (e) {
    return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
  }

  const fileId = uploaded.id ?? uploaded.file_id ?? uploaded?.file?.id ?? null;
  const name = uploaded.name ?? uploaded.filename ?? 'upload';

  // Moonraker's "print=true" means start now. We queue instead, for the same
  // reason the OctoPrint route does: the scheduler owns dispatch and the caller
  // has no idea which printer is free or whether one is mid-job. Scanned from a
  // latin1 view so a binary payload cannot corrupt the search, and the field may
  // legitimately appear after the file part.
  const wantsPrint = (() => {
    const m = /name="print"\r?\n\r?\n([^\r\n]*)/i.exec(req.body.toString('latin1'));
    return !!m && /^(true|1|yes)$/i.test(m[1].trim());
  })();

  let queued = false, queueError = null;
  if (fileId && wantsPrint) {
    try {
      const qr = await fetch(base + '/api/v1/library/files/add-to-queue', {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ file_ids: [fileId] })
      });
      const qj = await qr.json().catch(() => ({}));
      if (qr.ok && !(qj?.errors?.length)) queued = true;
      else queueError = qj?.errors?.[0]?.error || ('queue rejected (' + qr.status + ')');
    } catch (e) { queueError = e.message; }
  }
  if (queueError) req.log.warn({ fileId, queueError }, 'moonraker print-host queue failed');

  // Moonraker's documented response shape. Clients parse item.path and, when
  // they asked to print, print_started. Reporting print_started for a QUEUED job
  // would be a lie the client acts on, so it reflects the queue result.
  reply.code(201);
  return {
    item: { path: name, root: 'gcodes' },
    print_started: queued,
    action: 'create_file',
    openprinthq: { file_id: fileId, queued, queue_error: queueError }
  };
});

// ---- tenant object storage ----------------------------------------------
// Provisioned on first use: a bucket and a key scoped to it, per tenant.
//
// Only provisioning and accounting happen here. Clients that need to move bytes
// ask for a presigned URL and talk to the object store DIRECTLY. Bulk data must
// never be proxied through this service, which may be running off-site.
//
// The bucket's access key is created here and never leaves: it is persisted so
// this service can sign on a tenant's behalf, and no client ever sees it.
async function ensureStorageFor(user, req) {
  let row = await getTenantStorage(user.id);
  if (!row) {
    const s = await ensureTenantStorage(user.id, user.email, defaultQuotaBytes());
    await setTenantStorage(user.id, s);
    req.log.info({ userId: user.id, bucket: s.bucket }, 'provisioned tenant storage');
    row = await getTenantStorage(user.id);
  }
  // An engine created before this tenant had storage carries no bucket mount,
  // and nothing else would ever add one. Idempotent and fire-and-forget: it
  // inspects first and returns untouched in the normal case, so the common path
  // costs one docker inspect and the request never waits on a recreate.
  const inst = await getInstanceForUser(user.id).catch(() => null);
  if (inst?.subdomain) {
    ensureEngineBucketMount(user.id, inst.subdomain)
      .then((r) => { if (r.changed) req.log.info({ userId: user.id, backup: r.backup }, 'engine recreated with bucket mount'); })
      .catch((e) => req.log.warn({ err: e.message }, 'bucket mount reconcile failed'));
    ensureVault(user.id, inst.subdomain, user.email)
      .then((r) => { if (r.changed) req.log.info({ userId: user.id }, 'tenant library provisioned'); })
      .catch((e) => req.log.warn({ err: e.message }, 'library reconcile failed'));
  }
  return row;
}

app.get('/api/storage', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  if (!garageConfigured()) return { configured: false };
  try {
    const row = await ensureStorageFor(user, req);
    const u = await storageUsage(row.bucket).catch(() => null);
    return {
      configured: true,
      bucket: row.bucket,
      quotaBytes: row.quota_bytes == null ? null : Number(row.quota_bytes),
      usedBytes: u?.bytes ?? null,
      objects: u?.objects ?? null,
      overQuota: u?.overQuota ?? false
    };
  } catch (e) {
    req.log.error({ err: e.message }, 'tenant storage lookup failed');
    reply.code(502).send({ error: e.message });
  }
});

// A short-lived, single-object URL for a client that will talk to the object
// store directly. Bulk never passes through here.
//
// This replaces handing out the bucket's access key. That was a standing
// credential: readable from devtools, valid until rotated, usable outside the
// app entirely, and impossible to revoke for one session without breaking the
// tenant. A presigned URL is scoped to one method and one key and expires in
// minutes, and the secret stays server-side.
//
// Which endpoint gets signed depends on where the caller sits. A browser is
// off-site and gets the public name; a slicer session or the engine is on the
// same network as the store and gets the LAN address, so a plate does not
// hairpin out to the internet and back.
// Shared by the signed-in path below and by the token-authenticated one under
// /api/pub/v1. One implementation deliberately: the two callers differ only in
// how the user was established, and a second copy is how a validation rule ends
// up applying to browsers but not to the browser extension.
async function presignForUser(user, req, reply) {
  if (!garageConfigured()) { reply.code(503).send({ error: 'object storage not configured' }); return; }

  const method = String(req.body?.method || 'PUT').toUpperCase();
  if (!['GET', 'PUT', 'HEAD', 'DELETE'].includes(method)) {
    return reply.code(400).send({ error: 'unsupported method' });
  }
  const key = String(req.body?.key || '').replace(/^\/+/, '');
  // Traversal is meaningless to S3, which has no directories, but a key
  // containing .. is almost certainly a client bug and is worth refusing
  // rather than silently creating an object with a confusing name.
  if (!key || key.length > 1024 || key.split('/').includes('..')) {
    return reply.code(400).send({ error: 'invalid object key' });
  }

  try {
    const row = await ensureStorageFor(user, req);
    // Where the caller sits decides which address gets signed. Three places,
    // not two: off-site browsers, slicer sessions on their own VLAN, and the
    // engine container beside this service. See garage.js for why.
    const scope = String(req.body?.scope || 'public');
    if (!['public', 'lan', 'engine'].includes(scope)) {
      return reply.code(400).send({ error: 'unknown scope' });
    }
    const endpoint = scope === 'lan' ? s3EndpointLan()
      : scope === 'engine' ? s3EndpointEngine()
      : s3EndpointPublic();
    const signed = presign({
      method,
      endpoint,
      // The prefix only exists for the public edge; internal callers reach the
      // store directly and would sign a path it never sees.
      pathPrefix: scope === 'public' ? s3PathPrefix() : '',
      bucket: row.bucket,
      key,
      accessKeyId: row.access_key_id,
      secretAccessKey: row.secret_key,
      region: s3Region(),
      expiresIn: presignTtl()
    });
    req.log.info({ userId: user.id, bucket: row.bucket, method, scope }, 'presigned object url');
    return { url: signed.url, method: signed.method, expiresAt: signed.expiresAt, bucket: row.bucket, key };
  } catch (e) {
    req.log.error({ err: e.message }, 'presign failed');
    reply.code(502).send({ error: e.message });
  }
}

app.post('/api/storage/presign', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return await presignForUser(user, req, reply);
});

// Ask the engine to re-index the mounted bucket folder.
//
// The store is written directly by the browser, so nothing tells the engine a
// new object exists: it indexes on scan, not on open. Without this an upload
// lands correctly and stays invisible until something else triggers a scan.
async function rescanForUser(userId, reply) {
  const inst = await getInstanceForUser(userId);
  const base = engineBase(inst);
  if (!base) return reply.code(409).send({ error: 'no running instance for this account' });
  try {
    const r = await fetch(base + '/api/v1/library/folders', { headers: { accept: 'application/json' } });
    if (!r.ok) return reply.code(502).send({ error: 'engine rejected the listing' });
    const d = await r.json().catch(() => []);
    const arr = Array.isArray(d) ? d : (d.folders || d.items || []);
    const folder = arr.find((f) => f?.is_external);
    // Not an error: a deployment without the bucket mount has no such folder,
    // and the caller should carry on rather than surface a failure.
    if (!folder) return { scanned: false, reason: 'no external folder' };
    const sr = await fetch(`${base}/api/v1/library/folders/${folder.id}/scan`, { method: 'POST' });
    if (!sr.ok) return reply.code(502).send({ error: 'scan failed' });
    // The tenant library is a separate index over the same bucket and is also
    // blind to a write made from outside it.
    if (vaultEnabled() && inst?.subdomain) vaultScan(inst.subdomain).catch(() => {});
    return { scanned: true, folderId: folder.id, ...(await sr.json().catch(() => ({}))) };
  } catch (e) {
    return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
  }
}

app.post('/api/storage/rescan', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return await rescanForUser(user.id, reply);
});

// ---- token-authenticated ingest (browser extension) ----------------------
// Same two operations as the signed-in routes above, reached with a print-host
// token instead of an Authentik session, so something that is not a browser tab
// can put a file in the user's library.
//
// Under /api/pub/ because that prefix is the one the edge exempts from
// forward-auth. That is not a naming preference: anywhere else, Authentik
// intercepts the request and the token is never seen.
//
// Bulk still does not pass through here. The caller gets a presigned URL and
// PUTs the bytes to the object store directly, exactly as the web uploader
// does, which is the whole reason this is two small JSON calls rather than a
// file upload endpoint.

// Which ingest path this deployment supports, so a client does not have to
// guess. Without object storage the library is the engine's own, and the
// OctoPrint-compatible upload endpoint is the way in.
app.get('/api/pub/v1/ingest/capabilities', async (req, reply) => {
  const who = await printHostUser(req, reply); if (!who) return;
  const inst = await getInstanceForUser(who.userId).catch(() => null);
  return {
    storage: garageConfigured(),
    printhost: true,
    instance: inst?.subdomain || null,
    // Advisory only. The store enforces the real limit; this lets a client fail
    // a 900 MB file early rather than after uploading it.
    maxObjectBytes: 2 * 1024 * 1024 * 1024
  };
});

app.post('/api/pub/v1/storage/presign', async (req, reply) => {
  const who = await printHostUser(req, reply); if (!who) return;
  const user = await getUserByEmail(who.email);
  if (!user) return reply.code(401).send({ error: 'invalid API key' });
  return await presignForUser(user, req, reply);
});

app.post('/api/pub/v1/storage/rescan', async (req, reply) => {
  const who = await printHostUser(req, reply); if (!who) return;
  return await rescanForUser(who.userId, reply);
});

// ---- admin: quotas -------------------------------------------------------
app.get('/api/admin/storage', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const rows = await listTenantStorage();
  const out = [];
  for (const r of rows) {
    const u = await storageUsage(r.bucket).catch(() => null);
    out.push({
      userId: r.user_id, email: r.email, bucket: r.bucket,
      quotaBytes: r.quota_bytes == null ? null : Number(r.quota_bytes),
      usedBytes: u?.bytes ?? null, objects: u?.objects ?? null,
      overQuota: u?.overQuota ?? false
    });
  }
  return { defaultQuotaBytes: defaultQuotaBytes(), tenants: out };
});

app.put('/api/admin/storage/:userId/quota', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const userId = Number(req.params.userId);
  const raw = req.body?.quotaBytes;
  // null is meaningful: it removes the limit rather than setting it to zero.
  const quota = raw === null ? null : Number(raw);
  if (quota !== null && (!Number.isFinite(quota) || quota < 0)) {
    return reply.code(400).send({ error: 'quotaBytes must be a non-negative number, or null for unlimited' });
  }
  const row = await getTenantStorage(userId);
  if (!row) return reply.code(404).send({ error: 'no storage provisioned for that account' });
  try {
    await setBucketQuota(row.bucket_id, quota);
    await setTenantQuota(userId, quota);
    req.log.info({ userId, quota }, 'tenant storage quota changed');
    const u = await storageUsage(row.bucket).catch(() => null);
    return { userId, bucket: row.bucket, quotaBytes: quota, usedBytes: u?.bytes ?? null };
  } catch (e) {
    reply.code(502).send({ error: e.message });
  }
});

app.get('/api/admin/storage/health', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  if (!garageConfigured()) return { configured: false };
  try { return { configured: true, ...(await storageHealth()) }; }
  catch (e) { reply.code(502).send({ error: e.message }); }
});

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
  if (DEV_LOGIN_SECRET && req.headers['x-ophq-dev-login'] !== DEV_LOGIN_SECRET) {
    return reply.code(403).send({ error: 'dev sign-in requires a valid test key' });
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

// ---- per-tenant log destination -----------------------------------------
// Scoped to the caller: a tenant configures where THEIR instance's logs go, and
// can only ever read or write their own row.
app.get('/api/settings/logging', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return { log_url: await getUserLogSink(user.id) };
});
app.put('/api/settings/logging', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const url = await setUserLogSink(user.id, (req.body || {}).log_url);
  setSink(`tenant:${user.id}`, url, { job: 'openprinthq-instance', scope: 'tenant', instance: String(user.id) });
  return { ok: true, log_url: url };
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
// Deployment-wide settings (owner-only). `deployment_mode` is one of:
//   'local'  - printers are reached directly on the engine's own LAN; the Cloud
//              Client download + connectors UI are hidden.
//   'remote' - printers live on a different network reached via a Cloud Client
//              connector; add-printer is gated until a client pairs.
//   'both'   - both local and remote printers can be added.
// Legacy value 'cloud' is treated as 'remote'; anything else falls back to 'both'.
const DEPLOYMENT_MODES = ['local', 'remote', 'both'];
function normalizeDeploymentMode(v) {
  const m = String(v || '').toLowerCase();
  if (m === 'cloud') return 'remote';
  return DEPLOYMENT_MODES.includes(m) ? m : 'both';
}
async function getDeploymentMode() {
  return normalizeDeploymentMode(await getAppSetting('deployment_mode', 'both'));
}

// Never return the TURN token, only whether one is set and enough of the key id
// to recognise which credential is in use. The token is write-only over the API:
// there is no code path that reads it back out to a browser.
async function turnStatus() {
  const { keyId, token } = await turnCredentials();
  return {
    configured: Boolean(keyId && token),
    key_id_hint: keyId ? `…${keyId.slice(-4)}` : null
  };
}
app.get('/api/admin/settings', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  return {
    deployment_mode: await getDeploymentMode(),
    cf_turn: await turnStatus(),
    // Server scope only. This destination receives the application's own
    // operational logs and deliberately never tenant or connector data.
    server_log_url: await getAppSetting('server_log_url', '') || ''
  };
});
app.put('/api/admin/settings', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const body = req.body || {};
  if ('deployment_mode' in body) {
    await setAppSetting('deployment_mode', normalizeDeploymentMode(body.deployment_mode));
  }
  // Cloudflare TURN credentials. Empty string clears; absent leaves unchanged,
  // so the UI can save other settings without having to re-enter the token.
  if ('cf_turn_key_id' in body) {
    await setSecretSetting('cf_turn_key_id', String(body.cf_turn_key_id || '').trim());
  }
  if ('cf_turn_api_token' in body) {
    await setSecretSetting('cf_turn_api_token', String(body.cf_turn_api_token || '').trim());
  }
  if ('server_log_url' in body) {
    const u = String(body.server_log_url || '').trim();
    await setAppSetting('server_log_url', u || null);
    setSink('server', u, { job: 'openprinthq-control-plane', scope: 'server' });
  }
  return { ok: true, deployment_mode: await getDeploymentMode(), cf_turn: await turnStatus() };
});
// Ask Cloudflare to mint a throwaway credential so the owner finds out the
// token is wrong here, rather than from a camera that silently fails to
// connect for one user on one network three weeks later.
app.post('/api/admin/settings/turn-test', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const { keyId, token } = await turnCredentials();
  if (!keyId || !token) return reply.code(400).send({ ok: false, error: 'No TURN key id / token saved yet.' });
  const r = await mintTurn(60);
  if (!r) return reply.code(400).send({ ok: false, error: 'TURN is not configured.' });
  if (r.error) return reply.code(502).send({ ok: false, error: `Cloudflare rejected the credentials: ${r.error}` });
  const urls = [].concat(r.iceServers?.urls || []);
  return { ok: true, relay_urls: urls.length, sample: urls.slice(0, 3) };
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
// Deployment-wide UI config. `deployment_mode` = 'cloud' | 'local'. Cloud shows
// the Cloud Client downloads + local connectors and defaults printer-add to
// "via connector"; local hides them (the engine is on the printers' LAN, so it
// reaches them directly). Public so the UI can shape itself before login.
app.get('/api/pub/config', async () => {
  return { connector_ws: true, deployment_mode: await getDeploymentMode() };
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
  // duplicate_agents surfaces the eviction alarm: repeated session replacement
  // means two agents are sharing this token and evicting each other, which the
  // user otherwise only sees as printers mysteriously flapping offline.
  return list.map((c) => ({
    ...c,
    online: isConnectorOnline(c.id),
    recent_session_replacements: connectorEvictionCount(c.id),
    duplicate_agents: connectorHasDuplicateAgents(c.id),
    // Self-reported by the agent, so decoration only — never used for auth.
    client: connectorClientIdentity(c.id)
  }));
});
app.post('/api/connectors', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const name = (req.body?.name || 'connector').toString().slice(0, 60);
  // Provision the command-signing key pair alongside the connector. Without
  // this, a fresh account holds a connector it cannot send signed commands to,
  // and the first job would have to generate the key on the hot path.
  try { await ensureKeyPair(user.id); }
  catch (err) { app.log.error({ err, userId: user.id }, 'ensureKeyPair failed on connector create'); }
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
  // Optional caller-supplied subnet to scan (defaults on the agent to its host
  // LAN). Enforce a /24 max so a scan can't fan out across a huge range.
  let subnet = (req.body?.subnet || '').toString().trim();
  if (subnet) {
    const m = subnet.match(/^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\/(\d{1,2})$/);
    if (!m || Number(m[2]) < 24 || Number(m[2]) > 32) {
      return reply.code(400).send({ error: 'subnet must be CIDR /24 or narrower (e.g. 192.168.1.0/24)' });
    }
  }
  dbg('connector', 'discover -> connector', { userId: user.id, connectorId: id, window_ms: windowMs, subnet: subnet || 'auto' });
  const job = { kind: 'discover', window_ms: windowMs };
  if (subnet) job.subnet = subnet;
  const r = await proxyViaConnector(user.id, job, windowMs + 8000, id);
  // Apply the learned friendly-name mapping to discovered devices (display only).
  const devices = await Promise.all((r.devices || []).map(async (d) => {
    if (d.vendor && d.model) {
      const hit = await getModelName(d.vendor, d.model);
      if (hit?.friendly_name) return { ...d, friendly_model: hit.friendly_name };
    }
    return d;
  }));
  dbg('connector', 'discover result', { userId: user.id, connectorId: id, found: devices.length, error: r.error || null });
  return { connector_online: true, devices, error: r.error || null };
});

// ---- printer model-name mapping (P4/P5) ---------------------------------
// Public-ish lookup: given vendor+code, return the learned friendly name (used
// by the add form to pre-fill Model). Requires an authenticated user.
app.get('/api/model-names/lookup', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const hit = await getModelName(req.query?.vendor, req.query?.code);
  return { friendly_name: hit?.friendly_name || null, locked: !!hit?.locked };
});
// Learn a mapping when a user names a model on add (fill-when-empty; never
// overwrites an existing or locked entry). Any authenticated user can teach.
app.post('/api/model-names/learn', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const { vendor, code, friendly_name } = req.body || {};
  if (!vendor || !code || !friendly_name) return { ok: false };
  const row = await learnModelName(vendor, code, friendly_name);
  return { ok: true, mapping: row || null };
});
// Admin CRUD for the Printer Names tab (owner-only).
app.get('/api/admin/model-names', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  return { model_names: await listModelNames() };
});
app.put('/api/admin/model-names', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const { vendor, code, friendly_name, locked } = req.body || {};
  if (!vendor || !code || !friendly_name) return reply.code(400).send({ error: 'vendor, code, friendly_name required' });
  const row = await upsertModelNameForce(vendor, code, friendly_name, !!locked);
  return { ok: true, mapping: row };
});
app.patch('/api/admin/model-names/lock', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const { vendor, code, locked } = req.body || {};
  const row = await setModelNameLock(vendor, code, !!locked);
  if (!row) return reply.code(404).send({ error: 'mapping not found' });
  return { ok: true, mapping: row };
});
app.delete('/api/admin/model-names', async (req, reply) => {
  const owner = await requireOwner(req, reply); if (!owner) return;
  const { vendor, code } = req.body || {};
  if (!vendor || !code) return reply.code(400).send({ error: 'vendor, code required' });
  await deleteModelName(vendor, code);
  return { ok: true };
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
  if (!k?.public_pem) return { public_pem: null, fingerprint: null, created_at: null };
  // The fingerprint is what an operator compares against the value their agent
  // prints when it pins the key. Without it, trust-on-first-use has no
  // out-of-band confirmation step and the pin is only as good as the network
  // during pairing.
  return { public_pem: k.public_pem, fingerprint: fingerprint(k.public_pem), created_at: k.created_at || null };
});
app.post('/api/connector/signing-key', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const { publicPem, privatePem } = generateKeyPair();
  await setSigningKey(user.id, publicPem, encryptPrivate(privatePem));
  invalidateSigningCache(user.id);
  // Rotation invalidates the pin held by every paired connector. Those
  // connectors reject all commands until they are re-paired.
  app.log.warn({ userId: user.id, fingerprint: fingerprint(publicPem) }, 'connector signing key rotated');
  return { public_pem: publicPem, fingerprint: fingerprint(publicPem) };
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

// ---- durable access keys (browser extension, scripts) --------------------
// The same print-host token a slicer session gets, but minted by the user and
// with no session to expire with. It is the credential the browser extension
// holds, and it is deliberately the SAME kind of credential rather than a new
// one: it reaches the same three things (presign, rescan, print-host upload),
// so a second token type would only mean two things to revoke.
//
// Kept apart from slicer tokens by `kind`, because the slicer bootstrap purges
// a user's tokens on every session start.
//
// The secret is stored as written, matching the existing print-host and
// integration tokens. Hashing at rest is the right end state and is a change to
// make for all three at once, not one that should land here and leave the table
// half one scheme and half the other.
const MAX_EXT_KEYS = 10;

app.get('/api/access-keys', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const rows = await listPrintHostTokens(user.id);
  return rows.map((r) => ({
    id: r.token_id,
    label: r.label,
    kind: r.kind,
    prefix: r.token_prefix,
    createdAt: r.created_at,
    lastUsedAt: r.last_used_at,
    expiresAt: r.expires_at
  }));
});

app.post('/api/access-keys', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const label = String(req.body?.label || '').trim().slice(0, 80) || 'Browser extension';
  // A cap, because there is no reason for an account to hold dozens and an
  // unbounded list is how a leaked session quietly mints a permanent foothold.
  if (await countPrintHostTokens(user.id, 'extension') >= MAX_EXT_KEYS) {
    return reply.code(409).send({ error: 'key limit reached, revoke one first' });
  }
  // Optional expiry in days. No default: a key the user has to re-issue on a
  // schedule they did not ask for is a key they will paste into a text file.
  const days = Number(req.body?.expiresInDays || 0);
  const expiresAt = days > 0 ? new Date(Date.now() + days * 86400000) : null;
  const secret = 'ophqx_' + randomBytes(24).toString('base64url');
  const { token, tokenId } = await createPrintHostToken(user.id, secret, label, expiresAt, 'extension');
  req.log.info({ userId: user.id, label }, 'access key minted');
  // The only time the secret is ever returned.
  return reply.code(201).send({
    token,
    id: tokenId,
    label,
    expiresAt: expiresAt ? expiresAt.toISOString() : null
  });
});

app.delete('/api/access-keys/:id', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const ok = await revokePrintHostToken(user.id, String(req.params.id));
  if (!ok) return reply.code(404).send({ error: 'no such key' });
  req.log.info({ userId: user.id }, 'access key revoked');
  return { revoked: true };
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
// ---- agent-local camera relay (Option A; OctoEverywhere-style) ----------
// For connector-routed printers the cloud engine can't reach the printer's LAN
// camera. Instead the on-LAN agent runs go2rtc locally (Bambu RTSPS) or fetches
// the webcam URL locally (Klipper), and relays a JPEG frame up through the
// connector tunnel. The engine's external_camera_url is pointed at the INTERNAL
// endpoint below (gateway-secret gated, keyed by userId+printerId) so the
// engine's existing external-camera path just works. Architecture credit:
// OctoEverywhere (https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere, AGPL-3.0).
async function relayCameraFrame(userId, pid, reply) {
  const autoAll = await getAutomation(userId);
  const connectorId = autoAll[pid]?.connector_id ?? null;
  if (!connectorId) return reply.code(409).send({ error: 'printer is not connector-routed' });
  const inst = await getInstanceForUser(userId);
  const base = engineBase(inst);
  if (!base) return reply.code(409).send({ error: 'no running instance' });
  let printer;
  try { printer = await (await fetch(`${base}/api/v1/printers/${pid}`)).json(); }
  catch { return reply.code(404).send({ error: 'printer not found' }); }
  const vendor = printer.connection_type;
  const directHost = autoAll[pid]?.direct_host || printer.ip_address;
  const job = vendor === 'bambu'
    // Carry the address and access code so the connector can re-register the
    // stream if its go2rtc restarted since registration. Without them a lost
    // go2rtc meant frames failed forever, because the control-plane only
    // registers a camera that is not already enabled.
    ? { kind: 'camera-frame', vendor, name: `p${pid}`, printer_id: pid, ip: directHost, access_code: printer.access_code }
    : { kind: 'camera-frame', vendor, snapshot_url: `http://${directHost}/webcam/?action=snapshot`, printer_id: pid };
  const r = await proxyViaConnector(userId, job, 12000, connectorId);
  if (!r || r.status !== 200 || !r.body) return reply.code(r?.status || 502).send({ error: r?.error || 'frame relay failed' });
  reply.header('content-type', (r.headers && r.headers['content-type']) || 'image/jpeg');
  reply.header('cache-control', 'no-cache, no-store, must-revalidate');
  return reply.send(Buffer.from(r.body, 'base64'));
}
// Internal (engine-facing): the printer's external_camera_url points here.
app.get('/api/internal/camera-relay/:userId/:printerId/frame', async (req, reply) => {
  // The engine fetches this URL itself and has no way to attach a header to an
  // external camera URL, so the secret is also accepted as a query parameter.
  // The URL never leaves the docker network -- it is stored in the tenant DB and
  // fetched container-to-container -- so it does not reach a proxy access log.
  const presented = req.headers['x-ophq-gateway'] || req.query?.gw;
  const gatewayOk = !GATEWAY_SECRET || presented === GATEWAY_SECRET;
  if (!gatewayOk) return reply.code(403).send({ error: 'forbidden' });
  const userId = Number(String(req.params.userId).replace(/[^0-9]/g, ''));
  const pid = Number(String(req.params.printerId).replace(/[^0-9]/g, ''));
  if (!userId || !pid) return reply.code(400).send({ error: 'bad id' });
  return relayCameraFrame(userId, pid, reply);
});
// Browser-facing (authed): direct frame fetch for the logged-in user.
app.get('/api/camera-relay/:printerId/frame', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const pid = Number(String(req.params.printerId).replace(/[^0-9]/g, ''));
  if (!pid) return reply.code(400).send({ error: 'bad printer id' });
  return relayCameraFrame(user.id, pid, reply);
});

app.post('/api/camera/webrtc/:printerId', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const pid = String(req.params.printerId).replace(/[^0-9]/g, '');
  if (!pid) return reply.code(400).send({ error: 'bad printer id' });
  const inst = await getInstanceForUser(user.id);
  if (!inst) return reply.code(409).send({ error: 'no running instance' });
  const offer = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);

  // Connector-routed printers: the camera is on a LAN this server cannot reach,
  // so the cloud go2rtc could never pull it. Hand the offer to the connector's
  // OWN go2rtc instead. Only the SDP crosses this server; the media then flows
  // browser <-> connector directly, which is the whole point — it keeps camera
  // bandwidth off the cloud host and works without any inbound port forward.
  const autoAll = await getAutomation(user.id);
  const connectorId = autoAll[pid]?.connector_id ?? null;
  if (connectorId) {
    const base = engineBase(inst);
    if (!base) return reply.code(409).send({ error: 'no running instance' });
    let printer;
    try { printer = await (await fetch(`${base}/api/v1/printers/${pid}`)).json(); }
    catch { return reply.code(404).send({ error: 'printer not found' }); }
    const vendor = printer.connection_type;
    const directHost = autoAll[pid]?.direct_host || printer.ip_address;
    // Klipper has no RTSPS source, but Moonraker serves MJPEG, which go2rtc can
    // ingest and re-publish as WebRTC. Without this a Klipper printer was pinned
    // to relayed still frames while the Bambus went live: the same camera
    // behaving differently by printer brand, for no reason a user can see, and
    // paying server bandwidth the whole time.
    if (vendor !== 'bambu') {
      // Derive the webcam URL from the PRINTER's address, never from
      // external_camera_url: setupCameraRelay has already rewritten that to
      // point at this server's frame relay, so using it told the connector to
      // pull video from the cloud host it exists to bypass -- and that endpoint
      // serves single JPEGs, so ffmpeg had no stream to transcode.
      const snapshotUrl = `http://${directHost}/webcam/?action=snapshot`;
      const job = {
        kind: 'camera-webrtc', vendor, name: `p${pid}`, printer_id: Number(pid), offer,
        snapshot_url: snapshotUrl,
        ice_servers: await iceServers(86400)
      };
      const r = await proxyViaConnector(user.id, job, 20000, connectorId);
      if (!r || !r.ok || !r.answer) {
        return reply.code(502).send({ error: r?.error || 'connector did not answer the WebRTC offer' });
      }
      // go2rtc already returns a complete {type, sdp} object, so pass it through
      // untouched. Wrapping it again produced an answer whose sdp field was
      // itself JSON, which a browser silently rejects -- the handshake looked
      // successful on this side while the video never started.
      reply.header('content-type', 'application/json');
      return reply.send(r.answer);
    }
    const job = {
      kind: 'camera-webrtc',
      vendor: 'bambu',
      ip: directHost,
      access_code: printer.access_code,
      // Resolve the vendor code to the marketing name before it leaves here.
      // The connector must never have to know that "O1D" means "H2D": that
      // mapping lives in one table, on this side, and a new printer model
      // becomes a database row rather than a client release.
      model: await friendlyModelName('bambu', printer.model),
      // And don't make the connector infer capability from a name at all.
      // Two independent model lists had already drifted far enough that the
      // X1 Carbon's own code was missing from one of them.
      rtsp: supportsRtsp(printer.model),
      name: `p${pid}`,
      printer_id: Number(pid),
      offer,
      // Long TTL: go2rtc reads ICE servers at startup, and every rotation costs
      // it a restart. A day is well inside Cloudflare's allowed range and keeps
      // restarts rare.
      ice_servers: await iceServers(86400)
    };
    const r = await proxyViaConnector(user.id, job, 20000, connectorId);
    if (!r || !r.ok || !r.answer) {
      return reply.code(502).send({ error: r?.error || 'connector did not answer the WebRTC offer' });
    }
    reply.header('content-type', 'application/json');
    return reply.send(r.answer);
  }

  // Local (same-network) printers: the cloud go2rtc can reach them directly.
  const name = await ensureStream(inst, user.id, pid);
  if (!name) return reply.code(404).send({ error: 'no WebRTC camera stream for this printer' });
  try {
    const res = await fetch(`${GO2RTC_URL}/api/webrtc?src=${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: { 'content-type': req.headers['content-type'] || 'application/json' },
      body: offer
    });
    reply.code(res.status);
    const ct = res.headers.get('content-type'); if (ct) reply.header('content-type', ct);
    return reply.send(Buffer.from(await res.arrayBuffer()));
  } catch (e) {
    return reply.code(502).send({ error: 'signaling relay failed: ' + e.message });
  }
});

// Tells the UI whether a printer can do direct WebRTC video, so it can choose
// between the peer-to-peer player and the snapshot fallback without probing.
app.get('/api/camera/capability/:printerId', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  const pid = Number(String(req.params.printerId).replace(/[^0-9]/g, ''));
  if (!pid) return reply.code(400).send({ error: 'bad printer id' });
  const inst = await getInstanceForUser(user.id);
  if (!inst) return reply.code(409).send({ error: 'no running instance' });
  const autoAll = await getAutomation(user.id);
  const connectorId = autoAll[pid]?.connector_id ?? null;
  const base = engineBase(inst);
  let printer = null;
  try { printer = await (await fetch(`${base}/api/v1/printers/${pid}`)).json(); } catch { /* best effort */ }
  // Bambu supplies RTSPS; everything else can still go live if it exposes an
  // MJPEG webcam, which Moonraker does by default.
  const bambu = printer?.connection_type === 'bambu';
  const hasWebcam = Boolean(printer?.external_camera_url) || printer?.connection_type === 'klipper';
  const turn = await turnCredentials();
  return {
    printer_id: pid,
    routed_via_connector: Boolean(connectorId),
    webrtc: bambu || hasWebcam,
    webrtc_source: bambu ? 'rtsps' : (hasWebcam ? 'mjpeg' : null),
    // Direct video across CGNAT usually needs a relay; say so plainly rather
    // than letting the camera fail silently on a strict network.
    relay_available: Boolean(turn.keyId && turn.token),
    fallback: 'snapshot'
  };
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

// ---- tenant model library ------------------------------------------------
// Served on its OWN ORIGIN, not a sub-path.
//
// The library references every asset absolutely (/css/style.css, /js/app.js)
// and calls its own API as fetch('/api/...'). Behind a /vault prefix those
// resolve to the app's origin root, so the stylesheet 404s and its API calls
// collide with ours. Rewriting them in the proxy would mean rewriting its
// JavaScript, which breaks on the next upstream release. A separate host makes
// every one of those paths correct with no rewriting at all.
//
// Authentik gates the app host at the edge, and the ticket below carries that
// decision to this one.
const VAULT_HOST = process.env.OPHQ_VAULT_HOST || '';

// The library host is NOT behind Authentik, and that is deliberate.
//
// It is framed by the Files tab. Authentik's authorize endpoint sends
// X-Frame-Options: DENY and then renders a flow-executor page, so the SSO round
// trip a first visit needs can never complete inside an iframe: the frame is
// blocked and the tab shows a failure page even for a signed-in user.
//
// So the app, which IS behind Authentik, mints a short-lived signed ticket for
// the user it already authenticated, and the library host trusts that instead.
// Same shape as the print-host tokens: the trust comes from a secret only this
// service holds, not from a session the browser has to establish in a frame.
//
// The cookie that ticket becomes says WHICH TENANT a later request belongs to,
// and nothing else. The library itself is told who the person is on every
// proxied request, in signed headers, so there is no library session to
// establish, keep alive or hand to a browser.
const VAULT_TICKET_TTL = 60; // seconds; it is redeemed immediately on frame load
const VAULT_COOKIE = 'ophq_vault';

function vaultSign(payload) {
  return createHmac('sha256', GATEWAY_SECRET || 'ophq-vault').update(payload).digest('base64url');
}
function mintVaultTicket(userId) {
  const body = `${userId}.${Math.floor(Date.now() / 1000) + VAULT_TICKET_TTL}`;
  return `${body}.${vaultSign(body)}`;
}
/** Returns the user id, or null. Constant-time compare, and expiry is checked
 *  before the signature is trusted for anything. */
function readVaultTicket(t) {
  const parts = String(t || '').split('.');
  if (parts.length !== 3) return null;
  const [uid, exp, sig] = parts;
  const expect = vaultSign(`${uid}.${exp}`);
  if (sig.length !== expect.length) return null;
  if (!timingSafeEqual(Buffer.from(sig), Buffer.from(expect))) return null;
  if (Number(exp) < Math.floor(Date.now() / 1000)) return null;
  return Number(uid) || null;
}

function isVaultHost(req) {
  const h = String(req.headers.host || '').split(':')[0].toLowerCase();
  return !!VAULT_HOST && h === VAULT_HOST.toLowerCase();
}

// Entry point. Opens a library session for the signed-in user, sets the cookie
// for THIS origin, and bounces to the app. The iframe points here, never at /.
// Tell the app where the library lives, and whether it is available at all.
// Separate from the session route because that one runs on the LIBRARY host and
// this one runs on the app host.
app.get('/api/vault/status', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  if (!vaultEnabled() || !VAULT_HOST) { reply.code(503).send({ error: 'tenant library not configured' }); return; }
  const inst = await getInstanceForUser(user.id);
  if (!inst?.subdomain) return reply.code(409).send({ error: 'no instance for this account' });
  ensureVault(user.id, inst.subdomain, user.email).catch(() => {});
  return { url: `https://${VAULT_HOST}/__ophq/session?t=${mintVaultTicket(user.id)}` };
});

app.get('/__ophq/session', async (req, reply) => {
  const userId = readVaultTicket(req.query?.t);
  if (!userId) { reply.code(401).send({ error: 'invalid or expired ticket' }); return; }
  if (!vaultEnabled()) { reply.code(503).send({ error: 'tenant library not configured' }); return; }
  const user = await getUserById(userId);
  if (!user) { reply.code(403).send({ error: 'no-account' }); return; }
  const inst = await getInstanceForUser(user.id);
  if (!inst?.subdomain) return reply.code(409).send({ error: 'no instance for this account' });
  try {
    // Re-attach before anything else. A promotion recreates this container and
    // drops the network attachment, so the first request after a deploy would
    // otherwise fail with the library looking unreachable.
    await joinVaultNetwork(inst.subdomain).catch(() => {});
    await ensureVault(user.id, inst.subdomain, user.email).catch(() => {});
    // Our own identity for this host, and the only cookie in play: it tells the
    // proxy which tenant a later request is for. SameSite=None; Secure because
    // the library is framed from the app's origin, and in that third-party
    // context a Lax cookie is never sent back.
    const id = `${user.id}.${Math.floor(Date.now() / 1000) + 12 * 3600}`;
    reply.header('set-cookie',
      `${VAULT_COOKIE}=${id}.${vaultSign(id)}; Path=/; HttpOnly; Secure; SameSite=None`);
    return reply.redirect('/');
  } catch (e) {
    return reply.code(502).send({ error: 'library unreachable: ' + e.message });
  }
});

// Everything else on the library host is the library.
app.all('/*', { constraints: {} }, async (req, reply) => {
  if (!isVaultHost(req)) return reply.callNotFound();
  if (!vaultEnabled()) { reply.code(503).send({ error: 'tenant library not configured' }); return; }
  const userId = readVaultTicket(req.cookies?.[VAULT_COOKIE]);
  if (!userId) {
    // No ticket cookie: the frame was opened directly, or it expired. Send them
    // back through the app, which is where the identity actually lives.
    reply.code(401).send({ error: 'library session required' });
    return;
  }
  const user = await getUserById(userId);
  if (!user) { reply.code(403).send({ error: 'no-account' }); return; }
  const inst = await getInstanceForUser(user.id);
  if (!inst?.subdomain) return reply.code(409).send({ error: 'no instance for this account' });
  // The person's identity is asserted here, per request, and signed. Nothing
  // the browser sent authenticates anything to the library: our own cookie
  // only said which tenant this is, and it is deliberately not forwarded.
  const headers = { ...vaultUserHeaders(user) };
  for (const h of ['accept', 'accept-language', 'content-type', 'range']) {
    if (req.headers[h]) headers[h] = req.headers[h];
  }
  let body;
  if (!['GET', 'HEAD'].includes(req.method) && req.body !== undefined) {
    body = Buffer.isBuffer(req.body) ? req.body
      : (typeof req.body === 'string' ? req.body : JSON.stringify(req.body));
  }
  try {
    const res = await fetch(vaultBase(inst.subdomain) + req.url, { method: req.method, headers, body, redirect: 'manual' });
    reply.code(res.status);
    for (const h of ['content-type', 'cache-control', 'content-disposition', 'location', 'accept-ranges', 'content-range', 'etag', 'last-modified']) {
      const v = res.headers.get(h);
      if (v) reply.header(h, v);
    }
    return reply.send(Buffer.from(await res.arrayBuffer()));
  } catch (e) {
    return reply.code(502).send({ error: 'library unreachable: ' + e.message });
  }
});

// ---- boot ---------------------------------------------------------------
try {
  await migrate();
  // Wrap console FIRST: the restore below logs its own result, and installing
  // the wrapper afterwards meant those lines were the one thing that never
  // shipped -- the log claimed nothing about shipping while shipping worked.
  for (const level of ['log', 'warn', 'error']) {
    const orig = console[level].bind(console);
    console[level] = (...args) => {
      orig(...args);
      try { shipServer(args.map((a) => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ')); } catch { /* never break logging */ }
    };
  }
  // Restore configured log destinations so shipping survives a restart. Both
  // scopes are opt-in: an unconfigured deployment ships nothing anywhere.
  try {
    const serverUrl = await getAppSetting('server_log_url', '');
    if (serverUrl) {
      setSink('server', serverUrl, { job: 'openprinthq-control-plane', scope: 'server' });
      console.log(`[logship] server logs -> ${serverUrl}`);
    }
    for (const row of await listUserLogSinks()) {
      setSink(`tenant:${row.user_id}`, row.url, { job: 'openprinthq-instance', scope: 'tenant', instance: String(row.user_id) });
    }
  } catch (e) { console.error('[logship] restore failed', e.message); }
  reconcileRoutes().catch((e) => console.error('reconcileRoutes', e.message));
  // Resume + drive any running temperature-staggered batches.
  startOrchestrator(8000);
  await app.listen({ host: '0.0.0.0', port: PORT });
  app.log.info(`control-plane listening on ${PORT}`);
  shipServer(`control-plane started, listening on ${PORT}`);
} catch (err) {
  app.log.error(err);
  process.exit(1);
}
