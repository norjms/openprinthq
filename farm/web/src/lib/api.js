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
  queue: () => req('/engine/api/v1/queue/'),
  files: () => req('/engine/api/v1/library/files'),
  spools: () => req('/engine/api/v1/inventory/spools'),
  printStats: () => req('/engine/api/v1/archives/stats'),
  slicerModels: () => req('/engine/api/v1/slicer/printer-models'),
  slicerPresets: () => req('/engine/api/v1/slicer/presets'),
  slice: (fileId, body) =>
    req('/engine/api/v1/library/files/' + fileId + '/slice', { method: 'POST', body: JSON.stringify(body) }),
  // Multipart upload straight through the gateway to the user's engine library.
  async uploadFile(file) {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch(base + '/engine/api/v1/library/files', {
      method: 'POST', body: fd, credentials: 'include'
    });
    if (!res.ok) {
      let d; try { d = await res.json(); } catch { d = { error: res.statusText }; }
      throw Object.assign(new Error(d.error || d.detail || 'upload failed'), { status: res.status });
    }
    return res.json().catch(() => ({}));
  }
};
