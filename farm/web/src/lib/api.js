// OpenPrintHQ browser API client
// SPDX-License-Identifier: AGPL-3.0-or-later
// Talks to the control-plane (via the /api proxy) for account + instance data,
// and — once provisioned — to the user's own engine instance.

const base = '/api';

async function req(path, opts = {}) {
  const res = await fetch(base + path, {
    headers: { 'content-type': 'application/json', ...(opts.headers || {}) },
    credentials: 'include',
    ...opts
  });
  if (!res.ok) {
    let detail;
    try { detail = await res.json(); } catch { detail = { error: res.statusText }; }
    throw Object.assign(new Error(detail.error || 'request failed'), { status: res.status, detail });
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

export const api = {
  health: () => req('/health'),
  me: () => req('/me'),
  myInstance: () => req('/instance'),
  provision: () => req('/instance/provision', { method: 'POST' }),
  stats: () => req('/instance/stats'),
  // Proxy straight to the logged-in user's engine (frontend-first model).
  engine: (path, opts) => req('/engine' + path, opts),
  printers: () => req('/engine/api/v1/printers/'),
  queue: () => req('/engine/api/v1/queue/')
};
