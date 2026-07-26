// OpenPrintHQ control-plane — HTTP API
// SPDX-License-Identifier: AGPL-3.0-or-later
import Fastify from 'fastify';
import cookie from '@fastify/cookie';
import { readFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { migrate, upsertUser, getUserByEmail, getInstanceForUser, getCompatiblePresets,
  getCircuits, setCircuit, getAutomation, setAutomation,
  createConnector, listConnectors, deleteConnector, getBatchById,
  getIntegrationToken, setIntegrationToken, getUserByIntegrationToken } from './db.js';
import { registerConnectorRoutes, connectorOnline, isConnectorOnline, proxyViaConnector } from './connector.js';
import { provisionForUser } from './provisioner.js';
import { startBatch, activeBatchForUser, advanceBatch, cancelBatch, startOrchestrator } from './batch.js';

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

// ---- HMS error descriptions --------------------------------------------
// Static Bambu HMS dictionary; the printer detail page decodes a machine's
// raw hms_errors (attr+code -> short_code) against this map. Small enough to
// return whole and cache client-side.
app.get('/api/hms/descriptions', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  reply.header('cache-control', 'public, max-age=86400');
  return HMS_DESCRIPTIONS;
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
  for (const [pid, cfg] of Object.entries(map)) {
    const id = Number(pid);
    if (!Number.isInteger(id) || !cfg || typeof cfg !== 'object') continue;
    await setAutomation(user.id, id, { auto_eject: !!cfg.auto_eject, eject_gcode: cfg.eject_gcode });
  }
  return await getAutomation(user.id);
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
  // Resume + drive any running temperature-staggered batches.
  startOrchestrator(8000);
  await app.listen({ host: '0.0.0.0', port: PORT });
  app.log.info(`control-plane listening on ${PORT}`);
} catch (err) {
  app.log.error(err);
  process.exit(1);
}
