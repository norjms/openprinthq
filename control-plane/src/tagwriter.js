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

import { spoolCode } from './tagpage.js';

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
    core_weight: num(s.core_weight),
    remaining_weight: label !== null && used !== null ? Math.max(0, label - used) : null,
    nozzle_temp_min: num(s.nozzle_temp_min),
    nozzle_temp_max: num(s.nozzle_temp_max),
    location: s.location || null,
    tag_uid: s.tag_uid || null,
    archived: !!s.archived_at
  };
}

/// The access-key check shared by every desktop-writer route: a user-minted
/// 'extension' key and nothing else. Slicer bootstrap tokens live in a shared
/// Kasm image and have no business here.
export function accessKeyUser(resolveToken) {
  return async function (req, reply) {
    const key = req.headers['x-api-key'] ||
      (req.headers['authorization'] || '').replace(/^Bearer\s+/i, '');
    if (!key) { reply.code(401).send({ error: 'missing API key' }); return null; }
    const who = await resolveToken(String(key).trim());
    if (!who) { reply.code(401).send({ error: 'invalid or expired API key' }); return null; }
    if (who.kind !== 'extension') {
      reply.code(403).send({ error: 'this key cannot manage filament, mint an access key in Settings' });
      return null;
    }
    return who;
  };
}

export function registerTagWriterRoutes(app, deps) {
  const { resolveToken, getInstance, engineBase, publicUrl, secret } = deps;
  const fetchImpl = deps.fetchImpl || fetch;
  const keyUser = accessKeyUser(resolveToken);

  async function tagUser(req, reply) {
    const who = await keyUser(req, reply);
    if (!who) return null;
    const inst = await getInstance(who.userId);
    const base = engineBase(inst);
    if (!base) { reply.code(409).send({ error: 'no running instance for this account' }); return null; }
    return { ...who, base, subdomain: inst.subdomain };
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

  async function listSpools(base, mode, subdomain) {
    const res = await engine(base, invBase(mode) + '/spools');
    if (!res.ok) return { res };
    const arr = Array.isArray(res.data) ? res.data : (res.data?.items || []);
    const spools = arr.map(slimSpool).filter((s) => s && !s.archived);
    // The code a label's QR uses when the spool has no tag yet. Signed, so the
    // public lookup cannot be walked by spool id.
    if (subdomain && secret) for (const s of spools) s.code = spoolCode(subdomain, s.id, secret);
    return { res, spools };
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
      const { res, spools } = await listSpools(who.base, mode, who.subdomain);
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
      const { res, spools } = await listSpools(who.base, mode, who.subdomain);
      if (!spools) return engineError(reply, res, 'spool listing');
      const spool = spools.find((s) => (s.tag_uid || '').toUpperCase() === uid) || null;
      return { tag_uid: uid, spool };
    } catch (e) {
      return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
    }
  });

  // Free a tag for reuse. The engine clears extra.tag when the spool PATCH
  // carries an explicit null tag_uid, so no engine change is needed for this.
  // Stock a new spool. The desktop writer calls this after the operator has
  // verified the filament's specs, so the body is already the shape the engine
  // wants and this route only guards it.
  app.post('/printhost/tags/spools', async (req, reply) => {
    const who = await tagUser(req, reply); if (!who) return;
    const b = req.body || {};
    if (!b.material && !b.spoolman_filament_id) {
      return reply.code(400).send({ error: 'material (or spoolman_filament_id) is required' });
    }
    const payload = {};
    for (const k of ['spoolman_filament_id', 'material', 'subtype', 'brand', 'color_name', 'rgba',
                     'label_weight', 'core_weight', 'weight_used', 'note', 'cost_per_kg',
                     'storage_location']) {
      if (b[k] !== undefined && b[k] !== null) payload[k] = b[k];
    }
    try {
      const mode = await inventoryMode(who.base);
      const res = await engine(who.base, invBase(mode) + '/spools', { method: 'POST', body: payload });
      if (!res.ok) return engineError(reply, res, 'spool create');
      const spool = slimSpool(res.data);
      if (spool && who.subdomain && secret) spool.code = spoolCode(who.subdomain, spool.id, secret);
      req.log.info({ userId: who.userId, spoolId: spool?.id, mode }, 'spool created from the tag writer');
      return { inventory_mode: mode, spool };
    } catch (e) {
      return reply.code(502).send({ error: 'engine unreachable: ' + e.message });
    }
  });

  app.post('/printhost/tags/unlink', async (req, reply) => {
    const who = await tagUser(req, reply); if (!who) return;
    const uid = req.body?.tag_uid === undefined ? null : normalizeTagUid(req.body.tag_uid);
    let spoolId = req.body?.spool_id === undefined ? null : Number(req.body.spool_id);
    if (req.body?.tag_uid !== undefined && !uid) return reply.code(400).send({ error: 'tag_uid must be 8 to 30 hex characters' });
    if (spoolId !== null && (!Number.isInteger(spoolId) || spoolId <= 0)) return reply.code(400).send({ error: 'spool_id must be a positive integer' });
    if (!uid && spoolId === null) return reply.code(400).send({ error: 'tag_uid or spool_id required' });
    try {
      const mode = await inventoryMode(who.base);
      const { res, spools } = await listSpools(who.base, mode, who.subdomain);
      if (!spools) return engineError(reply, res, 'spool listing');

      // Resolve whichever half the caller did not give, and refuse a mismatch
      // rather than clearing a tag the caller did not mean.
      const byTag = uid ? spools.find((s) => (s.tag_uid || '').toUpperCase() === uid) : null;
      if (uid && !byTag) return reply.code(404).send({ error: `no spool is linked to tag ${uid}` });
      if (spoolId === null) spoolId = byTag.id;
      else if (byTag && byTag.id !== spoolId) {
        return reply.code(409).send({ error: `tag ${uid} is linked to spool ${byTag.id}, not ${spoolId}` });
      }

      const target = spools.find((s) => s.id === spoolId);
      if (!target) return reply.code(404).send({ error: `spool ${spoolId} not found` });
      if (!target.tag_uid) return { inventory_mode: mode, spool: target, already_unlinked: true };

      const path = mode === 'spoolman'
        ? `/api/v1/spoolman/inventory/spools/${spoolId}`
        : `/api/v1/inventory/spools/${spoolId}`;
      const r = await engine(who.base, path, { method: 'PATCH', body: { tag_uid: null } });
      if (!r.ok) return engineError(reply, r, 'tag unlink');
      req.log.info({ userId: who.userId, spoolId, mode }, 'nfc tag unlinked');
      return { inventory_mode: mode, spool: slimSpool(r.data), unlinked_tag: target.tag_uid };
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
