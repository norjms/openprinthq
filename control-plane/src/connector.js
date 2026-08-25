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
import { getConnectorByToken, touchConnector, setConnectorClientKey, setConnectorHostCidr, getSigningPublic } from './db.js';
import { signJob, ensureKeyPair, fingerprint } from './signing.js';
import { dbg } from './debuglog.js';

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
// Stable relay port per (printer, endpoint index) — up to 10 endpoints/printer.
export function relayPort(printerId, idx = 0) { return 39000 + Number(printerId) * 10 + Number(idx); }

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
// Transport-agnostic send. An SSE session writes an event frame; a WebSocket
// session sends a text frame. Everything above this line is unaware of which.
// Fired when a connector session becomes usable. Reconcile runs at startup —
// before any agent has had time to reconnect — so anything that needs the
// connector present (camera registration) must hang off this instead, or it
// runs at the one moment the connector is guaranteed to be absent.
const onlineHandlers = [];
export function onConnectorOnline(fn) { onlineHandlers.push(fn); }
function announceOnline(connectorId, userId) {
  for (const fn of onlineHandlers) {
    try { Promise.resolve(fn(connectorId, userId)).catch(() => {}); } catch { /* never break the session */ }
  }
}

function writeToConnector(target, obj) { target.send(obj); }

// Bulk TCP payloads go as BINARY WebSocket frames when the transport supports
// it. On SSE they had to be base64 inside JSON, which inflated every byte by a
// third and forced the whole transfer through one ordered event stream — a
// large FTP upload would sit in front of a status poll and the UI would decide
// the printer had gone offline. Binary frames are chunked (see WS_CHUNK) so a
// big transfer interleaves with control traffic instead of blocking it.
const WS_CHUNK = 16 * 1024;
function sendTcpData(target, id, buf) {
  const b = Buffer.from(buf);
  if (target.sendData) {
    for (let off = 0; off < b.length; off += WS_CHUNK) {
      target.sendData(id, b.subarray(off, Math.min(off + WS_CHUNK, b.length)));
    }
    return;
  }
  writeToConnector(target, { id, kind: 'tcp-data', data: b.toString('base64') });
}

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
    signJob(userId, j).then(() => {
      // Must go through the transport, not target.raw: a WebSocket session has
      // no raw stream, so writing directly here failed every job on the
      // multiplexed tunnel while the SSE path kept working.
      try { writeToConnector(target, j); }
      catch { clearTimeout(timer); pending.delete(id); resolve({ status: 502, error: 'connector write failed' }); }
    }).catch((err) => {
      // Never fall through to an unsigned send. A job we cannot sign is a job
      // we do not send.
      dbg('connector', 'proxyViaConnector: SIGNING FAILED', { id, userId, kind: job.kind, err: String(err && err.message || err) });
      clearTimeout(timer); pending.delete(id);
      resolve({ status: 503, error: 'unable to sign command for this account' });
    });
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
    try { sendTcpData(s, id, buf); return true; }
    catch { return false; }
  };
  em.close = () => {
    const s = connectorFor(userId, connectorId);
    if (s) try { writeToConnector(s, { id, kind: 'tcp-close' }); } catch { /* */ }
    tcpStreams.delete(id);
  };
  // On a WebSocket session, hand the agent the compact stream index up front so
  // its upstream payloads can be binary too. Absent on SSE, where the agent
  // falls back to base64 JSON.
  const openJob = { id, kind: 'tcp-open', host, port };
  if (target.idxFor) openJob.sidx = target.idxFor(id);
  signJob(userId, openJob).then(() => {
    try { writeToConnector(target, openJob); }
    catch { tcpStreams.delete(id); em.emit('close', 'connector write failed'); }
  }).catch((err) => {
    dbg('connector', 'openTcpStream: SIGNING FAILED', { id, userId, host, port, err: String(err && err.message || err) });
    tcpStreams.delete(id);
    em.emit('close', 'unable to sign tcp-open for this account');
  });
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

/** The identity reported by the currently-connected agent, if any. */
export function connectorClientIdentity(connectorId) {
  return streams.get(connectorId)?.identity || null;
}

// ---------------------------------------------------------------------------
// Session eviction: replacing a connector's live session, loudly.
// ---------------------------------------------------------------------------
// A new session for a connector id unconditionally replaces the old one, in
// both transports. That is the right behaviour for a reconnect after a dropped
// tunnel, and it is a disaster when two agents hold the same token: they
// reconnect in turn, evict each other, and every eviction tears down the tunnels
// the loser owned. It happened on 2026-08-08 and again on 2026-08-24, and both
// times cost hours, because this replacement was silent — the operator saw
// Bambu printers flapping offline, not "you are running two clients".
//
// So evictions are logged at info level, never behind OPHQ_DEBUG, and counted.
// A connector whose session is replaced repeatedly in a short window is a
// duplicate-agent alarm and says so in plain words.

// ---------------------------------------------------------------------------
// Client identity
// ---------------------------------------------------------------------------
// Agents report who and where they are on connect (x-ophq-client-identity, a
// base64 JSON blob). Before this the server knew a connector's name and token
// and nothing else, so "which machine is reporting through this connector"
// could not be answered — least of all during a duplicate-agent fight, when it
// is the whole question.
//
// Treated as untrusted decoration, never as authentication: it is self-reported
// and trivially forgeable. Authentication is the token plus the pinned client
// key, and that is unchanged. So every field is clamped and coerced rather than
// trusted, and a malformed header costs nothing.
const IDENTITY_MAX_B64 = 4096;

function str(v, max) {
  if (v === null || v === undefined) return null;
  return String(v).slice(0, max);
}

export function parseClientIdentity(req) {
  const raw = req?.headers?.['x-ophq-client-identity'];
  if (!raw || typeof raw !== 'string' || raw.length > IDENTITY_MAX_B64) return null;
  try {
    const o = JSON.parse(Buffer.from(raw, 'base64').toString('utf8'));
    if (!o || typeof o !== 'object') return null;
    const pid = Number(o.pid);
    return {
      install_id: str(o.install_id, 32),
      hostname: str(o.hostname, 128),
      pid: Number.isFinite(pid) ? pid : null,
      platform: str(o.platform, 32),
      arch: str(o.arch, 16),
      version: str(o.version, 32),
      node: str(o.node, 32),
      started_at: str(o.started_at, 40)
    };
  } catch { return null; }
}

/** One-line rendering for logs; "unidentified" for pre-identity agents. */
export function describeIdentity(idy) {
  if (!idy) return 'unidentified (agent predates identity reporting)';
  const bits = [];
  if (idy.hostname) bits.push(`host=${idy.hostname}`);
  if (idy.install_id) bits.push(`install=${idy.install_id}`);
  if (idy.pid !== null) bits.push(`pid=${idy.pid}`);
  if (idy.version) bits.push(`v${idy.version}`);
  if (idy.platform) bits.push(`${idy.platform}/${idy.arch || '?'}`);
  return bits.join(' ') || 'unidentified';
}

const EVICTION_WINDOW_MS = 5 * 60 * 1000;
// A reconnecting agent evicts itself once per drop-out. Several within five
// minutes is a flaky link; this many is two agents taking turns.
const EVICTION_ALARM_COUNT = 4;
const evictions = new Map(); // connectorId -> { times: number[], alarmed: boolean }

/**
 * true when both sessions are the same install, false when demonstrably not,
 * null when it cannot be told (an agent too old to report identity). The
 * distinction matters: same host means one box with two autostart entries,
 * different hosts means two installs sharing a token, and the fixes differ.
 */
function sameHost(a, b) {
  if (!a || !b) return null;
  // Identical install ids settle it: the same install cannot be two machines.
  if (a.install_id && b.install_id && a.install_id === b.install_id) return true;
  // Otherwise the HOSTNAME decides, not the install id. Two installs on one box
  // — a background service and the desktop app, or a duplicate autostart entry —
  // each hold their own client key and so report different install ids while
  // being the same machine. Preferring the install id here would report "two
  // machines are sharing this token" for the single commonest cause of this
  // fault and send the reader hunting a second computer that does not exist.
  if (a.hostname && b.hostname) return a.hostname === b.hostname;
  return null;
}

function recordEviction(connectorId) {
  const now = Date.now();
  let e = evictions.get(connectorId);
  if (!e) { e = { times: [], alarmed: false }; evictions.set(connectorId, e); }
  e.times = e.times.filter((t) => now - t < EVICTION_WINDOW_MS);
  e.times.push(now);
  return e;
}

/** Recent session replacements for a connector, for the UI to surface. */
export function connectorEvictionCount(connectorId) {
  const e = evictions.get(connectorId);
  if (!e) return 0;
  const now = Date.now();
  return e.times.filter((t) => now - t < EVICTION_WINDOW_MS).length;
}

/** True when a connector is being replaced often enough to mean two agents. */
export function connectorHasDuplicateAgents(connectorId) {
  return connectorEvictionCount(connectorId) >= EVICTION_ALARM_COUNT;
}

/**
 * Tear down the previous session for this connector, saying who lost and who
 * won. Safe to call with no previous session.
 */
function evictPrevious(prev, conn, incoming) {
  if (!prev) return;
  try { clearInterval(prev.heartbeat); prev.closeSession?.(); } catch { /* already gone */ }
  const e = recordEviction(conn.id);
  const heldMs = prev.lastSeen ? Date.now() - prev.lastSeen : null;
  // Naming the machine on each side is the point: two entries with the same
  // host are one box with a duplicate autostart, two different hosts are two
  // installs sharing a token. Those need opposite fixes, and until now the log
  // could not tell them apart.
  console.log(
    `[control-plane][connector] session REPLACED connectorId=${conn.id} user=${conn.user_id} ` +
    `outgoing={name:"${prev.name}",transport:${prev.transport},${describeIdentity(prev.identity)}} ` +
    `incoming={name:"${incoming.name}",transport:${incoming.transport},ip:${incoming.ip || 'unknown'},${describeIdentity(incoming.identity)}} ` +
    `replacements_in_last_5min=${e.times.length}` +
    (heldMs !== null ? ` previous_session_last_seen_ms_ago=${heldMs}` : '') +
    (sameHost(prev.identity, incoming.identity) === true
      ? ' — SAME HOST: a duplicate autostart entry or a service and app both running'
      : sameHost(prev.identity, incoming.identity) === false
        ? ' — DIFFERENT HOSTS: two machines are sharing this connector token'
        : '')
  );
  if (e.times.length >= EVICTION_ALARM_COUNT && !e.alarmed) {
    e.alarmed = true;
    console.log(
      `[control-plane][connector] DUPLICATE AGENT ALARM connectorId=${conn.id} user=${conn.user_id} — ` +
      `this connector's session has been replaced ${e.times.length} times in five minutes. That is what ` +
      'two agents sharing one connector token look like: they evict each other on every reconnect and ' +
      'every eviction drops the tunnels the loser owned, which shows up as printers flapping offline and ' +
      'commands never landing. Find the second agent (a duplicate autostart entry, a second machine, or a ' +
      'service and a desktop app both running) and stop it. Newer agents refuse to start a second copy on ' +
      'one machine, but an older agent or a second host still can.'
    );
  } else if (e.times.length < EVICTION_ALARM_COUNT) {
    e.alarmed = false;
  }
}


export function registerConnectorRoutes(app) {
  // Agent → cloud: fetch the account's command-signing public key so the agent
  // can pin it.
  //
  // The equivalent route in server.js is session-authenticated and exists for
  // the web UI. An agent has a connector token and no session, so it could not
  // use it, which meant trust-on-first-use had no way to actually fetch the
  // key and every operator had to hand-copy a PEM block. Almost nobody did,
  // which is most of why signing was off everywhere.
  //
  // Handing the key to a valid connector token is not a disclosure: it is the
  // public half, and the token holder is the party the key authenticates TO.
  // The fingerprint is returned alongside so the operator can confirm the pin
  // out of band against the web UI, which is what closes the window where an
  // attacker positioned on the network during pairing could serve their own key.
  app.get('/api/connector/signing-pubkey', async (req, reply) => {
    const conn = await getConnectorByToken(bearer(req));
    if (!conn) return reply.code(401).send({ error: 'invalid connector token' });
    if (!(await ensureClientAuth(conn, req))) return reply.code(401).send({ error: 'client key authentication failed' });
    await ensureKeyPair(conn.user_id);
    const k = await getSigningPublic(conn.user_id);
    if (!k?.public_pem) return reply.code(503).send({ error: 'no signing key available for this account' });
    return { public_pem: k.public_pem, fingerprint: fingerprint(k.public_pem) };
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
    const identity = parseClientIdentity(req);
    evictPrevious(prev, conn, {
      name: (req.query?.name || conn.name || 'connector').toString(),
      transport: 'sse',
      ip: req.ip,
      identity
    });
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
    streams.set(conn.id, {
      raw, userId: conn.user_id, connectorId: conn.id, name, lastSeen: Date.now(), heartbeat,
      transport: 'sse', identity,
      send: (obj) => raw.write(`data: ${JSON.stringify(obj)}\n\n`),
      sendData: null, // SSE has no binary channel; falls back to base64 JSON
      closeSession: () => { try { raw.end(); } catch { /* already gone */ } }
    });
    dbg('connector', 'stream CONNECTED', { connectorId: conn.id, userId: conn.user_id, name, keyed: !!conn.client_public_pem });
    announceOnline(conn.id, conn.user_id);
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

  // Shared inbound handling for both transports: an agent message is the same
  // shape whether it arrived as a POST body or a WebSocket text frame.
  function handleAgentMessage(connId, body) {
    const { id, event } = body || {};
    if (event) {
      const t = id && tcpStreams.get(id);
      if (t) {
        if (event === 'open') t.emitter.emit('open');
        else if (event === 'data') t.emitter.emit('data', Buffer.from(body.data || '', 'base64'));
        else if (event === 'close') { t.emitter.emit('close', body.error || null); tcpStreams.delete(id); }
      }
      return;
    }
    const p = id && pending.get(id);
    if (p) { clearTimeout(p.timer); pending.delete(id); dbg('connector', 'result <- connector', { id, connectorId: connId, status: body.status, error: body.error }); p.resolve(body); }
  }

  // Agent → cloud: multiplexed WebSocket tunnel. Same auth as the SSE stream.
  //
  // This exists because the SSE + POST-per-result pairing had two costs that
  // showed up as "the connector keeps dropping": every upstream message opened a
  // fresh TCP+TLS connection, and every byte of printer traffic — MQTT, FTP,
  // camera frames — was base64'd into one ordered event stream, so a large
  // transfer stalled small status polls behind it until the UI's own timeout
  // fired and declared the printer offline.
  //
  // Here, control messages are JSON text frames and bulk TCP payloads are binary
  // frames chunked to 16KB, so transfers interleave with polls, nothing is
  // base64'd, and the connection is reused.
  //
  // Frame layout (binary): [uint8 type][uint32be streamIndex][payload]
  //   type 1 = TCP data
  // Stream indexes are per-session; the canonical id stays the UUID so the SSE
  // path and this one share all the logic above.
  app.get('/api/connector/ws', {
    websocket: true,
    // Authenticate BEFORE the upgrade. Checking inside the handler meant an
    // unauthenticated caller completed a full websocket handshake and only then
    // got closed — wasted work on our side and inconsistent with the SSE route,
    // which answers a plain 401. Rejecting here keeps both transports honest.
    preValidation: async (req, reply) => {
      // A rejected upgrade gives the client no response body to read — ws.onerror
      // fires with nothing useful — so the reason has to be logged here or it is
      // lost to both sides. Without this a connector silently drops to the slow
      // transport and the only symptom is printers flapping offline.
      const tok = bearer(req);
      const conn = await getConnectorByToken(tok);
      if (!conn) {
        dbg('connector', 'ws REJECT: no connector for token', { tokenLen: (tok || '').length, hasAuthHeader: !!req.headers['authorization'] });
        return reply.code(401).send({ error: 'invalid connector token' });
      }
      if (!(await ensureClientAuth(conn, req))) {
        dbg('connector', 'ws REJECT: client key auth failed', {
          connectorId: conn.id,
          keyed: !!conn.client_public_pem,
          sentTs: !!req.headers['x-ophq-client-ts'],
          sentSig: !!req.headers['x-ophq-client-sig'],
          sentPubkey: !!req.headers['x-ophq-client-pubkey'],
          skewMs: req.headers['x-ophq-client-ts'] ? Math.abs(Date.now() - Number(req.headers['x-ophq-client-ts'])) : null
        });
        return reply.code(401).send({ error: 'client key authentication failed — this connector is locked to a different client key; reset it in Settings → Connectors to pair a new client' });
      }
      req.ophqConnector = conn;
    }
  }, async (socket, req) => {
    const conn = req.ophqConnector;
    if (!conn) { try { socket.close(4401, 'invalid connector token'); } catch { /* */ } return; }
    const prev = streams.get(conn.id);
    const identity = parseClientIdentity(req);
    evictPrevious(prev, conn, {
      name: (req.query?.name || conn.name || 'connector').toString(),
      transport: 'ws',
      ip: req.ip,
      identity
    });

    const name = (req.query?.name || conn.name || 'connector').toString();
    const hostCidr = (req.query?.host_cidr || '').toString().slice(0, 64);
    if (hostCidr) { setConnectorHostCidr(conn.id, hostCidr).catch(() => {}); }

    // Per-session UUID <-> compact index mapping for binary frames.
    let nextIdx = 1;
    const idxByUuid = new Map();
    const uuidByIdx = new Map();
    const idxFor = (uuid) => {
      let i = idxByUuid.get(uuid);
      if (i === undefined) { i = nextIdx++; idxByUuid.set(uuid, i); uuidByIdx.set(i, uuid); }
      return i;
    };

    const heartbeat = setInterval(() => { try { socket.ping(); } catch { /* closed */ } }, 20000);
    streams.set(conn.id, {
      raw: null, userId: conn.user_id, connectorId: conn.id, name, lastSeen: Date.now(), heartbeat,
      transport: 'ws', identity,
      send: (obj) => socket.send(JSON.stringify(obj)),
      sendData: (uuid, chunk) => {
        const head = Buffer.alloc(5);
        head.writeUInt8(1, 0);
        head.writeUInt32BE(idxFor(uuid), 1);
        socket.send(Buffer.concat([head, Buffer.from(chunk)]), { binary: true });
      },
      // Lets openTcpStream stamp the compact index on the open job, so the agent
      // can send binary frames upstream for the same stream.
      idxFor,
      closeSession: () => { try { socket.close(1000, 'replaced by a newer session'); } catch { /* */ } }
    });
    dbg('connector', 'ws CONNECTED', { connectorId: conn.id, userId: conn.user_id, name, keyed: !!conn.client_public_pem });
    touchConnector(conn.id).catch(() => {});
    announceOnline(conn.id, conn.user_id);

    socket.on('message', (raw, isBinary) => {
      touchConnector(conn.id).catch(() => {});
      try {
        if (isBinary) {
          const buf = Buffer.from(raw);
          if (buf.length < 5) return;
          const type = buf.readUInt8(0);
          const uuid = uuidByIdx.get(buf.readUInt32BE(1));
          if (type !== 1 || !uuid) return;
          const t = tcpStreams.get(uuid);
          if (t) t.emitter.emit('data', buf.subarray(5));
          return;
        }
        handleAgentMessage(conn.id, JSON.parse(raw.toString()));
      } catch (e) { dbg('connector', 'ws message error', { connectorId: conn.id, error: e.message }); }
    });

    const cleanup = () => {
      clearInterval(heartbeat);
      const cur = streams.get(conn.id);
      if (cur && cur.transport === 'ws' && cur.send && cur.name === name) streams.delete(conn.id);
      dbg('connector', 'ws CLOSED', { connectorId: conn.id, userId: conn.user_id });
      for (const [sid, t] of tcpStreams) if (t.connectorId === conn.id) { t.emitter.emit('close', 'connector disconnected'); tcpStreams.delete(sid); }
    };
    socket.on('close', cleanup);
    socket.on('error', cleanup);
  });

  // Agent → cloud: the result of a job it performed on the LAN. Handles both
  // one-shot HTTP jobs (resolve a pending promise) and raw-TCP stream events.
  app.post('/api/connector/result', async (req, reply) => {
    const conn = await getConnectorByToken(bearer(req));
    if (!conn) return reply.code(401).send({ error: 'invalid connector token' });
    touchConnector(conn.id).catch(() => {});
    handleAgentMessage(conn.id, req.body || {});
    return { ok: true };
  });
}
