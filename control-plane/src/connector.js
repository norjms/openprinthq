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
import { getConnectorByToken, touchConnector, setConnectorClientKey, setConnectorHostCidr, getAutomation } from './db.js';
import { signJobIfKeyed } from './signing.js';
import { dbg } from './debuglog.js';

// connectorId -> { raw, userId, name, lastSeen, heartbeat }
const streams = new Map();
// jobId -> { resolve, timer }   (one-shot HTTP proxy jobs)
const pending = new Map();
// streamId -> { userId, connectorId, emitter }   (long-lived raw TCP tunnels)
const tcpStreams = new Map();
// listenPort -> net.Server   (auto-activation relays; see openTcpRelay)
const relays = new Map();
// Broker/rendezvous: client endpoint registry (connectorId -> {userId,host,port,gatewayPort,secret,ts}).
const brokerEndpoints = new Map();
// Late-bound hook (set by server.js) to re-activate routes when a client
// (re)registers its endpoint. Avoids a circular import with routing.js.
let onEndpointRegistered = null;
export function setOnEndpointRegistered(fn) { onEndpointRegistered = fn; }
const BROKER_ENDPOINT_TTL_MS = 90_000;   // stale endpoints expire (heartbeat is ~30s)

// The control-plane's own hostname on the docker network — engines connect here
// when a printer is routed "via connector". A stable, per-printer relay port
// keeps the engine's stored address valid across control-plane restarts.
export const RELAY_HOST = process.env.OPHQ_RELAY_HOST || 'openprinthq-control-plane-1';
// Stable relay port per (printer, endpoint index) — up to 10 endpoints/printer.
export function relayPort(printerId, idx = 0) { return 39000 + Number(printerId) * 10 + Number(idx); }
export function relayPortForPrinter(printerId) { return relayPort(printerId, 0); }

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

// Trust-on-first-use ("sticky" key). If the connector already has a locked key,
// the client must prove possession of it (verifyClientAuth) — a different key is
// rejected until the key is reset in Settings → Connectors. If NO key is locked
// yet, the first client to present a valid pubkey + self-signature over
// `${token}.${ts}` locks onto it, so onboarding needs no manual key copy/paste.
// A client that presents no key at all is a legacy token-only client (allowed,
// not locked).
async function ensureClientAuth(conn, req) {
  if (conn.client_public_pem) return verifyClientAuth(conn, req);
  const ts = req.headers['x-ophq-client-ts'];
  const sig = req.headers['x-ophq-client-sig'];
  const pubB64 = req.headers['x-ophq-client-pubkey'];
  if (!(ts && sig && pubB64)) return true;                      // legacy token-only
  if (Math.abs(Date.now() - Number(ts)) > 120000) return false;
  let pem;
  try { pem = Buffer.from(String(pubB64), 'base64').toString('utf8'); }
  catch { return true; }
  let ok = false;
  try {
    const key = crypto.createPublicKey(pem);
    ok = crypto.verify('sha256', Buffer.from(`${conn.token}.${ts}`), { key, ...CLIENT_PSS }, Buffer.from(sig, 'base64'));
  } catch { return true; }                                       // unparseable → treat as legacy
  if (!ok) { dbg('connector', 'TOFU: rejected client — bad self-signature', { connectorId: conn.id }); return false; }
  try { await setConnectorClientKey(conn.user_id, conn.id, pem); conn.client_public_pem = pem; }
  catch (e) { dbg('connector', 'TOFU: could not persist key', { connectorId: conn.id, err: e.message }); }
  dbg('connector', 'TOFU: locked client key on first connect', { connectorId: conn.id, userId: conn.user_id });
  return true;
}

// Resolve the stream to use. With a connectorId (a specific "site"), return
// exactly that connector's stream if it belongs to the user and is online —
// this is what makes multi-site work: a printer routed to site B tunnels
// through B, never through whichever happened to connect first. With no
// connectorId, fall back to the first online connector (single-site default;
// "first connector wins").
function connectorFor(userId, connectorId = null) {
  if (connectorId != null && connectorId !== '') {
    const s = streams.get(Number(connectorId));
    return (s && s.userId === userId) ? s : null;
  }
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
export function proxyViaConnector(userId, job, timeoutMs = 22000, connectorId = null) {
  let target = connectorFor(userId, connectorId);
  if (!target) {
    dbg('connector', 'proxyViaConnector: NO connector online', { userId, connectorId, job: { kind: job.kind, method: job.method, url: job.url, host: job.host, port: job.port } });
    return Promise.resolve({ status: 503, error: connectorId != null ? 'the selected connector (site) is offline' : 'no connector online for this account' });
  }
  const id = crypto.randomUUID();
  const j = { id, ...job };
  dbg('connector', 'proxyViaConnector: -> connector', { userId, connectorId: target.connectorId, id, kind: job.kind, method: job.method, url: job.url, host: job.host, port: job.port });
  return new Promise((resolve) => {
    const timer = setTimeout(() => { pending.delete(id); dbg('connector', 'proxyViaConnector: TIMEOUT', { id, userId }); resolve({ status: 504, error: 'connector timeout' }); }, timeoutMs);
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
export function openTcpStream(userId, host, port, connectorId = null) {
  const target = connectorFor(userId, connectorId);
  const em = new EventEmitter();
  if (!target) { queueMicrotask(() => em.emit('close', connectorId != null ? 'selected connector (site) offline' : 'no connector online')); return em; }
  const id = crypto.randomUUID();
  tcpStreams.set(id, { userId, connectorId: target.connectorId, emitter: em });
  em.write = (buf) => {
    const s = connectorFor(userId, connectorId);
    if (!s) return false;
    try { writeToConnector(s, { id, kind: 'tcp-data', data: Buffer.from(buf).toString('base64') }); return true; }
    catch { return false; }
  };
  em.close = () => {
    const s = connectorFor(userId, connectorId);
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
export function openTcpRelay(userId, targetHost, targetPort, listenPort, connectorId = null) {
  const ident = `${targetHost}:${targetPort}@${connectorId ?? '*'}`;
  const existing = relays.get(listenPort);
  if (existing) { if (existing._ophqTarget === ident) return listenPort; try { existing.close(); } catch { /* */ } relays.delete(listenPort); }
  const server = net.createServer((socket) => {
    const t = openTcpStream(userId, targetHost, targetPort, connectorId);
    let open = false; const preOpen = [];
    socket.on('data', (d) => { if (open) t.write(d); else preOpen.push(d); });
    t.on('open', () => { open = true; for (const d of preOpen.splice(0)) t.write(d); });
    t.on('data', (d) => { try { socket.write(d); } catch { /* */ } });
    t.on('close', () => { try { socket.end(); } catch { /* */ } });
    socket.on('close', () => { try { t.close(); } catch { /* */ } });
    socket.on('error', () => { try { t.close(); } catch { /* */ } });
  });
  server._ophqTarget = ident;
  server.on('error', (e) => console.error('relay', listenPort, e.message));
  server.listen(listenPort, '0.0.0.0');
  relays.set(listenPort, server);
  return listenPort;
}
export function closeRelay(listenPort) {
  const s = relays.get(listenPort);
  if (s) { try { s.close(); } catch { /* */ } relays.delete(listenPort); }
}

export function connectorOnline(userId, connectorId = null) {
  if (connectorId != null && connectorId !== '') {
    const s = streams.get(Number(connectorId));
    return !!(s && s.userId === userId);
  }
  for (const s of streams.values()) if (s.userId === userId) return true;
  return false;
}

export function isConnectorOnline(connectorId) { return streams.has(connectorId); }

export function registerConnectorRoutes(app) {
  // ==== Broker registration (docs/broker-architecture.md) ==================
  // Client posts its public endpoint; we store it, hand back a per-connector
  // gateway secret (for browser-token verification) + the printer inventory it
  // fronts. Printer bytes never transit here - browsers talk to the client directly.
  app.post('/api/connector/register-endpoint', async (req, reply) => {
    const conn = await getConnectorByToken(bearer(req));
    if (!conn) return reply.code(401).send({ error: 'invalid connector token' });
    if (!(await ensureClientAuth(conn, req))) return reply.code(401).send({ error: 'client key authentication failed' });
    const b = req.body || {};
    const srcIp = (req.headers['x-forwarded-for'] || req.ip || '').toString().split(',')[0].trim();
    const host = (b.public_host && String(b.public_host).trim()) || srcIp;
    const port = Number(b.public_port) || Number(b.gateway_port) || 8787;
    let rec = brokerEndpoints.get(conn.id);
    if (!rec) rec = { secret: crypto.randomBytes(32).toString('base64url') };
    rec.userId = conn.user_id; rec.host = host; rec.port = port;
    rec.gatewayPort = Number(b.gateway_port) || port; rec.ts = Date.now();
    // Single-port model: the client fronts EVERY printer behind one gateway port
    // (host:port above). No per-printer forward ports, no separate passthrough
    // port - so nothing else to record here. The engine reaches a printer by
    // opening an OPHQ1 tunnel to that one port (see brokerEngineEndpoint).
    brokerEndpoints.set(conn.id, rec);
    touchConnector(conn.id).catch(() => {});
    let printers = [];
    try { printers = await brokerPrintersForConnector(conn.user_id, conn.id); } catch { /* best effort */ }
    // Re-activate routes so the engine is pointed at this fresh endpoint. The
    // engine reaches each printer through the client's single gateway port via an
    // in-engine loopback shim, so there's no forward map to wait for.
    if (onEndpointRegistered) { setImmediate(() => onEndpointRegistered(conn.user_id, conn.id).catch(() => {})); }
    return { gateway_secret: rec.secret, public_host: host, public_port: port, printers };
  });

  // Agent → cloud: long-lived SSE stream carrying jobs down to the LAN.
  app.get('/api/connector/stream', async (req, reply) => {
    const conn = await getConnectorByToken(bearer(req));
    if (!conn) return reply.code(401).send({ error: 'invalid connector token' });
    if (!(await ensureClientAuth(conn, req))) return reply.code(401).send({ error: 'client key authentication failed — this connector is locked to a different client key; reset it in Settings → Connectors to pair a new client' });
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
    // The agent reports its Docker host's LAN CIDR (network_mode: host, so it
    // sees the real host interfaces). Persist it so the UI can default the scan
    // subnet field. Validated loosely here; the UI enforces the /24 cap.
    const hostCidr = (req.query?.host_cidr || '').toString().slice(0, 64);
    if (hostCidr) { setConnectorHostCidr(conn.id, hostCidr).catch(() => {}); }
    const heartbeat = setInterval(() => { try { raw.write(': ping\n\n'); } catch { /* closed */ } }, 20000);
    streams.set(conn.id, { raw, userId: conn.user_id, connectorId: conn.id, name, lastSeen: Date.now(), heartbeat });
    dbg('connector', 'stream CONNECTED', { connectorId: conn.id, userId: conn.user_id, name, keyed: !!conn.client_public_pem });
    touchConnector(conn.id).catch(() => {});
    const cleanup = () => {
      clearInterval(heartbeat);
      const s = streams.get(conn.id);
      if (s && s.raw === raw) streams.delete(conn.id);
      dbg('connector', 'stream CLOSED', { connectorId: conn.id, userId: conn.user_id });
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
    if (p) { clearTimeout(p.timer); pending.delete(id); dbg('connector', 'result <- connector', { id, connectorId: conn.id, status: body.status, error: body.error }); p.resolve(body); }
    return { ok: true };
  });
}

// ---- Broker helpers (docs/broker-architecture.md) ------------------------
// Expire stale endpoints (client stopped heartbeating).
function brokerEndpointFresh(rec) { return rec && (Date.now() - rec.ts) < BROKER_ENDPOINT_TTL_MS; }

// The printers a connector fronts, as bridge targets for the client gateway.
// Derived from automation rows assigned to this connector. direct_host is the
// printer's real LAN IP (activateRoute stored it); direct_port its API port.
export async function brokerPrintersForConnector(userId, connectorId) {
  const auto = await getAutomation(userId);
  const out = [];
  for (const [pid, cfg] of Object.entries(auto || {})) {
    if ((cfg.connector_id ?? null) !== connectorId) continue;
    if (!cfg.direct_host) continue;                       // no real address known yet
    out.push({
      id: Number(pid),
      ip: cfg.direct_host,
      moonraker_port: Number(cfg.direct_port) || 7125,
      vendor: cfg.vendor || null                          // enriched by caller if available
    });
  }
  return out;
}

// Look up the client endpoint that fronts a given printer, for a browser.
// Returns { host, port, secret } or null if no fresh endpoint fronts it.
export async function brokerEndpointForPrinter(userId, printerId) {
  const auto = await getAutomation(userId);
  const cfg = auto[printerId];
  if (!cfg || cfg.connector_id == null) return null;
  const rec = brokerEndpoints.get(cfg.connector_id);
  if (!brokerEndpointFresh(rec) || rec.userId !== userId) return null;
  return { host: rec.host, port: rec.port, secret: rec.secret };
}

// Expose the map for the registration route (same module scope).
export function _brokerEndpoints() { return brokerEndpoints; }

// Single-port broker endpoint for a printer's ENGINE path (docs/broker-architecture.md).
// There are no per-printer forward ports anymore: every printer is reached through
// the client's ONE gateway port. Returns the client public host+port, plus a
// short-lived token the client verifies on the raw OPHQ1 engine tunnel. The engine
// shim opens that tunnel (client_host:client_port) with the preamble
//   OPHQ1 <token> <printerId> <targetPort>
// and TLS (Bambu MQTT/FTPS) rides through end-to-end.
//   Returns { host, port, token }  or null if no fresh endpoint fronts it.
export function brokerEngineEndpoint(connectorId, printerId) {
  const rec = brokerEndpoints.get(connectorId);
  if (!brokerEndpointFresh(rec) || !rec.host) return null;
  const claims = { printer_id: String(printerId), exp: Date.now() + 10 * 60 * 1000, scope: 'engine' };
  const b64 = Buffer.from(JSON.stringify(claims)).toString('base64url');
  const sig = crypto.createHmac('sha256', rec.secret).update(b64).digest('base64url');
  return { host: rec.host, port: rec.port, token: b64 + '.' + sig };
}
