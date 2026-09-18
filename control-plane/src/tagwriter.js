// OpenPrintHQ control-plane - NFC tag writer API
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The desktop tag writer (a native app next to a USB NFC reader) needs four
// things: which inventory backend is live, the spool list, a way to link a tag
// UID to a spool, and a way to look a tag back up. Everything else about a
// spool is the engine's business and stays there.
//
// Why /printhost. A desktop app cannot complete Authentik forward-auth, and
// /printhost/* is already exempt from it at both edges (prod Caddy @public,
// test npmplus location block) and authenticates with X-Api-Key. Living under
// it means no edge change, and the existing access-key UI mints the credential.
//
// Scope. Only 'extension' access keys are accepted: those are the long-lived
// keys a user mints deliberately in Settings. Slicer bootstrap tokens live in a
// shared desktop image and have no business rewriting inventory.
//
// The engine exposes the same spool shape from both backends (built-in
// /inventory and the tenant's Spoolman /spoolman/inventory) but links tags on
// different paths, so the mode is resolved per request from the engine's own
// settings, the same way the web client does it.

const TAG_UID_RE = /^[0-9A-F]{8,30}$/;

export function normalizeTagUid(raw) {
  const hex = String(raw ?? '').replace(/[^0-9a-fA-F]/g, '').toUpperCase();
  if (!TAG_UID_RE.test(hex) || hex.length % 2 !== 0 || /^0+$/.test(hex)) return null;
  return hex;
}

// Only the fields a tag writer shows or encodes. The engine's spool dict carries
// scale, K-profile and slicer data the app has no use for.
export function slimSpool(s) {
  if (!s || typeof s !== 'object') return null;
  const num = (v) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v));
  const label = num(s.label_weight);
  const used = num(s.weight_used);
  return {
    id: s.id,
    material: s.material || '',
    subtype: s.subtype || null,
    brand: s.brand || null,
    color_name: s.color_name || null,
    rgba: s.rgba || null,
    label_weight: label,
    remaining_weight: label !== null && used !== null ? Math.max(0, label - used) : null,
    nozzle_temp_min: num(s.nozzle_temp_min),
    nozzle_temp_max: num(s.nozzle_temp_max),
    location: s.location || null,
    tag_uid: s.tag_uid || null,
    archived: !!s.archived_at
  };
}

export function registerTagWriterRoutes(app, deps) {
  const { resolveToken, getInstance, engineBase, publicUrl } = deps;
  const fetchImpl = deps.fetchImpl || fetch;

  async function tagUser(req, reply) {
    const key = req.headers['x-api-key'] ||
      (req.headers['authorization'] || '').replace(/^Bearer\s+/i, '');
    if (!key) { reply.code(401).send({ error: 'missing API key' }); return null; }
    const who = await resolveToken(String(key).trim());
    if (!who) { reply.code(401).send({ error: 'invalid or expired API key' }); return null; }
    if (who.kind !== 'extension') {
      reply.code(403).send({ error: 'this key cannot manage filament, mint an access key in Settings' });
      return null;
    }
    const inst = await getInstance(who.userId);
    const base = engineBase(inst);
    if (!base) { reply.code(409).send({ error: 'no running instance for this account' }); return null; }
    return { ...who, base };
  }

  async function engine(base, path, opts = {}) {
    const headers = { accept: 'application/json' };
    if (opts.body !== undefined) headers['content-type'] = 'application/json';
    const r = await fetchImpl(base + path, {
      method: opts.method || 'GET',
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body)
    });
    let data = null;
    const text = await r.text();
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    return { ok: r.ok, status: r.status, data };
  }

  async function inventoryMode(base) {
    const s = await engine(base, '/api/v1/settings');
    const on = s.ok && s.data && (s.data.spoolman_enabled === true || s.data.spoolman_enabled === 'true');
    return on ? 'spoolman' : 'internal';
  }
  const invBase = (mode) => (mode === 'spoolman' ? '/api/v1/spoolman/inventory' : '/api/v1/inventory');

  function engineError(reply, res, what) {
    const d = res.data && typeof res.data === 'object' ? (res.data.detail ?? res.data.error) : null;
    const msg = typeof d === 'string' ? d : `${what} failed (engine ${res.status})`;
    const code = [400, 404, 409, 422].includes(res.status) ? res.status : 502;
    return reply.code(code).send({ error: msg });
  }

  async function listSpools(base, mode) {
    const res = await engine(base, invBase(mode) + '/spools');
    if (!res.ok) return { res };
    const arr = Array.isArray(res.data) ? res.data : (res.data?.items || []);
    return { res, spools: arr.map(slimSpool).filter((s) => s && !s.archived) };
  }

  app.get('/printhost/tags/config', async (req, reply) => {
    const who = await tagUser(req, reply); if (!who) return;
    try {
      return {
        account: who.email,
        inventory_mode: await inventoryMode(who.base),
        tag_url_base: publicUrl ? publicUrl + '/t/' : null
      };
    } catch (e) {
      return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
    }
  });

  app.get('/printhost/tags/spools', async (req, reply) => {
    const who = await tagUser(req, reply); if (!who) return;
    try {
      const mode = await inventoryMode(who.base);
      const { res, spools } = await listSpools(who.base, mode);
      if (!spools) return engineError(reply, res, 'spool listing');
      return { inventory_mode: mode, spools };
    } catch (e) {
      return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
    }
  });

  app.get('/printhost/tags/lookup', async (req, reply) => {
    const who = await tagUser(req, reply); if (!who) return;
    const uid = normalizeTagUid(req.query?.tag_uid);
    if (!uid) return reply.code(400).send({ error: 'tag_uid must be 8 to 30 hex characters' });
    try {
      const mode = await inventoryMode(who.base);
      const { res, spools } = await listSpools(who.base, mode);
      if (!spools) return engineError(reply, res, 'spool listing');
      const spool = spools.find((s) => (s.tag_uid || '').toUpperCase() === uid) || null;
      return { tag_uid: uid, spool };
    } catch (e) {
      return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
    }
  });

  app.post('/printhost/tags/link', async (req, reply) => {
    const who = await tagUser(req, reply); if (!who) return;
    const spoolId = Number(req.body?.spool_id);
    const uid = normalizeTagUid(req.body?.tag_uid);
    if (!Number.isInteger(spoolId) || spoolId <= 0) return reply.code(400).send({ error: 'spool_id required' });
    if (!uid) return reply.code(400).send({ error: 'tag_uid must be 8 to 30 hex characters' });
    try {
      const mode = await inventoryMode(who.base);
      const path = mode === 'spoolman'
        ? `/api/v1/spoolman/inventory/spools/${spoolId}/tag`
        : `/api/v1/inventory/spools/${spoolId}/link-tag`;
      const res = await engine(who.base, path, { method: 'PATCH', body: { tag_uid: uid } });
      if (!res.ok) return engineError(reply, res, 'tag link');
      req.log.info({ userId: who.userId, spoolId, mode }, 'nfc tag linked');
      return { inventory_mode: mode, spool: slimSpool(res.data) };
    } catch (e) {
      return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
    }
  });
}
