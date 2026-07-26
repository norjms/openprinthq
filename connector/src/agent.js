// OpenPrintHQ Local Connector — agent
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Runs on the same LAN as your printers and gives cloud-hosted OpenPrintHQ a
// way to reach them WITHOUT any inbound port-forward — so it works behind a
// home router, a strict firewall, or carrier-grade NAT (CGNAT).
//
// How it works (outbound-only):
//   1. The agent opens a long-lived Server-Sent-Events stream *out* to the
//      control-plane:  GET /api/connector/stream   (Bearer <connector token>)
//      Because the connection is initiated from inside your network, no
//      inbound firewall rule / port-forward is ever needed.
//   2. The control-plane pushes "jobs" down that stream — each job is a single
//      HTTP request it wants performed against a printer on your LAN
//      (e.g. GET http://10.10.10.121:7125/printer/info on a Moonraker box).
//   3. The agent performs the request locally and POSTs the response back:
//      POST /api/connector/result
//
// Security: the agent will ONLY talk to hosts/ports on its allow-list (private
// ranges + printer ports by default). A compromised or malicious control-plane
// therefore cannot use the agent to reach arbitrary internet hosts (SSRF).
//
// Dependency-free: uses only Node ≥ 20 built-ins (global fetch, streams).

import net from 'node:net';
import crypto from 'node:crypto';
import fs from 'node:fs';

function readPubKey() {
  const inline = process.env.OPHQ_SIGNING_PUBKEY;
  if (inline && inline.trim()) return inline.trim();
  const file = process.env.OPHQ_SIGNING_PUBKEY_FILE;
  if (file) { try { return fs.readFileSync(file, 'utf8'); } catch { return ''; } }
  return '';
}

const CONFIG = {
  controlUrl: (process.env.OPHQ_CONTROL_URL || '').replace(/\/+$/, ''),
  token: process.env.OPHQ_CONNECTOR_TOKEN || '',
  // Allow-list: comma-separated hosts, CIDRs, or the keyword "private".
  allow: (process.env.OPHQ_ALLOW || 'private').split(',').map((s) => s.trim()).filter(Boolean),
  // Allowed destination ports (printer APIs, cameras). "*" allows any.
  allowPorts: (process.env.OPHQ_ALLOW_PORTS || '80,443,7125,8080,8081,8888,3000,1883,8883,990,21').split(',').map((s) => s.trim()),
  name: process.env.OPHQ_CONNECTOR_NAME || 'connector',
  // Optional RSA public key (PEM). When set, the agent verifies that every
  // command it receives is signed by the control-plane's matching private key.
  signPubKeyPem: readPubKey(),
  maxClockSkewMs: Number(process.env.OPHQ_MAX_CLOCK_SKEW_MS || 120000),
  // Keep-alive: if no bytes (control-plane sends a ':ping' comment ~every 20s)
  // arrive within this window, treat the tunnel as dead and reconnect. Catches
  // silent drop-outs (half-open TCP, NAT timeouts) that never send a FIN.
  streamTimeoutMs: Number(process.env.OPHQ_STREAM_TIMEOUT_MS || 60000),
  reconnectMinMs: 2000,
  reconnectMaxMs: 30000,
  requestTimeoutMs: Number(process.env.OPHQ_REQUEST_TIMEOUT_MS || 20000)
};

// Logs go to stderr so stdout stays clean (e.g. for `--pubkey`).
function log(...a) { console.error(new Date().toISOString(), '[connector]', ...a); }
function fail(msg) { console.error('FATAL:', msg); process.exit(1); }

// ---- command signature verification (optional but recommended) -----------
const PSS = { padding: crypto.constants.RSA_PKCS1_PSS_PADDING, saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST };
let signPubKey = null;
if (CONFIG.signPubKeyPem) {
  try { signPubKey = crypto.createPublicKey(CONFIG.signPubKeyPem); }
  catch { fail('OPHQ_SIGNING_PUBKEY is not a valid PEM public key'); }
}
const seenIds = new Set();   // replay defence (bounded)
function rememberId(id) { seenIds.add(id); if (seenIds.size > 5000) seenIds.delete(seenIds.values().next().value); }
function canonJob(j) {
  return Buffer.from(JSON.stringify([
    j.id ?? null, j.ts ?? null, j.kind ?? null,
    j.host ?? null, j.port ?? null, j.scheme ?? null,
    j.path ?? null, j.method ?? null, j.headers ?? null, j.body ?? null
  ]));
}
const isCommand = (j) => j.kind === undefined || j.kind === 'tcp-open' || j.kind === 'tcp-probe';
let warnedUnsigned = false;
function verifyCommand(job) {
  if (!signPubKey) {
    if (!warnedUnsigned) { warnedUnsigned = true; log('WARNING: OPHQ_SIGNING_PUBKEY not set — command signatures are NOT enforced. Set it for best security.'); }
    return true;
  }
  if (!isCommand(job)) return true;   // stream data/close ride an already-authenticated stream id
  if (!job.sig || !job.ts) { log('rejected unsigned command', job.id); return false; }
  if (Math.abs(Date.now() - Number(job.ts)) > CONFIG.maxClockSkewMs) { log('rejected stale command', job.id); return false; }
  if (seenIds.has(job.id)) { log('rejected replayed command', job.id); return false; }
  let ok = false;
  try { ok = crypto.verify('sha256', canonJob(job), { key: signPubKey, ...PSS }, Buffer.from(job.sig, 'base64')); }
  catch { ok = false; }
  if (!ok) { log('rejected bad-signature command', job.id); return false; }
  rememberId(job.id);
  return true;
}

// ---- mutual auth: this connector's own key pair --------------------------
// The connector proves itself to the control-plane (SSH-style): it holds a
// private key and you register its public key in Settings → Connectors. On
// every connect it signs `${token}.${ts}` so a leaked bearer token alone can't
// impersonate it. The key is persisted (OPHQ_CLIENT_KEY_FILE) so the public key
// stays stable across restarts.
function loadOrCreateClientKey() {
  const inline = process.env.OPHQ_CLIENT_PRIVKEY;
  if (inline && inline.trim()) return crypto.createPrivateKey(inline.trim());
  const file = process.env.OPHQ_CLIENT_KEY_FILE;
  if (!file) return null;
  try {
    if (fs.existsSync(file)) return crypto.createPrivateKey(fs.readFileSync(file, 'utf8'));
    const { privateKey } = crypto.generateKeyPairSync('rsa', {
      modulusLength: 2048,
      privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
      publicKeyEncoding: { type: 'spki', format: 'pem' }
    });
    fs.writeFileSync(file, privateKey, { mode: 0o600 });
    log(`generated a new connector key at ${file}`);
    return crypto.createPrivateKey(privateKey);
  } catch (e) { log('client key error:', e.message); return null; }
}
const clientKey = loadOrCreateClientKey();
const clientPubPem = clientKey ? crypto.createPublicKey(clientKey).export({ type: 'spki', format: 'pem' }).toString() : null;

// `node src/agent.js --pubkey` prints the public key to register, then exits.
if (process.argv.includes('--pubkey')) {
  if (!clientPubPem) fail('set OPHQ_CLIENT_KEY_FILE (or OPHQ_CLIENT_PRIVKEY) first, then re-run with --pubkey');
  process.stdout.write(clientPubPem);
  process.exit(0);
}

function clientAuthHeaders() {
  if (!clientKey) return {};
  const ts = String(Date.now());
  const sig = crypto.sign('sha256', Buffer.from(`${CONFIG.token}.${ts}`), { key: clientKey, ...PSS }).toString('base64');
  return { 'x-ophq-client-ts': ts, 'x-ophq-client-sig': sig };
}

// ---- allow-list ----------------------------------------------------------
function ipToInt(ip) {
  const p = ip.split('.').map(Number);
  if (p.length !== 4 || p.some((n) => Number.isNaN(n) || n < 0 || n > 255)) return null;
  return ((p[0] << 24) >>> 0) + (p[1] << 16) + (p[2] << 8) + p[3];
}
function inCidr(ip, cidr) {
  const [range, bitsRaw] = cidr.split('/');
  const bits = Number(bitsRaw);
  const a = ipToInt(ip), b = ipToInt(range);
  if (a == null || b == null || Number.isNaN(bits)) return false;
  const mask = bits === 0 ? 0 : (~0 << (32 - bits)) >>> 0;
  return (a & mask) === (b & mask);
}
const PRIVATE = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '169.254.0.0/16'];
function hostAllowed(host) {
  const h = String(host || '').toLowerCase();
  if (!h) return false;
  const isIp = ipToInt(h) != null;
  for (const rule of CONFIG.allow) {
    if (rule === 'private') { if (isIp && PRIVATE.some((c) => inCidr(h, c))) return true; continue; }
    if (rule.includes('/')) { if (isIp && inCidr(h, rule)) return true; continue; }
    if (rule === h) return true;                                   // exact host / IP
    if (rule.startsWith('*.') && h.endsWith(rule.slice(1))) return true; // wildcard domain
  }
  return false;
}
function portAllowed(port) {
  return CONFIG.allowPorts.includes('*') || CONFIG.allowPorts.includes(String(port));
}

// ---- perform one proxied job --------------------------------------------
async function runHttpJob(job) {
  const { host, port = 80, scheme = 'http', path = '/', method = 'GET', headers = {}, body } = job;
  if (!hostAllowed(host)) return { status: 403, error: `host ${host} not in allow-list` };
  if (!portAllowed(port)) return { status: 403, error: `port ${port} not in allow-list` };
  const url = `${scheme}://${host}:${port}${path.startsWith('/') ? path : '/' + path}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), CONFIG.requestTimeoutMs);
  try {
    const init = { method, headers: { ...headers }, signal: ctrl.signal };
    delete init.headers.host; delete init.headers.Host;
    if (body != null && method !== 'GET' && method !== 'HEAD') init.body = Buffer.from(body, 'base64');
    const res = await fetch(url, init);
    const buf = Buffer.from(await res.arrayBuffer());
    const outHeaders = {};
    res.headers.forEach((v, k) => { outHeaders[k] = v; });
    return { status: res.status, headers: outHeaders, body: buf.toString('base64') };
  } catch (e) {
    return { status: 502, error: (e && e.name === 'AbortError') ? 'timeout' : (e?.message || 'request failed') };
  } finally { clearTimeout(timer); }
}

// Optional raw TCP probe (used for connectivity checks, e.g. Bambu MQTT 8883).
async function runTcpProbe(job) {
  const { host, port } = job;
  if (!hostAllowed(host) || !portAllowed(port)) return { ok: false, error: 'not allowed' };
  return await new Promise((resolve) => {
    const sock = net.connect({ host, port, timeout: 4000 }, () => { sock.destroy(); resolve({ ok: true }); });
    sock.on('error', (e) => resolve({ ok: false, error: e.message }));
    sock.on('timeout', () => { sock.destroy(); resolve({ ok: false, error: 'timeout' }); });
  });
}

async function post(body) {
  try {
    await fetch(`${CONFIG.controlUrl}/api/connector/result`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${CONFIG.token}` },
      body: JSON.stringify(body)
    });
  } catch (e) { log('post failed', body?.id, e?.message); }
}

// ---- raw TCP tunnelling --------------------------------------------------
// Multiplexed byte streams keyed by stream id. Cloud→agent bytes arrive as
// `tcp-data` SSE events; agent→cloud bytes are POSTed as {event:'data'}. This
// carries any TCP protocol — Bambu MQTT (8883), FTP (990), etc.
const sockets = new Map();   // streamId -> net.Socket

function openTcp(job) {
  const { id, host, port } = job;
  if (!hostAllowed(host) || !portAllowed(port)) { post({ id, event: 'close', error: 'not allowed' }); return; }
  const sock = net.connect({ host, port });
  sockets.set(id, sock);
  sock.on('connect', () => post({ id, event: 'open' }));
  sock.on('data', (chunk) => post({ id, event: 'data', data: chunk.toString('base64') }));
  sock.on('error', (e) => { post({ id, event: 'close', error: e.message }); sockets.delete(id); });
  sock.on('close', () => { post({ id, event: 'close' }); sockets.delete(id); });
  sock.setTimeout(0);
}
function dataTcp(job) { const s = sockets.get(job.id); if (s && job.data) s.write(Buffer.from(job.data, 'base64')); }
function closeTcp(job) { const s = sockets.get(job.id); if (s) { s.end(); sockets.delete(job.id); } }

async function handleJob(job) {
  if (!verifyCommand(job)) return;   // drop commands not signed by the control-plane
  if (job.kind === 'tcp-open') return openTcp(job);
  if (job.kind === 'tcp-data') return dataTcp(job);
  if (job.kind === 'tcp-close') return closeTcp(job);
  const result = (job.kind === 'tcp-probe') ? await runTcpProbe(job) : await runHttpJob(job);
  await post({ id: job.id, ...result });
}

// ---- SSE stream consumer -------------------------------------------------
async function connectOnce() {
  const url = `${CONFIG.controlUrl}/api/connector/stream?name=${encodeURIComponent(CONFIG.name)}`;
  const ac = new AbortController();
  let idle = setTimeout(() => ac.abort(), CONFIG.streamTimeoutMs);
  const bump = () => { clearTimeout(idle); idle = setTimeout(() => ac.abort(), CONFIG.streamTimeoutMs); };
  let res;
  try {
    res = await fetch(url, {
      headers: { authorization: `Bearer ${CONFIG.token}`, accept: 'text/event-stream', ...clientAuthHeaders() },
      signal: ac.signal
    });
  } catch (e) { clearTimeout(idle); throw e; }
  if (res.status === 401 || res.status === 403) { clearTimeout(idle); fail(`control-plane rejected the connector (${res.status}). If mutual auth is enabled, register this connector's public key (run with --pubkey to print it).`); }
  if (!res.ok || !res.body) { clearTimeout(idle); throw new Error(`stream failed: HTTP ${res.status}`); }
  log(`connected to ${CONFIG.controlUrl} as "${CONFIG.name}" — waiting for jobs (keep-alive ${Math.round(CONFIG.streamTimeoutMs / 1000)}s)`);

  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) throw new Error('stream closed by server');
      bump();                                                // any byte (incl. ':ping') resets the watchdog
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const line = raw.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;                                 // heartbeat comment ": ping"
        const payload = line.slice(5).trim();
        if (!payload) continue;
        let job; try { job = JSON.parse(payload); } catch { continue; }
        if (job && job.id && (job.host || job.kind)) handleJob(job);  // fire-and-forget
      }
    }
  } finally { clearTimeout(idle); }
}

async function main() {
  if (!CONFIG.controlUrl) fail('OPHQ_CONTROL_URL is required (e.g. https://openprinthq.example.org)');
  if (!CONFIG.token) fail('OPHQ_CONNECTOR_TOKEN is required (create one in Settings → Connectors)');
  log(`starting — control=${CONFIG.controlUrl} allow=[${CONFIG.allow.join(',')}] ports=[${CONFIG.allowPorts.join(',')}] signature-verification=${signPubKey ? 'ENFORCED' : 'off'} client-key=${clientKey ? 'on' : 'off'}`);
  if (clientPubPem) log(`this connector's public key (register it in Settings → Connectors):\n${clientPubPem.trim()}`);
  let backoff = CONFIG.reconnectMinMs;
  for (;;) {
    try {
      await connectOnce();
      backoff = CONFIG.reconnectMinMs;
    } catch (e) {
      log('disconnected:', e?.message, `— retrying in ${Math.round(backoff / 1000)}s`);
      await new Promise((r) => setTimeout(r, backoff));
      backoff = Math.min(CONFIG.reconnectMaxMs, Math.round(backoff * 1.7));
    }
  }
}

process.on('SIGINT', () => { log('shutting down'); process.exit(0); });
process.on('SIGTERM', () => { log('shutting down'); process.exit(0); });
main();
