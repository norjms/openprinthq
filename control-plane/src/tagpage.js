// OpenPrintHQ control-plane - the public lookup behind a scanned tag or label
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// A tag or a label QR is scanned by a phone that is not signed in and may not
// even be the owner's, so this is the one place inventory is readable without a
// session. Two things keep that honest:
//
//  - The code IS the credential. A tag's code is its NTAG UID, 14 hex digits
//    that were never chosen by us. A spool's code carries an HMAC of the tenant
//    and the spool id, so /t/s1, /t/s2, /t/s3 cannot be walked to read an
//    inventory: without the signature the id alone opens nothing.
//  - Only that one spool comes back, never a listing, never the account.
//
// The code does not name a tenant, so the lookup asks each instance in turn and
// the first match wins. That is a fan-out of one request per tenant, which is
// why the answer is cached briefly.

import crypto from 'node:crypto';

const TAG_RE = /^[0-9A-F]{8,30}$/;
const SPOOL_RE = /^s(\d+)-([0-9a-f]{10})$/;
const CACHE_MS = 15000;

/// The signature a spool code carries: tenant plus spool id, so a code minted
/// for one tenant cannot resolve in another and ids cannot be guessed.
export function spoolSignature(subdomain, spoolId, secret) {
  return crypto.createHmac('sha256', String(secret || ''))
    .update(`${subdomain}:${spoolId}`)
    .digest('hex')
    .slice(0, 10);
}

export function spoolCode(subdomain, spoolId, secret) {
  return `s${spoolId}-${spoolSignature(subdomain, spoolId, secret)}`;
}

export function parseCode(raw) {
  const s = String(raw ?? '').trim();
  const hex = s.replace(/[^0-9a-fA-F]/g, '').toUpperCase();
  const spool = SPOOL_RE.exec(s.toLowerCase());
  if (spool) return { kind: 'spool', id: Number(spool[1]), signature: spool[2] };
  if (TAG_RE.test(hex) && hex.length % 2 === 0 && !/^0+$/.test(hex)) return { kind: 'tag', uid: hex };
  return null;
}

/// Everything a phone at the shelf needs, and nothing about the account that
/// owns it.
export function publicSpool(s, code) {
  if (!s || typeof s !== 'object') return null;
  const num = (v) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v));
  const label = num(s.label_weight);
  const used = num(s.weight_used);
  return {
    code,
    id: s.id,
    material: s.material || '',
    subtype: s.subtype || null,
    brand: s.brand || null,
    color_name: s.color_name || null,
    rgba: s.rgba || null,
    label_weight: label,
    remaining_weight: label !== null && used !== null ? Math.max(0, label - used) : label,
    location: typeof s.location === 'object' ? (s.location?.name ?? null) : (s.location ?? null),
    note: s.note || null,
    tag_uid: s.tag_uid || null
  };
}

/// A plain fixed-window limiter. The point is not to stop a determined scraper,
/// it is to make guessing codes pointless while a real scan is never refused.
export function limiter({ perMinute = 40, now = () => Date.now() } = {}) {
  const seen = new Map();
  return function allow(key) {
    const minute = Math.floor(now() / 60000);
    const at = seen.get(key);
    if (!at || at.minute !== minute) {
      seen.set(key, { minute, count: 1 });
      if (seen.size > 5000) for (const [k, v] of seen) if (v.minute !== minute) seen.delete(k);
      return true;
    }
    at.count += 1;
    return at.count <= perMinute;
  };
}

export function registerTagPageRoutes(app, deps) {
  const { listInstances, engineBase, secret, genFilamentBase } = deps;
  const fetchImpl = deps.fetchImpl || fetch;
  const allow = deps.allow || limiter();
  const cache = new Map();

  async function engine(base, path) {
    const r = await fetchImpl(base + path, { headers: { accept: 'application/json' } });
    const text = await r.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    return { ok: r.ok, status: r.status, data };
  }

  async function spoolsOf(inst) {
    const base = engineBase(inst);
    if (!base) return [];
    const settings = await engine(base, '/api/v1/settings');
    const spoolman = settings.ok && settings.data &&
      (settings.data.spoolman_enabled === true || settings.data.spoolman_enabled === 'true');
    const res = await engine(base, (spoolman ? '/api/v1/spoolman/inventory' : '/api/v1/inventory') + '/spools');
    if (!res.ok) return [];
    return Array.isArray(res.data) ? res.data : (res.data?.items || []);
  }

  /// The print values live in GenFilament, keyed by the brand and product the
  /// spool was stocked under. Best effort: a spool with no stored filament still
  /// answers, it just has nothing to say about temperatures.
  async function filamentFor(spool) {
    if (!genFilamentBase || !spool?.brand) return null;
    const product = spool.subtype || spool.material;
    if (!product) return null;
    const same = (a, b) => String(a ?? '').trim().toLowerCase().replace(/\s+/g, ' ') ===
                           String(b ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
    try {
      const r = await fetchImpl(genFilamentBase.replace(/\/+$/, '') + '/api/filaments/',
                                { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(8000) });
      if (!r.ok) return null;
      const all = await r.json();
      const hit = (Array.isArray(all) ? all : []).find((f) =>
        same(f.manufacturer?.name, spool.brand) && same(f.product_name, product));
      if (!hit) return null;
      const s = hit.specs || {};
      return {
        id: hit.id,
        product_name: hit.product_name,
        material_type: hit.material_type,
        notes: hit.notes ?? null,
        specs: {
          filament_type: s.filament_type ?? null,
          density: s.density ?? null,
          nozzle_temp_normal: s.nozzle_temp_normal ?? null,
          nozzle_temp_initial_layer: s.nozzle_temp_initial_layer ?? null,
          nozzle_temp_range_low: s.nozzle_temp_range_low ?? null,
          nozzle_temp_range_high: s.nozzle_temp_range_high ?? null,
          bed_temp: s.bed_temp ?? null,
          chamber_temp: s.chamber_temp ?? null,
          flow_ratio: s.flow_ratio ?? null,
          notes: s.notes ?? null
        }
      };
    } catch {
      return null;
    }
  }

  async function resolve(parsed) {
    const instances = await listInstances();
    for (const inst of instances || []) {
      const spools = await spoolsOf(inst);
      const hit = parsed.kind === 'tag'
        ? spools.find((s) => String(s.tag_uid || '').toUpperCase() === parsed.uid)
        : spools.find((s) => Number(s.id) === parsed.id &&
            crypto.timingSafeEqual(Buffer.from(spoolSignature(inst.subdomain, s.id, secret)),
                                   Buffer.from(parsed.signature)));
      if (hit) return hit;
    }
    return null;
  }

  app.get('/api/pub/tag/:code', async (req, reply) => {
    const parsed = parseCode(req.params.code);
    if (!parsed) return reply.code(400).send({ error: 'that is not a tag code' });
    if (!allow(req.ip)) return reply.code(429).send({ error: 'too many lookups, try again in a minute' });

    const key = parsed.kind === 'tag' ? `t:${parsed.uid}` : `s:${parsed.id}:${parsed.signature}`;
    const cached = cache.get(key);
    if (cached && Date.now() - cached.at < CACHE_MS) return cached.body;

    let hit;
    try {
      hit = await resolve(parsed);
    } catch (e) {
      req.log.error({ err: e }, 'public tag lookup failed');
      return reply.code(502).send({ error: 'inventory is not reachable right now' });
    }
    // A wrong signature and an unknown tag answer identically: nothing here is
    // told apart by guessing.
    if (!hit) return reply.code(404).send({ error: 'no spool is linked to that code' });

    const body = {
      spool: publicSpool(hit, String(req.params.code)),
      filament: await filamentFor(hit)
    };
    cache.set(key, { at: Date.now(), body });
    return body;
  });
}
