// OpenPrintHQ control-plane — HTTP API
// SPDX-License-Identifier: AGPL-3.0-or-later
import Fastify from 'fastify';
import cookie from '@fastify/cookie';
import { migrate, upsertUser, getUserByEmail, getInstanceForUser } from './db.js';
import { provisionForUser } from './provisioner.js';

const PORT = Number(process.env.PORT || 8080);
const SESSION_SECRET = process.env.SESSION_SECRET || 'dev-insecure-secret-change-me-in-prod-000000';
const COOKIE = 'ophq_sess';

const app = Fastify({ logger: true, trustProxy: true });
await app.register(cookie, { secret: SESSION_SECRET });

function setSession(reply, email) {
  reply.setCookie(COOKIE, email, {
    path: '/', httpOnly: true, sameSite: 'lax', signed: true,
    maxAge: 60 * 60 * 24 * 30
  });
}

function currentEmail(req) {
  const raw = req.cookies?.[COOKIE];
  if (!raw) return null;
  const un = app.unsignCookie(raw);
  return un.valid ? un.value : null;
}

async function requireUser(req, reply) {
  const email = currentEmail(req);
  if (!email) { reply.code(401).send({ error: 'not authenticated' }); return null; }
  const user = await getUserByEmail(email);
  if (!user) { reply.code(401).send({ error: 'unknown user' }); return null; }
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

// Placeholder fleet endpoints — return an honest empty shape until the engine
// adapters are wired. The dashboard renders "—" for null values.
app.get('/api/instance/stats', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return { printersOnline: 0, activeJobs: 0, queued: 0, successRate: null };
});

app.get('/api/instance/printers', async (req, reply) => {
  const user = await requireUser(req, reply); if (!user) return;
  return { printers: [] };
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
