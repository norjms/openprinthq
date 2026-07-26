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
import net from 'node:net';
import { EventEmitter } from 'node:events';
import { getConnectorByToken, touchConnector } from './db.js';
import { signJobIfKeyed } from './signing.js';

// connectorId -> { raw, userId, name, lastSeen, heartbeat }
const streams = new Map();
// jobId -> { resolve, timer }   (one-shot HTTP proxy jobs)
const pending = new Map();
// streamId -> { userId, connectorId, emitter }   (long-lived raw TCP tunnels)
const tcpStreams = new Map();
// listenPort -> net.Server   (auto-activation relays; see openTcpRelay)
const relays = new Map();

// The control-plane's own hostname on the docker network — engines connect here
// when a printer is routed "via connector". A stable, per-printer relay port
// keeps the engine's stored address valid across control-plane restarts.
export const RELAY_HOST = process.env.OPHQ_RELAY_HOST || 'openprinthq-control-plane-1';
export function relayPortForPrinter(printerId) { return 39000 + Number(printerId); }

// Mutual auth: if a connector has registered its own public key, it must prove
// possession of the matching private key on every stream-connect by signing
// `${token}.${ts}`. A leaked bearer token alone is then not enough to
// impersonate the connector. No key registered -> token-only (backward compat).
const CLIENT_PSS = { padding: crypto.constants.RSA_PKCS1_PSS_PADDING, saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST };
function verifyClientAuth(conn, req) {
  if (!conn.client_public_pem) return true;
  const ts = req.headers['x-ophq-client-ts'];
  const sig = req.headers['x-ophq-client-sig'];
  if (!ts || !sig) return false;
  if (Math.abs(Date.now() - Number(ts)) > 120000) return false;
  try {
    const key = crypto.createPublicKey(conn.client_public_pem);
    return crypto.verify('sha256', Buffer.from(`${conn.token}.${ts}`), { key, ...CLIENT_PSS }, Buffer.from(sig, 'base64'));
  } catch { return false; }
}

function connectorFor(userId) {
  for (const s of streams.values()) if (s.userId === userId) return s;
  return null;
}
function writeToConnector(target, obj) { target.raw.write(`data: ${JSON.stringify(obj)}\n\n`); }

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
  const j = { id, ...job };
  return new Promise((resolve) => {
    const timer = setTimeout(() => { pending.delete(id); resolve({ status: 504, error: 'connector timeout' }); }, timeoutMs);
    pending.set(id, { resolve, timer });
    signJobIfKeyed(userId, j).then(() => {
      try { target.raw.write(`data: ${JSON.stringify(j)}\n\n`); }
      catch { clearTimeout(timer); pending.delete(id); resolve({ status: 502, error: 'connector write failed' }); }
    }).catch(() => { clearTimeout(timer); pending.delete(id); resolve({ status: 500, error: 'signing failed' }); });
  });
}

// Open a raw bidirectional TCP tunnel to host:port through the user's
// connector. Returns an EventEmitter that emits 'open', 'data' (Buffer),
// 'close' (error?), and exposes .write(buf) and .close(). Carries any TCP
// protocol (Bambu MQTT 8883, FTP 990, …), not just HTTP.
export function openTcpStream(userId, host, port) {
  const target = connectorFor(userId);
  const em = new EventEmitter();
  if (!target) { queueMicrotask(() => em.emit('close', 'no connector online')); return em; }
  const id = crypto.randomUUID();
  tcpStreams.set(id, { userId, connectorId: target.connectorId, emitter: em });
  em.write = (buf) => {
    const s = connectorFor(userId);
    if (!s) return false;
    try { writeToConnector(s, { id, kind: 'tcp-data', data: Buffer.from(buf).toString('base64') }); return true; }
    catch { return false; }
  };
  em.close = () => {
    const s = connectorFor(userId);
    if (s) try { writeToConnector(s, { id, kind: 'tcp-close' }); } catch { /* */ }
    tcpStreams.delete(id);
  };
  const openJob = { id, kind: 'tcp-open', host, port };
  signJobIfKeyed(userId, openJob).then(() => {
    try { writeToConnector(target, openJob); }
    catch { tcpStreams.delete(id); em.emit('close', 'connector write failed'); }
  }).catch(() => { tcpStreams.delete(id); em.emit('close', 'signing failed'); });
  return em;
}

// Auto-activation: stand up a local TCP listener on `listenPort` that tunnels
// every accepted connection to `targetHost:targetPort` through the user's
// connector. An engine pointed at RELAY_HOST:listenPort then reaches a printer
// on a remote LAN transparently. Idempotent per listenPort.
export function openTcpRelay(userId, targetHost, targetPort, listenPort) {
  const existing = relays.get(listenPort);
  if (existing) { if (existing._ophqTarget === `${targetHost}:${targetPort}`) return listenPort; try { existing.close(); } catch { /* */ } relays.delete(listenPort); }
  const server = net.createServer((socket) => {
    const t = openTcpStream(userId, targetHost, targetPort);
    let open = false; const preOpen = [];
    socket.on('data', (d) => { if (open) t.write(d); else preOpen.push(d); });
    t.on('open', () => { open = true; for (const d of preOpen.splice(0)) t.write(d); });
    t.on('data', (d) => { try { socket.write(d); } catch { /* */ } });
    t.on('close', () => { try { socket.end(); } catch { /* */ } });
    socket.on('close', () => { try { t.close(); } catch { /* */ } });
    socket.on('error', () => { try { t.close(); } catch { /* */ } });
  });
  server._ophqTarget = `${targetHost}:${targetPort}`;
  server.on('error', (e) => console.error('relay', listenPort, e.message));
  server.listen(listenPort, '0.0.0.0');
  relays.set(listenPort, server);
  return listenPort;
}
export function closeRelay(listenPort) {
  const s = relays.get(listenPort);
  if (s) { try { s.close(); } catch { /* */ } relays.delete(listenPort); }
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
    if (!verifyClientAuth(conn, req)) return reply.code(401).send({ error: 'client key authentication failed' });
    const raw = reply.raw;
    reply.hijack();
    // Keep the tunnel socket alive and never idle it out server-side; the agent
    // reconnects on drop-outs, and we replace any stale stream on reconnect.
    try { raw.socket.setKeepAlive(true, 15000); raw.socket.setTimeout(0); } catch { /* */ }
    const prev = streams.get(conn.id);
    if (prev) { try { clearInterval(prev.heartbeat); prev.raw.end(); } catch { /* */ } }
    raw.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
      'x-accel-buffering': 'no'
    });
    raw.write(': connected\n\n');
    const name = (req.query?.name || conn.name || 'connector').toString();
    const heartbeat = setInterval(() => { try { raw.write(': ping\n\n'); } catch { /* closed */ } }, 20000);
    streams.set(conn.id, { raw, userId: conn.user_id, connectorId: conn.id, name, lastSeen: Date.now(), heartbeat });
    touchConnector(conn.id).catch(() => {});
    const cleanup = () => {
      clearInterval(heartbeat);
      const s = streams.get(conn.id);
      if (s && s.raw === raw) streams.delete(conn.id);
      // Tear down any TCP tunnels that belonged to this connector.
      for (const [sid, t] of tcpStreams) if (t.connectorId === conn.id) { t.emitter.emit('close', 'connector disconnected'); tcpStreams.delete(sid); }
    };
    raw.on('close', cleanup);
    raw.on('error', cleanup);
  });

  // Agent → cloud: the result of a job it performed on the LAN. Handles both
  // one-shot HTTP jobs (resolve a pending promise) and raw-TCP stream events.
  app.post('/api/connector/result', async (req, reply) => {
    const conn = await getConnectorByToken(bearer(req));
    if (!conn) return reply.code(401).send({ error: 'invalid connector token' });
    const body = req.body || {};
    const { id, event } = body;
    touchConnector(conn.id).catch(() => {});
    if (event) {
      // Raw TCP tunnel event.
      const t = id && tcpStreams.get(id);
      if (t) {
        if (event === 'open') t.emitter.emit('open');
        else if (event === 'data') t.emitter.emit('data', Buffer.from(body.data || '', 'base64'));
        else if (event === 'close') { t.emitter.emit('close', body.error || null); tcpStreams.delete(id); }
      }
      return { ok: true };
    }
    const p = id && pending.get(id);
    if (p) { clearTimeout(p.timer); pending.delete(id); p.resolve(body); }
    return { ok: true };
  });
}
