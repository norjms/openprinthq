// The public tag landing lookup: what a code opens, and what it must not.
import test from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import { registerTagPageRoutes, spoolCode, spoolSignature, parseCode, publicSpool, limiter } from '../src/tagpage.js';

const SECRET = 'test-secret';
const SPOOLS = [
  { id: 12, material: 'PP', subtype: null, brand: 'Channel Prime Alliance', color_name: 'Clear',
    rgba: 'F5F5F5FF', label_weight: 613, weight_used: 0, note: 'empty spool 148g', tag_uid: '04C77A9EE42A81' },
  { id: 9, material: 'PLA', subtype: 'Basic', brand: 'Bambu Lab', label_weight: 1000, weight_used: 250,
    location: { name: 'Shelf B' }, tag_uid: null }
];

function build({ instances = [{ subdomain: 'norjms' }], genFilamentBase = 'http://gf:8000', calls = [] } = {}) {
  const app = Fastify();
  const fetchImpl = async (url) => {
    calls.push(url);
    const json = (status, body) => ({ ok: status < 400, status, text: async () => JSON.stringify(body), json: async () => body });
    if (url.endsWith('/api/v1/settings')) return json(200, { spoolman_enabled: true });
    if (url.endsWith('/spoolman/inventory/spools')) return json(200, SPOOLS);
    if (url.endsWith('/api/filaments/')) return json(200, [{
      id: 1532, product_name: 'PP', material_type: 'PP', manufacturer: { name: 'Channel Prime Alliance' },
      specs: { filament_type: 'PP', nozzle_temp_normal: 225, bed_temp: 70, density: 0.9, fan_max_speed: 100 }
    }]);
    return json(404, { detail: 'nope' });
  };
  registerTagPageRoutes(app, {
    listInstances: async () => instances,
    engineBase: (i) => `http://ophq-${i.subdomain}:8000`,
    secret: SECRET,
    genFilamentBase,
    fetchImpl
  });
  return app;
}

test('a tag UID and a signed spool code both parse', () => {
  assert.deepEqual(parseCode('04C77A9EE42A81'), { kind: 'tag', uid: '04C77A9EE42A81' });
  assert.deepEqual(parseCode('s12-abcdef0123'), { kind: 'spool', id: 12, signature: 'abcdef0123' });
  assert.equal(parseCode('s12'), null, 'an unsigned spool id is not a code');
  assert.equal(parseCode('00000000'), null);
  assert.equal(parseCode('hello'), null);
});

test('a spool code is tied to its tenant', () => {
  const mine = spoolCode('norjms', 12, SECRET);
  assert.match(mine, /^s12-[0-9a-f]{10}$/);
  assert.notEqual(spoolSignature('norjms', 12, SECRET), spoolSignature('someone-else', 12, SECRET));
  assert.notEqual(spoolSignature('norjms', 12, SECRET), spoolSignature('norjms', 13, SECRET));
  assert.notEqual(spoolSignature('norjms', 12, SECRET), spoolSignature('norjms', 12, 'other-secret'));
});

test('scanning a tag returns that spool and its stored values', async () => {
  const r = await build().inject({ url: '/api/pub/tag/04C77A9EE42A81' });
  assert.equal(r.statusCode, 200);
  const b = r.json();
  assert.equal(b.spool.id, 12);
  assert.equal(b.spool.brand, 'Channel Prime Alliance');
  assert.equal(b.spool.remaining_weight, 613);
  assert.equal(b.filament.specs.nozzle_temp_normal, 225);
  assert.equal(b.filament.specs.bed_temp, 70);
});

test('nothing about the account comes back', async () => {
  const b = (await build().inject({ url: '/api/pub/tag/04C77A9EE42A81' })).json();
  const flat = JSON.stringify(b).toLowerCase();
  assert.equal('account' in b, false);
  assert.equal(flat.includes('@'), false, 'no email anywhere in the payload');
  assert.equal(flat.includes('subdomain'), false);
});

test('a spool id cannot be walked without its signature', async () => {
  for (const code of ['s9-0000000000', 's12-0000000000', 's1-abcdef0123']) {
    const r = await build().inject({ url: `/api/pub/tag/${code}` });
    assert.equal(r.statusCode, 404, code);
    assert.match(r.json().error, /no spool/);
  }
});

test('a correctly signed spool code opens the spool that has no tag', async () => {
  const r = await build().inject({ url: `/api/pub/tag/${spoolCode('norjms', 9, SECRET)}` });
  assert.equal(r.statusCode, 200);
  const b = r.json();
  assert.equal(b.spool.id, 9);
  assert.equal(b.spool.remaining_weight, 750, 'label weight less what has been used');
  assert.equal(b.spool.location, 'Shelf B', 'a location object is flattened to its name');
  assert.equal(b.spool.tag_uid, null);
});

test('an unknown tag and a bad signature are indistinguishable', async () => {
  const unknown = await build().inject({ url: '/api/pub/tag/04AAAAAAAAAAAA' });
  const forged = await build().inject({ url: '/api/pub/tag/s12-0000000000' });
  assert.equal(unknown.statusCode, forged.statusCode);
  assert.deepEqual(unknown.json(), forged.json());
});

test('a code from another tenant does not resolve here', async () => {
  const r = await build({ instances: [{ subdomain: 'norjms' }] })
    .inject({ url: `/api/pub/tag/${spoolCode('someone-else', 12, SECRET)}` });
  assert.equal(r.statusCode, 404);
});

test('the lookup is refused once a scanner is obviously guessing', async () => {
  const app = build();
  const allowAll = [];
  for (let i = 0; i < 45; i++) allowAll.push(await app.inject({ url: `/api/pub/tag/s${i}-0000000000` }));
  assert.equal(allowAll.filter((r) => r.statusCode === 429).length > 0, true, 'guessing hits the limiter');
});

test('a spool with no stored filament still answers', async () => {
  const r = await build({ genFilamentBase: '' }).inject({ url: '/api/pub/tag/04C77A9EE42A81' });
  assert.equal(r.statusCode, 200);
  assert.equal(r.json().filament, null);
});

test('publicSpool keeps remaining weight honest when nothing has been used', () => {
  assert.equal(publicSpool({ id: 1, label_weight: 1000 }, 'x').remaining_weight, 1000);
  assert.equal(publicSpool({ id: 1, label_weight: 1000, weight_used: 1200 }, 'x').remaining_weight, 0);
});

test('the limiter counts per key and resets each minute', () => {
  let now = 0;
  const allow = limiter({ perMinute: 2, now: () => now });
  assert.equal(allow('a'), true);
  assert.equal(allow('a'), true);
  assert.equal(allow('a'), false);
  assert.equal(allow('b'), true, 'one scanner does not lock out another');
  now = 61000;
  assert.equal(allow('a'), true);
});
