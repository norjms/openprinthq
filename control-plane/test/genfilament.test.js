// GenFilament proxy: key scoping, stored-filament matching, lookup handling.
import test from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import { registerGenFilamentRoutes, slimFilament, sameName } from '../src/genfilament.js';

const FILAMENTS = [
  {
    id: 1531, manufacturer_id: 4, manufacturer: { id: 4, name: 'taulman3D' },
    product_name: 'PA 645', material_type: 'PA', color_name: 'Black', color_hex: '#000000',
    specs: { filament_type: 'PA', density: 1.13, nozzle_temp_normal: 245, bed_temp: 100, chamber_temp: 60, confidence: 'high' }
  },
  {
    id: 1532, manufacturer_id: 4, manufacturer: { id: 4, name: 'taulman3D' },
    product_name: 'PA 645 CF', material_type: 'PA-CF',
    specs: { nozzle_temp_normal: 250, bed_temp: 100 }
  },
  {
    id: 99, manufacturer_id: 7, manufacturer: { id: 7, name: 'Polymaker' },
    product_name: 'PolyLite PLA', material_type: 'PLA', specs: { nozzle_temp_normal: 210 }
  }
];

function build({ kind = 'extension', baseUrl = 'http://genfilament:8000', calls = [], lookupStatus = 200, hang = false } = {}) {
  const app = Fastify();
  const fetchImpl = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', body: opts.body });
    const json = (status, body) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
    if (url.endsWith('/api/health')) return json(200, { status: 'ok' });
    if (url.endsWith('/api/manufacturers/')) return json(200, [{ id: 4, name: 'taulman3D', website: 'https://taulman3d.com' }]);
    if (url.endsWith('/api/filaments/') && (opts.method || 'GET') === 'GET') return json(200, FILAMENTS);
    if (url.endsWith('/api/filaments/') && opts.method === 'POST') return json(201, { ...FILAMENTS[0], id: 2000 });
    if (url.includes('/api/filaments/') && opts.method === 'PUT') return json(200, { ...FILAMENTS[0], notes: 'corrected' });
    if (url.endsWith('/api/lookup/')) {
      if (hang) {
        const err = new Error('aborted');
        err.name = 'AbortError';
        throw err;
      }
      if (lookupStatus !== 200) return json(lookupStatus, { detail: 'model refused' });
      return json(200, { specs: { nozzle_temp_normal: 245, bed_temp: 100, confidence: 'medium' } });
    }
    return json(404, { detail: 'nope' });
  };
  registerGenFilamentRoutes(app, {
    requireUser: async (req, reply) => {
      const key = req.headers['x-api-key'];
      if (key !== 'good') { reply.code(401).send({ error: 'invalid or expired API key' }); return null; }
      if (kind !== 'extension') { reply.code(403).send({ error: 'wrong key kind' }); return null; }
      return { userId: 1, email: 'a@b.c', kind };
    },
    baseUrl,
    fetchImpl
  });
  return app;
}

const KEY = { 'x-api-key': 'good' };

test('sameName ignores case and spacing', () => {
  assert.equal(sameName('PA 645', ' pa  645 '), true);
  assert.equal(sameName('PA 645', 'PA 645 CF'), false);
  assert.equal(sameName('', ''), false);
});

test('slimFilament keeps the label fields and drops the rest', () => {
  const f = slimFilament(FILAMENTS[0]);
  assert.equal(f.manufacturer, 'taulman3D');
  assert.equal(f.specs.nozzle_temp_normal, 245);
  assert.equal(f.specs.bed_temp, 100);
  assert.equal(f.specs.confidence, 'high');
  assert.equal('slow_down_layer_time' in f.specs, false);
});

test('every route needs a valid key', async () => {
  const app = build();
  for (const [method, url] of [['GET', '/printhost/genfilament/manufacturers'], ['GET', '/printhost/genfilament/filaments'],
                               ['POST', '/printhost/genfilament/lookup']]) {
    const r = await app.inject({ method, url, payload: method === 'POST' ? {} : undefined });
    assert.equal(r.statusCode, 401, `${method} ${url}`);
  }
});

test('status reports when GenFilament is not configured', async () => {
  const r = await build({ baseUrl: '' }).inject({ url: '/printhost/genfilament/status', headers: KEY });
  assert.deepEqual(r.json(), { configured: false, reachable: false });
});

test('routes refuse to guess when GenFilament is not configured', async () => {
  const r = await build({ baseUrl: '' }).inject({ url: '/printhost/genfilament/manufacturers', headers: KEY });
  assert.equal(r.statusCode, 503);
});

test('an exact brand and product match is a cache hit, no lookup', async () => {
  const calls = [];
  const r = await build({ calls }).inject({
    url: '/printhost/genfilament/filaments?manufacturer=taulman3d&product_name=pa%20645', headers: KEY
  });
  const body = r.json();
  assert.equal(body.exact.length, 1);
  assert.equal(body.exact[0].id, 1531);
  assert.equal(body.near.some((f) => f.id === 1532), true, 'the CF variant is offered as a near match');
  assert.equal(calls.some((c) => c.url.endsWith('/api/lookup/')), false);
});

test('filament search stays inside the named brand', async () => {
  const r = await build().inject({
    url: '/printhost/genfilament/filaments?manufacturer=taulman3D&product_name=PLA', headers: KEY
  });
  const body = r.json();
  assert.equal(body.exact.length, 0);
  assert.equal(body.near.length, 0, 'Polymaker PolyLite PLA must not leak into a taulman3D search');
});

test('lookup forwards the brand and product', async () => {
  const calls = [];
  const r = await build({ calls }).inject({
    method: 'POST', url: '/printhost/genfilament/lookup', headers: KEY,
    payload: { manufacturer: 'taulman3D', product_name: 'PA 645' }
  });
  assert.equal(r.statusCode, 200);
  assert.equal(r.json().result.specs.nozzle_temp_normal, 245);
  assert.deepEqual(JSON.parse(calls.find((c) => c.url.endsWith('/api/lookup/')).body),
                   { manufacturer: 'taulman3D', product_name: 'PA 645' });
});

test('lookup needs both halves', async () => {
  const r = await build().inject({
    method: 'POST', url: '/printhost/genfilament/lookup', headers: KEY, payload: { manufacturer: 'taulman3D' }
  });
  assert.equal(r.statusCode, 400);
});

test('a hung lookup reports a timeout rather than a generic failure', async () => {
  const r = await build({ hang: true }).inject({
    method: 'POST', url: '/printhost/genfilament/lookup', headers: KEY,
    payload: { manufacturer: 'taulman3D', product_name: 'PA 645' }
  });
  assert.equal(r.statusCode, 504);
  assert.match(r.json().error, /timed out/);
});

test('saving verified specs creates the filament', async () => {
  const calls = [];
  const r = await build({ calls }).inject({
    method: 'POST', url: '/printhost/genfilament/filaments', headers: KEY,
    payload: { manufacturer_id: 4, product_name: 'PA 645', material_type: 'PA', specs: { nozzle_temp_normal: 245 } }
  });
  assert.equal(r.statusCode, 200);
  assert.equal(r.json().filament.id, 2000);
  assert.equal(calls.some((c) => c.method === 'POST' && c.url.endsWith('/api/filaments/')), true);
});

test('saving without specs is refused before reaching GenFilament', async () => {
  const calls = [];
  const r = await build({ calls }).inject({
    method: 'POST', url: '/printhost/genfilament/filaments', headers: KEY,
    payload: { manufacturer_id: 4, product_name: 'PA 645' }
  });
  assert.equal(r.statusCode, 400);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
});

test('a correction updates the stored filament', async () => {
  const calls = [];
  const r = await build({ calls }).inject({
    method: 'PUT', url: '/printhost/genfilament/filaments/1531', headers: KEY,
    payload: { notes: 'corrected' }
  });
  assert.equal(r.statusCode, 200);
  assert.equal(r.json().filament.notes, 'corrected');
  assert.equal(calls.find((c) => c.method === 'PUT').url.endsWith('/api/filaments/1531'), true);
});
