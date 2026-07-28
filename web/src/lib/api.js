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
  // Per-user Look & Feel (theme mode, colour overrides, text scale, a11y, branding).
  appearance: () => req('/appearance'),
  saveAppearance: (config) => req('/appearance', { method: 'PUT', body: JSON.stringify(config) }),
  // Public SITE branding (owner's branding) for the logged-out landing page.
  pubBranding: () => req('/pub/branding'),
  myInstance: () => req('/instance'),
  provision: () => req('/instance/provision', { method: 'POST' }),
  claim: (code) => req('/instance/claim', { method: 'POST', body: JSON.stringify({ code }) }),
  stats: () => req('/instance/stats'),
  // Public signup (invite code -> Authentik user + instance).
  signupInfo: () => req('/pub/signup-info'),
  signup: (body) => req('/pub/signup', { method: 'POST', body: JSON.stringify(body) }),
  // Owner-only admin (invites, users, instances, usage).
  adminSummary: () => req('/admin/summary'),
  adminInvites: () => req('/admin/invites'),
  createInvite: (body) => req('/admin/invites', { method: 'POST', body: JSON.stringify(body || {}) }),
  revokeInvite: (code) => req('/admin/invites/' + encodeURIComponent(code), { method: 'DELETE' }),
  adminUsers: () => req('/admin/users'),
  adminInstances: () => req('/admin/instances'),
  adminProvision: (body) => req('/admin/instances', { method: 'POST', body: JSON.stringify(body) }),
  adminFeatures: () => req('/admin/features'),
  setInstanceFeature: (id, key, enabled) => req('/admin/instances/' + id + '/features', { method: 'PUT', body: JSON.stringify({ key, enabled }) }),
  setInstanceQuota: (id, quotaMb) => req('/admin/instances/' + id + '/quota', { method: 'PUT', body: JSON.stringify({ quotaMb }) }),
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
  // ---- OrcaSlicer printer catalog (control-command framework import mechanism) ----
  // Lets the add-printer flow offer every OrcaSlicer-supported model + its comm
  // mechanism + capability flags. See db/control-framework/.
  printerCatalog: (params = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
    return req('/engine/api/v1/printers/catalog' + (q.toString() ? '?' + q : ''));
  },
  printerCatalogMechanisms: () => req('/engine/api/v1/printers/catalog/mechanisms'),
  printerCatalogCommands: (catalogId) => req('/engine/api/v1/printers/catalog/' + catalogId + '/commands'),
  printer: (id) => req('/engine/api/v1/printers/' + id),
  // Update a printer record (name, ip_address, mac_address, chamber_heater,
  // show_filament_panel, …). Changing ip_address re-links + reconnects engine-side.
  updatePrinter: (id, body) =>
    req('/engine/api/v1/printers/' + id, { method: 'PATCH', body: JSON.stringify(body) }),
  // Delete a printer from the dashboard. delete_archives=false PRESERVES print
  // archives/history (they're orphaned, not deleted); the engine also un-assigns
  // any queued jobs (printer_id→null) rather than cascade-deleting them.
  deletePrinter: (id) =>
    req('/engine/api/v1/printers/' + id + '?delete_archives=false', { method: 'DELETE' }),
  printerStatus: (id) => req('/engine/api/v1/printers/' + id + '/status'),
  // action: 'print/pause' | 'print/resume' | 'print/stop' | 'connect' | 'disconnect' | 'refresh-status'
  printerAction: (id, action) =>
    req('/engine/api/v1/printers/' + id + '/' + action, { method: 'POST' }),
  // ---- local connectors: outbound tunnel for LAN printers (#28/#29) ----
  connectors: () => req('/connectors'),
  createConnector: (name) => req('/connectors', { method: 'POST', body: JSON.stringify({ name }) }),
  deleteConnector: (id) => req('/connectors/' + id, { method: 'DELETE' }),
  // register (or clear) a connector's own public key for mutual auth
  setConnectorClientKey: (id, client_public_key) => req('/connectors/' + id, { method: 'PATCH', body: JSON.stringify({ client_public_key }) }),
  testConnector: (body) => req('/connectors/test', { method: 'POST', body: JSON.stringify(body) }),
  // command-signing key pair (RSA-2048): control-plane holds the private key,
  // connector holds the public key and verifies every command.
  signingKey: () => req('/connector/signing-key'),
  generateSigningKey: () => req('/connector/signing-key', { method: 'POST' }),
  deleteSigningKey: () => req('/connector/signing-key', { method: 'DELETE' }),
  // ---- printer automation: bed ejection / continuous printing (#20) ----
  printerAutomation: () => req('/printer-automation'),
  savePrinterAutomation: (map) => req('/printer-automation', { method: 'PUT', body: JSON.stringify(map) }),
  // ---- raw g-code console / macros (#23) + Klipper tuning (#24) ----
  sendGcode: (id, command) =>
    req('/engine/api/v1/printers/' + id + '/gcode', { method: 'POST', body: JSON.stringify({ command }) }),
  klipperLevel: (id) => req('/engine/api/v1/printers/' + id + '/klipper/level', { method: 'POST' }),
  klipperEmergencyStop: (id) => req('/engine/api/v1/printers/' + id + '/klipper/emergency-stop', { method: 'POST' }),
  // Realtime Klipper console: recent gcode responses (server.gcode_store) +
  // toolhead homed_axes. Read-only Moonraker passthrough; poll it for the console.
  klipperConsole: (id, count = 120) => req('/engine/api/v1/printers/' + id + '/klipper/console?count=' + count),
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
  // BOM / bill of materials (#22)
  projectBom: (id) => req('/engine/api/v1/projects/' + id + '/bom'),
  addBomItem: (id, body) => req('/engine/api/v1/projects/' + id + '/bom', { method: 'POST', body: JSON.stringify(body) }),
  updateBomItem: (id, itemId, body) => req('/engine/api/v1/projects/' + id + '/bom/' + itemId, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteBomItem: (id, itemId) => req('/engine/api/v1/projects/' + id + '/bom/' + itemId, { method: 'DELETE' }),
  // ---- inventory (spools) ----
  spoolsInv: () => req('/engine/api/v1/inventory/spools'),
  spoolLocations: () => req('/engine/api/v1/inventory/locations'),
  addSpool: (body) => req('/engine/api/v1/inventory/spools', { method: 'POST', body: JSON.stringify(body) }),
  addSpoolsBulk: (spool, quantity) => req('/engine/api/v1/inventory/spools/bulk', { method: 'POST', body: JSON.stringify({ spool, quantity }) }),
  updateSpool: (id, body) => req('/engine/api/v1/inventory/spools/' + id, { method: 'PATCH', body: JSON.stringify(body) }),
  archiveSpool: (id) => req('/engine/api/v1/inventory/spools/' + id + '/archive', { method: 'POST' }),
  // ---- cloud (Bambu / OrcaSlicer cloud presets) ----
  cloudStatus: () => req('/engine/api/v1/cloud/status'),
  // ---- API keys (#15): scoped keys for the /webhook/* automation endpoints ----
  apiKeys: () => req('/engine/api/v1/api-keys/'),
  createApiKey: (body) => req('/engine/api/v1/api-keys/', { method: 'POST', body: JSON.stringify(body) }),
  updateApiKey: (id, body) => req('/engine/api/v1/api-keys/' + id, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteApiKey: (id) => req('/engine/api/v1/api-keys/' + id, { method: 'DELETE' }),
  // ---- integrations (Home Assistant / Homepage / Prometheus) ----
  integrationToken: () => req('/integration-token'),
  regenIntegrationToken: () => req('/integration-token/regenerate', { method: 'POST' }),
  // ---- archives / timelapses ----
  archives: (limit = 60) => req('/engine/api/v1/archives/?limit=' + limit),
  archiveThumbUrl: (id) => base + '/engine/api/v1/archives/' + id + '/thumbnail',
  archiveTimelapseUrl: (id) => base + '/engine/api/v1/archives/' + id + '/timelapse',
  // ---- Obico AI failure detection ----
  obicoStatus: () => req('/engine/api/v1/obico/status'),
  obicoTest: () => req('/engine/api/v1/obico/test-connection', { method: 'POST' }),
  // ---- maintenance ----
  maintenancePrinter: (pid) => req('/engine/api/v1/maintenance/printers/' + pid),
  maintenanceOverview: () => req('/engine/api/v1/maintenance/overview'),
  maintenancePerform: (itemId, body) => req('/engine/api/v1/maintenance/items/' + itemId + '/perform', { method: 'POST', body: JSON.stringify(body || {}) }),
  maintenanceTypes: () => req('/engine/api/v1/maintenance/types'),
  maintenanceAssign: (pid, typeId) => req('/engine/api/v1/maintenance/printers/' + pid + '/assign/' + typeId, { method: 'POST' }),
  // ---- notification channels ----
  notifProviders: () => req('/engine/api/v1/notifications/'),
  notifCreate: (body) => req('/engine/api/v1/notifications/', { method: 'POST', body: JSON.stringify(body) }),
  notifUpdate: (id, body) => req('/engine/api/v1/notifications/' + id, { method: 'PATCH', body: JSON.stringify(body) }),
  notifDelete: (id) => req('/engine/api/v1/notifications/' + id, { method: 'DELETE' }),
  notifTest: (id) => req('/engine/api/v1/notifications/' + id + '/test', { method: 'POST' }),
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
  // ---- motion / hardware control (Bambu + Klipper via gcode) ----
  xyJog: (id, x, y) => req('/engine/api/v1/printers/' + id + '/xy-jog?x=' + x + '&y=' + y, { method: 'POST' }),
  bedJog: (id, distance) => req('/engine/api/v1/printers/' + id + '/bed-jog?distance=' + distance, { method: 'POST' }),
  extruderJog: (id, distance) => req('/engine/api/v1/printers/' + id + '/extruder-jog?distance=' + distance, { method: 'POST' }),
  homeAxes: (id, axes) => req('/engine/api/v1/printers/' + id + '/home-axes?axes=' + encodeURIComponent(axes), { method: 'POST' }),
  selectExtruder: (id, extruder) => req('/engine/api/v1/printers/' + id + '/select-extruder?extruder=' + extruder, { method: 'POST' }),
  fanSpeed: (id, fan, speed) => req('/engine/api/v1/printers/' + id + '/fan-speed?fan=' + fan + '&speed=' + speed, { method: 'POST' }),
  chamberLight: (id, on) => req('/engine/api/v1/printers/' + id + '/chamber-light?on=' + (on ? 'true' : 'false'), { method: 'POST' }),
  // Print speed mode: 1=silent, 2=standard, 3=sport, 4=ludicrous.
  printSpeed: (id, mode) => req('/engine/api/v1/printers/' + id + '/print-speed?mode=' + mode, { method: 'POST' }),
  // Re-read an AMS slot's RFID (filament reread).
  amsSlotRefresh: (id, amsId, slotId) =>
    req('/engine/api/v1/printers/' + id + '/ams/' + amsId + '/slot/' + slotId + '/refresh', { method: 'POST' }),
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
  // ---- library file preview (#17): thumbnails, 3MF plates, raw g-code ----
  fileThumbUrl: (id) => base + '/engine/api/v1/library/files/' + id + '/thumbnail',
  filePlates: (id) => req('/engine/api/v1/library/files/' + id + '/plates'),
  filePlateThumbUrl: (id, idx) => base + '/engine/api/v1/library/files/' + id + '/plate-thumbnail/' + idx,
  fileGcode: (id) => req('/engine/api/v1/library/files/' + id + '/gcode'),
  fileDownloadUrl: (id) => base + '/engine/api/v1/library/files/' + id + '/download',
  spools: () => req('/engine/api/v1/inventory/spools'),
  printStats: () => req('/engine/api/v1/archives/stats'),
  printLog: (limit = 25) => req('/engine/api/v1/print-log/?limit=' + limit),
  // Filtered print-log for reports (#25): {date_from,date_to,printer_id,status,limit,offset}
  printLogQuery: (params = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
    return req('/engine/api/v1/print-log/?' + q.toString());
  },
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
  // Acknowledge the build plate is cleared after a finished/failed/stopped print
  // (resets the printer to "Ready to print"; lets the queue start the next job).
  clearPlate: (id) => req('/engine/api/v1/printers/' + id + '/clear-plate', { method: 'POST' }),
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
