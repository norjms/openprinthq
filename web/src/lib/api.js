// OpenPrintHQ browser API client
// SPDX-License-Identifier: AGPL-3.0-or-later
// Talks to the control-plane (via the /api proxy) for account + instance data,
// and — once provisioned — to the user's own engine instance.

const base = '/api';

async function req(path, opts = {}) {
  const { headers: optHeaders, ...rest } = opts;
  const headers = { ...(optHeaders || {}) };
  // Only advertise a JSON body when there actually is one. A bodyless
  // POST/DELETE that still sends `content-type: application/json` makes the
  // Fastify gateway reject it with 400 ("body cannot be empty"), which broke
  // every no-body action (queue start/cancel/stop/delete, printer controls).
  if (rest.body !== undefined && !('content-type' in headers)) {
    headers['content-type'] = 'application/json';
  }
  const res = await fetch(base + path, {
    headers,
    credentials: 'include',
    ...rest
  });
  if (!res.ok) {
    let detail;
    try { detail = await res.json(); } catch { detail = null; }
    // Engine/gateway errors come in several shapes: a plain string, a FastAPI
    // validation array, or a structured object like {detail:{code,message}}.
    // Flatten to a human message so callers can just use e.message (and never
    // render "[object Object]").
    const d = detail && (detail.detail ?? detail.error ?? detail.message);
    let msg;
    if (typeof d === 'string') msg = d;
    else if (Array.isArray(d)) msg = d.map((x) => x?.msg || (typeof x === 'string' ? x : '')).filter(Boolean).join('; ');
    else if (d && typeof d === 'object') msg = d.message || d.detail || d.msg;
    msg = msg || (typeof detail?.error === 'string' ? detail.error : '') || res.statusText || 'request failed';
    throw Object.assign(new Error(msg), { status: res.status, detail });
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
  // Bambu discovery (Docker path = per-host unicast SSDP subnet scan).
  discoverScan: (subnet, timeout = 1.5) =>
    req('/engine/api/v1/discovery/scan', { method: 'POST', body: JSON.stringify({ subnet, timeout }) }),
  discoverScanStatus: () => req('/engine/api/v1/discovery/scan/status'),
  discoveredPrinters: () => req('/engine/api/v1/discovery/printers'),
  printer: (id) => req('/engine/api/v1/printers/' + id),
  printerStatus: (id) => req('/engine/api/v1/printers/' + id + '/status'),
  // action: 'print/pause' | 'print/resume' | 'print/stop' | 'connect' | 'disconnect' | 'refresh-status'
  printerAction: (id, action) =>
    req('/engine/api/v1/printers/' + id + '/' + action, { method: 'POST' }),
  // kind: 'nozzle' | 'bed' | 'chamber' — engine takes ?target= as a query param
  setTemp: (id, kind, target) =>
    req('/engine/api/v1/printers/' + id + '/temperature/' + kind + '?target=' + encodeURIComponent(target), { method: 'POST' }),
  queue: () => req('/engine/api/v1/queue/'),
  queueUpdate: (id, body) =>
    req('/engine/api/v1/queue/' + id, { method: 'PATCH', body: JSON.stringify(body) }),
  queueDelete: (id) => req('/engine/api/v1/queue/' + id, { method: 'DELETE' }),
  queueStart: (id) => req('/engine/api/v1/queue/' + id + '/start', { method: 'POST' }),
  queueCancel: (id) => req('/engine/api/v1/queue/' + id + '/cancel', { method: 'POST' }),
  queueStop: (id) => req('/engine/api/v1/queue/' + id + '/stop', { method: 'POST' }),
  queueReorder: (items) =>
    req('/engine/api/v1/queue/reorder', { method: 'POST', body: JSON.stringify({ items }) }),
  files: () => req('/engine/api/v1/library/files'),
  spools: () => req('/engine/api/v1/inventory/spools'),
  printStats: () => req('/engine/api/v1/archives/stats'),
  slicerModels: () => req('/engine/api/v1/slicer/printer-models'),
  slicerPresets: () => req('/engine/api/v1/slicer/presets'),
  // Compatible process/filament preset names for a printer (control-plane join).
  compatiblePresets: (printer) => req('/slicer/compatible?printer=' + encodeURIComponent(printer)),
  slice: (fileId, body) =>
    req('/engine/api/v1/library/files/' + fileId + '/slice', { method: 'POST', body: JSON.stringify(body) }),
  sliceJob: (jobId) => req('/engine/api/v1/slice-jobs/' + jobId),
  // Add sliced library files (.gcode / .gcode.3mf) to the print queue as
  // unassigned items — the engine scheduler dispatches to a compatible printer.
  addToQueue: (fileIds) =>
    req('/engine/api/v1/library/files/add-to-queue', {
      method: 'POST', body: JSON.stringify({ file_ids: fileIds })
    }),
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
