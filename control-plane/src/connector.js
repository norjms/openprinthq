// OpenPrintHQ control-plane — local-connector gateway (#28/#29)
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Terminates the outbound SSE tunnel from local connector agents and lets the
// rest of the control-plane reach LAN printers through them (NAT/CGNAT-safe).
//
// This file is copied into control-plane/src/connector.js and wired from
// server.js via registerConnectorRoutes(app). It has no extra dependencies —
// SSE is written straight to the raw Node response after reply.hijack().
import crypto from 'node:crypto';
import { getConnectorByToken, touchConnector } from './db.js';

// connectorId -> { raw, userId, name, lastSeen, heartbeat }
const streams = new Map();
// jobId -> { resolve, timer }
const pending = new Map();

function bearer(req) {
  const h = req.headers['authorization'] || '';
  const m = /^Bearer\s+(.+)$/i.exec(h);
  return m ? m[1].trim() : (req.query?.token || '').toString();
}

// Push a job to the first live connector for this user; resolve when the agent
// posts the result back (or on timeout). Returns { status, headers, body(b64) }.
export function proxyViaConnector(userId, job, timeoutMs = 22000) {
  let target = null;
  for (const s of streams.values()) if (s.userId === userId) { target = s; break; }
  if (!target) return Promise.resolve({ status: 503, error: 'no connector online for this account' });
  const id = crypto.randomUUID();
  const payload = JSON.stringify({ id, ...job });
  return new Promise((resolve) => {
    const timer = setTimeout(() => { pending.delete(id); resolve({ status: 504, error: 'connector timeout' }); }, timeoutMs);
    pending.set(id, { resolve, timer });
    try { target.raw.write(`data: ${payload}\n\n`); }
    catch { clearTimeout(timer); pending.delete(id); resolve({ status: 502, error: 'connector write failed' }); }
  });
}

export function connectorOnline(userId) {
  for (const s of streams.values()) if (s.userId === userId) return true;
  return false;
}

export function isConnectorOnline(connectorId) { return streams.has(connectorId); }

export function registerConnectorRoutes(app) {
  // Agent → cloud: long-lived SSE stream carrying jobs down to the LAN.
  app.get('/api/connector/stream', async (req, reply) => {
    const conn = await getConnectorByToken(bearer(req));
    if (!conn) return reply.code(401).send({ error: 'invalid connector token' });
    const raw = reply.raw;
    reply.hijack();
    raw.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
      'x-accel-buffering': 'no'
    });
    raw.write(': connected\n\n');
    const name = (req.query?.name || conn.name || 'connector').toString();
    const heartbeat = setInterval(() => { try { raw.write(': ping\n\n'); } catch { /* closed */ } }, 20000);
    streams.set(conn.id, { raw, userId: conn.user_id, name, lastSeen: Date.now(), heartbeat });
    touchConnector(conn.id).catch(() => {});
    const cleanup = () => {
      clearInterval(heartbeat);
      const s = streams.get(conn.id);
      if (s && s.raw === raw) streams.delete(conn.id);
    };
    raw.on('close', cleanup);
    raw.on('error', cleanup);
  });

  // Agent → cloud: the result of a job it performed on the LAN.
  app.post('/api/connector/result', async (req, reply) => {
    const conn = await getConnectorByToken(bearer(req));
    if (!conn) return reply.code(401).send({ error: 'invalid connector token' });
    const { id } = req.body || {};
    const p = id && pending.get(id);
    if (p) { clearTimeout(p.timer); pending.delete(id); p.resolve(req.body); touchConnector(conn.id).catch(() => {}); }
    return { ok: true };
  });
}
