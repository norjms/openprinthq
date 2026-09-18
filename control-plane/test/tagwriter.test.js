// NFC tag writer routes: key scoping, inventory-mode routing, UID validation.
import test from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import { registerTagWriterRoutes, normalizeTagUid, slimSpool } from '../src/tagwriter.js';

const SPOOL = {
  id: 7, material: 'PETG', subtype: 'HF', brand: 'Bambu Lab', color_name: 'Jade White',
  rgba: 'FFFFFFFF', label_weight: 1000, weight_used: 250, nozzle_temp_min: 230,
  tag_uid: '04A1B2C3D4E5F6', archived_at: null
};

function build({ mode = 'spoolman', kind = 'extension', patchStatus = 200, calls = [] } = {}) {
  const app = Fastify();
  const fetchImpl = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', body: opts.body });
    const json = (status, body) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
    if (url.endsWith('/api/v1/settings')) return json(200, { spoolman_enabled: mode === 'spoolman' });
    if (url.endsWith('/spools') && (opts.method || 'GET') === 'GET') {
      return json(200, [SPOOL, { ...SPOOL, id: 8, tag_uid: null, archived_at: '2026-01-01' }]);
    }
    if (opts.method === 'PATCH') {
      if (patchStatus !== 200) return json(patchStatus, { detail: 'Tag is already assigned to spool 3' });
      return json(200, { ...SPOOL, tag_uid: JSON.parse(opts.body).tag_uid });
    }
    return json(404, { detail: 'nope' });
  };
  registerTagWriterRoutes(app, {
    resolveToken: async (t) => (t === 'good' ? { userId: 1, email: 'a@b.c', kind } : null),
    getInstance: async () => ({ subdomain: 'acme' }),
    engineBase: (inst) => `http://ophq-${inst.subdomain}:8000`,
    publicUrl: 'https://openprinthq.com',
    fetchImpl
  });
  return app;
}

test('normalizeTagUid accepts 4, 7 and 10 byte UIDs and rejects junk', () => {
  assert.equal(normalizeTagUid('04:a1:b2:c3:d4:e5:f6'), '04A1B2C3D4E5F6');
  assert.equal(normalizeTagUid('DEADBEEF'), 'DEADBEEF');
  assert.equal(normalizeTagUid('ABC'), null);
  assert.equal(normalizeTagUid('0000000000000000'), null);
  assert.equal(normalizeTagUid('04A1B2C3D4E5F'), null);
});

test('slimSpool derives remaining weight', () => {
  assert.equal(slimSpool(SPOOL).remaining_weight, 750);
});

test('missing and wrong keys are refused', async () => {
  const app = build();
  assert.equal((await app.inject({ url: '/printhost/tags/config' })).statusCode, 401);
  assert.equal((await app.inject({ url: '/printhost/tags/config', headers: { 'x-api-key': 'bad' } })).statusCode, 401);
});

test('slicer tokens cannot touch inventory', async () => {
  const app = build({ kind: 'slicer' });
  const r = await app.inject({ url: '/printhost/tags/spools', headers: { 'x-api-key': 'good' } });
  assert.equal(r.statusCode, 403);
});

test('config reports mode and tag url base', async () => {
  const r = await build().inject({ url: '/printhost/tags/config', headers: { 'x-api-key': 'good' } });
  assert.deepEqual(r.json(), { account: 'a@b.c', inventory_mode: 'spoolman', tag_url_base: 'https://openprinthq.com/t/' });
});

test('spools list hides archived spools', async () => {
  const r = await build().inject({ url: '/printhost/tags/spools', headers: { authorization: 'Bearer good' } });
  assert.equal(r.statusCode, 200);
  assert.deepEqual(r.json().spools.map((s) => s.id), [7]);
});

test('lookup finds the spool by tag uid', async () => {
  const r = await build().inject({ url: '/printhost/tags/lookup?tag_uid=04a1b2c3d4e5f6', headers: { 'x-api-key': 'good' } });
  assert.equal(r.json().spool.id, 7);
});

test('link routes to spoolman tag path in spoolman mode', async () => {
  const calls = [];
  const r = await build({ calls }).inject({
    method: 'POST', url: '/printhost/tags/link', headers: { 'x-api-key': 'good' },
    payload: { spool_id: 7, tag_uid: '04 A1 B2 C3 D4 E5 F6' }
  });
  assert.equal(r.statusCode, 200);
  const patch = calls.find((c) => c.method === 'PATCH');
  assert.equal(patch.url, 'http://ophq-acme:8000/api/v1/spoolman/inventory/spools/7/tag');
  assert.deepEqual(JSON.parse(patch.body), { tag_uid: '04A1B2C3D4E5F6' });
});

test('link routes to link-tag path in internal mode', async () => {
  const calls = [];
  await build({ mode: 'internal', calls }).inject({
    method: 'POST', url: '/printhost/tags/link', headers: { 'x-api-key': 'good' },
    payload: { spool_id: 7, tag_uid: '04A1B2C3D4E5F6' }
  });
  assert.equal(calls.find((c) => c.method === 'PATCH').url, 'http://ophq-acme:8000/api/v1/inventory/spools/7/link-tag');
});

test('engine conflict passes through as 409 with its message', async () => {
  const r = await build({ patchStatus: 409 }).inject({
    method: 'POST', url: '/printhost/tags/link', headers: { 'x-api-key': 'good' },
    payload: { spool_id: 7, tag_uid: '04A1B2C3D4E5F6' }
  });
  assert.equal(r.statusCode, 409);
  assert.match(r.json().error, /already assigned to spool 3/);
});

test('bad input is rejected before reaching the engine', async () => {
  const calls = [];
  const r = await build({ calls }).inject({
    method: 'POST', url: '/printhost/tags/link', headers: { 'x-api-key': 'good' },
    payload: { spool_id: 7, tag_uid: 'xyz' }
  });
  assert.equal(r.statusCode, 400);
  assert.equal(calls.filter((c) => c.method === 'PATCH').length, 0);
});
