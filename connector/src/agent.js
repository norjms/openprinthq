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

const CONFIG = {
  controlUrl: (process.env.OPHQ_CONTROL_URL || '').replace(/\/+$/, ''),
  token: process.env.OPHQ_CONNECTOR_TOKEN || '',
  // Allow-list: comma-separated hosts, CIDRs, or the keyword "private".
  allow: (process.env.OPHQ_ALLOW || 'private').split(',').map((s) => s.trim()).filter(Boolean),
  // Allowed destination ports (printer APIs, cameras). "*" allows any.
  allowPorts: (process.env.OPHQ_ALLOW_PORTS || '80,443,7125,8080,8081,8888,3000,1883,8883,990,21').split(',').map((s) => s.trim()),
  name: process.env.OPHQ_CONNECTOR_NAME || 'connector',
  reconnectMinMs: 2000,
  reconnectMaxMs: 30000,
  requestTimeoutMs: Number(process.env.OPHQ_REQUEST_TIMEOUT_MS || 20000)
};

function log(...a) { console.log(new Date().toISOString(), '[connector]', ...a); }
function fail(msg) { console.error('FATAL:', msg); process.exit(1); }

if (!CONFIG.controlUrl) fail('OPHQ_CONTROL_URL is required (e.g. https://openprinthq.example.org)');
if (!CONFIG.token) fail('OPHQ_CONNECTOR_TOKEN is required (create one in Settings → Connectors)');

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

async function handleJob(job) {
  let result;
  if (job.kind === 'tcp-probe') result = await runTcpProbe(job);
  else result = await runHttpJob(job);
  try {
    await fetch(`${CONFIG.controlUrl}/api/connector/result`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${CONFIG.token}` },
      body: JSON.stringify({ id: job.id, ...result })
    });
  } catch (e) { log('failed to post result for job', job.id, e?.message); }
}

// ---- SSE stream consumer -------------------------------------------------
async function connectOnce() {
  const url = `${CONFIG.controlUrl}/api/connector/stream?name=${encodeURIComponent(CONFIG.name)}`;
  const res = await fetch(url, {
    headers: { authorization: `Bearer ${CONFIG.token}`, accept: 'text/event-stream' }
  });
  if (res.status === 401 || res.status === 403) fail(`control-plane rejected the connector token (${res.status})`);
  if (!res.ok || !res.body) throw new Error(`stream failed: HTTP ${res.status}`);
  log(`connected to ${CONFIG.controlUrl} as "${CONFIG.name}" — waiting for jobs`);

  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) throw new Error('stream closed by server');
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
      const line = raw.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;                                   // heartbeat comment ": ping"
      const payload = line.slice(5).trim();
      if (!payload) continue;
      let job; try { job = JSON.parse(payload); } catch { continue; }
      if (job && job.id && (job.host || job.kind)) handleJob(job);  // fire-and-forget
    }
  }
}

async function main() {
  log(`starting — control=${CONFIG.controlUrl} allow=[${CONFIG.allow.join(',')}] ports=[${CONFIG.allowPorts.join(',')}]`);
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
