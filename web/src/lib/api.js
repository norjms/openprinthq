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
  // Klipper/Moonraker discovery (subnet probe of port 7125).
  discoverKlipperScan: (subnet, timeout = 1.5) =>
    req('/engine/api/v1/discovery/klipper/scan', { method: 'POST', body: JSON.stringify({ subnet, timeout }) }),
  discoverKlipperScanStatus: () => req('/engine/api/v1/discovery/klipper/scan/status'),
  discoveredKlipperPrinters: () => req('/engine/api/v1/discovery/klipper/printers'),
  printer: (id) => req('/engine/api/v1/printers/' + id),
  printerStatus: (id) => req('/engine/api/v1/printers/' + id + '/status'),
  // action: 'print/pause' | 'print/resume' | 'print/stop' | 'connect' | 'disconnect' | 'refresh-status'
  printerAction: (id, action) =>
    req('/engine/api/v1/printers/' + id + '/' + action, { method: 'POST' }),
  // ---- fleet firmware (transport-aware: Bambu wiki / Klipper Moonraker) ----
  firmwareUpdates: () => req('/engine/api/v1/firmware/updates'),
  // ---- failure analysis ----
  failureAnalysis: (days = 30) => req('/engine/api/v1/archives/analysis/failures?period_days=' + days),
  // ---- projects ----
  projects: () => req('/engine/api/v1/projects/'),
  project: (id) => req('/engine/api/v1/projects/' + id),
  createProject: (body) => req('/engine/api/v1/projects/', { method: 'POST', body: JSON.stringify(body) }),
  projectArchives: (id) => req('/engine/api/v1/projects/' + id + '/archives'),
  projectQueue: (id) => req('/engine/api/v1/projects/' + id + '/queue'),
  // ---- inventory (spools) ----
  spoolsInv: () => req('/engine/api/v1/inventory/spools'),
  spoolLocations: () => req('/engine/api/v1/inventory/locations'),
  addSpool: (body) => req('/engine/api/v1/inventory/spools', { method: 'POST', body: JSON.stringify(body) }),
  archiveSpool: (id) => req('/engine/api/v1/inventory/spools/' + id + '/archive', { method: 'POST' }),
  // ---- cloud (Bambu / OrcaSlicer cloud presets) ----
  cloudStatus: () => req('/engine/api/v1/cloud/status'),
  // ---- integrations (Home Assistant / Homepage / Prometheus) ----
  integrationToken: () => req('/integration-token'),
  regenIntegrationToken: () => req('/integration-token/regenerate', { method: 'POST' }),
  // ---- smart plugs / power control + energy metering ----
  plugByPrinter: (pid) => req('/engine/api/v1/smart-plugs/by-printer/' + pid),
  plugStatus: (id) => req('/engine/api/v1/smart-plugs/' + id + '/status'),
  plugControl: (id, action) =>
    req('/engine/api/v1/smart-plugs/' + id + '/control', { method: 'POST', body: JSON.stringify({ action }) }),
  plugCreate: (body) => req('/engine/api/v1/smart-plugs/', { method: 'POST', body: JSON.stringify(body) }),
  plugUpdate: (id, body) => req('/engine/api/v1/smart-plugs/' + id, { method: 'PATCH', body: JSON.stringify(body) }),
  plugDelete: (id) => req('/engine/api/v1/smart-plugs/' + id, { method: 'DELETE' }),
  // ---- AMS filament backup (auto-switch to a backup spool on runout) ----
  amsBackup: (id, enabled) =>
    req('/engine/api/v1/printers/' + id + '/ams-backup?enabled=' + (enabled ? 'true' : 'false'), { method: 'POST' }),
  // ---- AMS filament drying (per AMS unit; ams_id required by the engine) ----
  dryingStart: (id, { ams_id, temp, duration, filament, rotate_tray }) =>
    req('/engine/api/v1/printers/' + id + '/drying/start?ams_id=' + ams_id + '&temp=' + temp + '&duration=' + duration +
        '&filament=' + encodeURIComponent(filament || '') + '&rotate_tray=' + (rotate_tray ? 'true' : 'false'),
      { method: 'POST' }),
  dryingStop: (id, ams_id) => req('/engine/api/v1/printers/' + id + '/drying/stop?ams_id=' + ams_id, { method: 'POST' }),
  // AMS filament load (tray_id: 0-15 AMS slot = ams*4+slot, 254 external) / unload.
  amsLoad: (id, trayId) =>
    req('/engine/api/v1/printers/' + id + '/ams/load?tray_id=' + encodeURIComponent(trayId), { method: 'POST' }),
  amsUnload: (id) =>
    req('/engine/api/v1/printers/' + id + '/ams/unload', { method: 'POST' }),
  // kind: 'nozzle' | 'bed' | 'chamber' — engine takes ?target= as a query param.
  // For dual-nozzle machines, nozzle index 0 = right/default, 1 = left.
  setTemp: (id, kind, target, nozzle) =>
    req('/engine/api/v1/printers/' + id + '/temperature/' + kind + '?target=' + encodeURIComponent(target) +
        (kind === 'nozzle' && nozzle ? '&nozzle=' + nozzle : ''), { method: 'POST' }),
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
  printLog: (limit = 25) => req('/engine/api/v1/print-log/?limit=' + limit),
  engineSettings: () => req('/engine/api/v1/settings'),
  updateEngineSettings: (body) => req('/engine/api/v1/settings', { method: 'PATCH', body: JSON.stringify(body) }),
  slicerModels: () => req('/engine/api/v1/slicer/printer-models'),
  slicerPresets: () => req('/engine/api/v1/slicer/presets'),
  // Compatible process/filament preset names for a printer (control-plane join).
  compatiblePresets: (printer) => req('/slicer/compatible?printer=' + encodeURIComponent(printer)),
  // Bambu HMS error dictionary (short_code -> description) for decoding alerts.
  hmsDescriptions: () => req('/hms/descriptions'),
  // Clear/acknowledge a printer's HMS flags (like dismissing on the printer screen).
  hmsClear: (id) => req('/engine/api/v1/printers/' + id + '/hms/clear', { method: 'POST' }),
  // Power circuits (printer_id -> circuit label) for staggered batch printing.
  circuits: () => req('/printer-circuits'),
  saveCircuits: (map) => req('/printer-circuits', { method: 'PUT', body: JSON.stringify(map) }),
  // Temperature-staggered batch printing.
  batchStart: (body) => req('/batch', { method: 'POST', body: JSON.stringify(body) }),
  batchActive: () => req('/batch/active'),
  batchAdvance: (id) => req('/batch/' + id + '/advance', { method: 'POST' }),
  batchCancel: (id) => req('/batch/' + id + '/cancel', { method: 'POST' }),
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
