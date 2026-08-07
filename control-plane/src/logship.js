// Log shipping for the control-plane, in two deliberately separate scopes.
//
//   SERVER scope  - the application's own operational logs (startup, routing,
//                   connector transport, errors). Configured by the global
//                   admin. MUST NOT carry tenant or connector data: a platform
//                   operator watching server health has no business receiving
//                   another party's printer activity as a side effect.
//
//   TENANT scope  - one instance's own logs, configured by that tenant in their
//                   own settings and shipped only to the destination they gave.
//
// Both are opt-in with no fallback endpoint. Loki push or RFC5424 syslog,
// chosen by URL scheme.
import dgram from 'node:dgram';

const FLUSH_MS = 5000;
const MAX_BATCH = 300;
const MAX_QUEUE = MAX_BATCH * 5;

const sinks = new Map(); // key -> { url, labels, queue }

export function setSink(key, url, labels = {}) {
  const u = String(url || '').trim();
  if (!u) { sinks.delete(key); return false; }
  const existing = sinks.get(key);
  sinks.set(key, { url: u, labels, queue: existing?.queue || [] });
  return true;
}

export function sinkConfigured(key) { return sinks.has(key); }

export function shipServer(line) { push('server', line); }
export function shipTenant(userId, line) { push(`tenant:${userId}`, line); }

function push(key, line) {
  const s = sinks.get(key);
  if (!s || !line) return;
  s.queue.push([String(Date.now() * 1e6), String(line)]);
  // Bounded: a destination that is down must not turn into unbounded memory.
  if (s.queue.length > MAX_QUEUE) s.queue = s.queue.slice(-MAX_QUEUE);
}

async function flushAll() {
  for (const [, s] of sinks) {
    if (!s.queue.length) continue;
    const batch = s.queue.splice(0, MAX_BATCH);
    try {
      if (s.url.startsWith('syslog://')) sendSyslog(s, batch);
      else await sendLoki(s, batch);
    } catch { /* logging must never disturb the request path */ }
  }
}
setInterval(flushAll, FLUSH_MS).unref?.();

async function sendLoki(s, batch) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 8000);
  try {
    await fetch(s.url.replace(/\/$/, '') + '/loki/api/v1/push', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ streams: [{ stream: s.labels, values: batch }] }),
      signal: ctrl.signal
    });
  } finally { clearTimeout(t); }
}

function sendSyslog(s, batch) {
  const m = /^syslog:\/\/([^:/]+)(?::(\d+))?/.exec(s.url);
  if (!m) return;
  const [, host, port] = m;
  const sock = dgram.createSocket('udp4');
  const tag = String(s.labels.job || 'openprinthq').replace(/[^\w-]/g, '');
  let left = batch.length;
  const done = () => { if (--left <= 0) { try { sock.close(); } catch { /* */ } } };
  for (const [, line] of batch) {
    const msg = `<134>1 ${new Date().toISOString()} ${s.labels.host || 'openprinthq'} ${tag} ${process.pid} - - ${line}`;
    sock.send(Buffer.from(msg), Number(port) || 514, host, done);
  }
}
