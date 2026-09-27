// OpenPrintHQ control-plane - GenFilament proxy for the desktop tag writer
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The spool intake workflow is: name a brand and product, take the specs from
// GenFilament (a stored filament when we have one, an AI lookup when we do
// not), let the operator correct them, save them back so the next roll of the
// same product costs no lookup, then tag, label and stock the spool.
//
// GenFilament has no authentication of its own and is not on the public edge,
// so the desktop app never talks to it directly: these routes sit next to the
// other /printhost/tags endpoints, take the same access key, and forward to
// OPHQ_GENFILAMENT_API_URL. That also means the workflow works away from the
// LAN, which a direct connection to the service would not.

const LOOKUP_TIMEOUT_MS = Number(process.env.OPHQ_GENFILAMENT_LOOKUP_TIMEOUT_MS || 120000);
const CALL_TIMEOUT_MS = Number(process.env.OPHQ_GENFILAMENT_TIMEOUT_MS || 15000);

/// Compare brand/product names the way a person would: case and spacing blind.
export function sameName(a, b) {
  const norm = (s) => String(s ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
  return norm(a) === norm(b) && norm(a) !== '';
}

/// GenFilament's own FilamentSpecs field set, in its order, plus the identity
/// fields the intake screen shows. Kept complete rather than trimmed to the
/// label's four values: the app sends this object straight back on a
/// correction, so a field dropped here would be a field silently erased.
export const SPEC_FIELDS = [
  'filament_type', 'density', 'temperature_vitrification',
  'nozzle_temp_normal', 'nozzle_temp_initial_layer', 'nozzle_temp_high_flow',
  'nozzle_temp_range_low', 'nozzle_temp_range_high',
  'bed_temp', 'chamber_temp', 'flow_ratio', 'pressure_advance',
  'fan_min_speed', 'fan_max_speed', 'slow_down_layer_time',
  'filament_cost_estimate', 'notes', 'confidence'
];

export function slimFilament(f) {
  if (!f || typeof f !== 'object') return null;
  const src = f.specs || {};
  const specs = {};
  for (const k of SPEC_FIELDS) specs[k] = src[k] ?? null;
  return {
    id: f.id,
    manufacturer_id: f.manufacturer_id,
    manufacturer: f.manufacturer?.name ?? null,
    product_name: f.product_name,
    material_type: f.material_type,
    series: f.series ?? null,
    sub_brand: f.sub_brand ?? null,
    color_name: f.color_name ?? null,
    color_hex: f.color_hex ?? null,
    notes: f.notes ?? null,
    specs
  };
}

export function registerGenFilamentRoutes(app, deps) {
  const { requireUser, baseUrl } = deps;
  const fetchImpl = deps.fetchImpl || fetch;
  const configured = () => !!baseUrl;

  async function gf(path, { method = 'GET', body, timeout = CALL_TIMEOUT_MS } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const r = await fetchImpl(baseUrl.replace(/\/+$/, '') + path, {
        method,
        headers: body === undefined ? { accept: 'application/json' } : { accept: 'application/json', 'content-type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal
      });
      const text = await r.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = text; }
      return { ok: r.ok, status: r.status, data };
    } finally {
      clearTimeout(timer);
    }
  }

  function fail(reply, res, what) {
    const d = res.data && typeof res.data === 'object' ? (res.data.detail ?? res.data.error) : null;
    const msg = typeof d === 'string' ? d : `${what} failed (GenFilament ${res.status})`;
    return reply.code([400, 404, 409, 422].includes(res.status) ? res.status : 502).send({ error: msg });
  }

  async function guard(req, reply) {
    const who = await requireUser(req, reply);
    if (!who) return null;
    if (!configured()) {
      reply.code(503).send({ error: 'GenFilament is not configured on this server (OPHQ_GENFILAMENT_API_URL)' });
      return null;
    }
    return who;
  }

  app.get('/printhost/genfilament/status', async (req, reply) => {
    const who = await requireUser(req, reply); if (!who) return;
    if (!configured()) return { configured: false, reachable: false };
    try {
      const r = await gf('/api/health', { timeout: 5000 });
      return { configured: true, reachable: r.ok };
    } catch (e) {
      return { configured: true, reachable: false, detail: e.message };
    }
  });

  app.get('/printhost/genfilament/manufacturers', async (req, reply) => {
    const who = await guard(req, reply); if (!who) return;
    try {
      const r = await gf('/api/manufacturers/');
      if (!r.ok) return fail(reply, r, 'manufacturer listing');
      const list = Array.isArray(r.data) ? r.data : [];
      return { manufacturers: list.map((m) => ({ id: m.id, name: m.name, website: m.website ?? null })) };
    } catch (e) {
      return reply.code(502).send({ error: 'GenFilament unreachable: ' + e.message });
    }
  });

  // A brand the catalog has never seen (the common case for a small filament
  // maker) has to exist before its filament can be stored, so the intake screen
  // can create one rather than dead-ending on an unknown name.
  app.post('/printhost/genfilament/manufacturers', async (req, reply) => {
    const who = await guard(req, reply); if (!who) return;
    const name = (req.body?.name || '').toString().trim();
    if (!name) return reply.code(400).send({ error: 'name is required' });
    try {
      const existing = await gf('/api/manufacturers/');
      if (existing.ok && Array.isArray(existing.data)) {
        const hit = existing.data.find((m) => sameName(m.name, name));
        if (hit) return { manufacturer: { id: hit.id, name: hit.name, website: hit.website ?? null }, created: false };
      }
      const r = await gf('/api/manufacturers/', {
        method: 'POST',
        body: { name, website: req.body?.website || null, notes: req.body?.notes || null }
      });
      if (!r.ok) return fail(reply, r, 'manufacturer create');
      req.log.info({ userId: who.id ?? who.userId, name }, 'genfilament manufacturer created');
      return { manufacturer: { id: r.data?.id, name: r.data?.name ?? name, website: r.data?.website ?? null }, created: true };
    } catch (e) {
      return reply.code(502).send({ error: 'GenFilament unreachable: ' + e.message });
    }
  });

  // The stored-filament check that makes a repeat roll free: exact brand and
  // product match first, then anything from that brand whose name contains the
  // query, so a near miss is offered rather than silently sent to the AI.
  app.get('/printhost/genfilament/filaments', async (req, reply) => {
    const who = await guard(req, reply); if (!who) return;
    const brand = (req.query?.manufacturer || '').toString().trim();
    const product = (req.query?.product_name || '').toString().trim();
    const manufacturerId = req.query?.manufacturer_id ? Number(req.query.manufacturer_id) : null;
    try {
      const r = await gf('/api/filaments/');
      if (!r.ok) return fail(reply, r, 'filament listing');
      const all = (Array.isArray(r.data) ? r.data : []).map(slimFilament).filter(Boolean);
      const ofBrand = all.filter((f) =>
        (manufacturerId ? f.manufacturer_id === manufacturerId : true) &&
        (brand ? sameName(f.manufacturer, brand) : true));
      const exact = product ? ofBrand.filter((f) => sameName(f.product_name, product)) : [];
      const q = product.toLowerCase();
      const near = product
        ? ofBrand.filter((f) => !exact.includes(f) && String(f.product_name).toLowerCase().includes(q))
        : ofBrand;
      return { exact, near, total: all.length };
    } catch (e) {
      return reply.code(502).send({ error: 'GenFilament unreachable: ' + e.message });
    }
  });

  // An AI lookup, so it is slow and is only reached on a miss.
  app.post('/printhost/genfilament/lookup', async (req, reply) => {
    const who = await guard(req, reply); if (!who) return;
    const manufacturer = (req.body?.manufacturer || '').toString().trim();
    const productName = (req.body?.product_name || '').toString().trim();
    if (!manufacturer || !productName) return reply.code(400).send({ error: 'manufacturer and product_name are required' });
    try {
      const r = await gf('/api/lookup/', {
        method: 'POST',
        body: { manufacturer, product_name: productName },
        timeout: LOOKUP_TIMEOUT_MS
      });
      if (!r.ok) return fail(reply, r, 'lookup');
      req.log.info({ userId: who.id ?? who.userId, manufacturer, productName }, 'genfilament lookup');
      return { result: r.data };
    } catch (e) {
      const msg = e.name === 'AbortError'
        ? `lookup timed out after ${Math.round(LOOKUP_TIMEOUT_MS / 1000)}s`
        : 'GenFilament unreachable: ' + e.message;
      return reply.code(504).send({ error: msg });
    }
  });

  // Saving the verified values is what turns a lookup into a cache hit later.
  app.post('/printhost/genfilament/filaments', async (req, reply) => {
    const who = await guard(req, reply); if (!who) return;
    const body = req.body || {};
    if (!body.manufacturer_id || !body.product_name || !body.material_type || !body.specs) {
      return reply.code(400).send({ error: 'manufacturer_id, product_name, material_type and specs are required' });
    }
    try {
      const r = await gf('/api/filaments/', { method: 'POST', body });
      if (!r.ok) return fail(reply, r, 'filament save');
      return { filament: slimFilament(r.data) };
    } catch (e) {
      return reply.code(502).send({ error: 'GenFilament unreachable: ' + e.message });
    }
  });

  app.put('/printhost/genfilament/filaments/:id', async (req, reply) => {
    const who = await guard(req, reply); if (!who) return;
    const id = Number(req.params.id);
    if (!Number.isInteger(id) || id <= 0) return reply.code(400).send({ error: 'bad filament id' });
    try {
      const r = await gf(`/api/filaments/${id}`, { method: 'PUT', body: req.body || {} });
      if (!r.ok) return fail(reply, r, 'filament update');
      return { filament: slimFilament(r.data) };
    } catch (e) {
      return reply.code(502).send({ error: 'GenFilament unreachable: ' + e.message });
    }
  });
}
