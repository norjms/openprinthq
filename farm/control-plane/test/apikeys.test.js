// Scoped API keys: what each scope may reach, and how a key holder is resolved.
import test from 'node:test';
import assert from 'node:assert/strict';
import { scopeFor, allows, presentedKey, keyCaller, normalizeScope, API_KEY_PREFIX } from '../src/apikeys.js';

test('reads need read, getting work in needs submit, the rest needs control', () => {
  assert.equal(scopeFor('GET', '/api/v1/printers/'), 'read');
  assert.equal(scopeFor('GET', '/api/v1/queue/?status=pending'), 'read');
  assert.equal(scopeFor('POST', '/api/v1/queue/'), 'submit');
  assert.equal(scopeFor('POST', '/api/v1/library/files'), 'submit');
  assert.equal(scopeFor('POST', '/api/v1/library/files/12/print'), 'submit');
  assert.equal(scopeFor('POST', '/api/v1/archives/upload'), 'submit');
  assert.equal(scopeFor('POST', '/api/v1/archives/5/reprint'), 'submit');
  assert.equal(scopeFor('POST', '/api/v1/printers/3/print/pause'), 'control');
  assert.equal(scopeFor('PATCH', '/api/v1/queue/9'), 'control');
  assert.equal(scopeFor('DELETE', '/api/v1/library/files/12'), 'control');
  assert.equal(scopeFor('PUT', '/api/v1/settings/'), 'control');
});

test('account and credential routes never take a key, whatever the method', () => {
  for (const [m, p] of [
    ['GET', '/api/v1/users'], ['POST', '/api/v1/auth/login'], ['GET', '/api/v1/auth/tokens'],
    ['POST', '/api/v1/api-keys/'], ['GET', '/api/v1/mfa/status'], ['PATCH', '/api/v1/groups/1'],
    ['POST', '/api/v1/cloud/login'], ['GET', '/api/v1/settings/backup'], ['POST', '/api/v1/settings/restore'],
    ['GET', '/api/v1/printers/../users'], ['GET', '/api/v1/printers/%2e%2e/users']
  ]) {
    assert.equal(scopeFor(m, p), 'deny', `${m} ${p}`);
    assert.equal(allows('control', scopeFor(m, p)), false);
  }
  // Read-only cloud status is still fine.
  assert.equal(scopeFor('GET', '/api/v1/cloud/status'), 'read');
});

test('scopes are inclusive', () => {
  assert.ok(allows('control', 'read') && allows('control', 'submit') && allows('control', 'control'));
  assert.ok(allows('submit', 'read') && allows('submit', 'submit') && !allows('submit', 'control'));
  assert.ok(allows('read', 'read') && !allows('read', 'submit'));
  assert.ok(!allows(null, 'read') && !allows('admin', 'read'));
  assert.equal(normalizeScope(' Control '), 'control');
  assert.equal(normalizeScope('owner'), null);
});

test('only ophqk_ keys count, from Bearer or X-API-Key', () => {
  const k = API_KEY_PREFIX + 'abc';
  assert.equal(presentedKey({ headers: { authorization: `Bearer ${k}` } }), k);
  assert.equal(presentedKey({ headers: { 'x-api-key': k } }), k);
  assert.equal(presentedKey({ headers: { authorization: 'Bearer ophqx_extension' } }), '');
  assert.equal(presentedKey({ headers: {} }), '');
});

function reply() {
  const r = { statusCode: 200, body: null };
  r.code = (c) => { r.statusCode = c; return r; };
  r.send = (b) => { r.body = b; return r; };
  return r;
}

test('a key holder is resolved, and refused past their scope', async () => {
  const k = API_KEY_PREFIX + 'secret';
  const deps = {
    resolveToken: async (t) => (t === k ? { userId: 4, kind: 'api', scope: 'submit', tokenId: 'tid' } : null),
    getUser: async (id) => (id === 4 ? { id: 4, email: 'a@b.example' } : null)
  };
  const req = { headers: { authorization: `Bearer ${k}` } };
  const ok = await keyCaller(req, reply(), 'submit', deps);
  assert.equal(ok.user.id, 4);
  assert.deepEqual(ok.key, { id: 'tid', scope: 'submit' });

  const r1 = reply();
  assert.equal(await keyCaller(req, r1, 'control', deps), null);
  assert.equal(r1.statusCode, 403);
  assert.equal(r1.body.needs, 'control');

  const r2 = reply();
  assert.equal(await keyCaller(req, r2, 'deny', deps), null);
  assert.equal(r2.statusCode, 403);

  const r3 = reply();
  assert.equal(await keyCaller({ headers: { authorization: `Bearer ${API_KEY_PREFIX}nope` } }, r3, 'read', deps), null);
  assert.equal(r3.statusCode, 401);

  // A key of another kind (slicer, extension) is not an API key, even with the right prefix.
  const r4 = reply();
  const other = { ...deps, resolveToken: async () => ({ userId: 4, kind: 'slicer', scope: null }) };
  assert.equal(await keyCaller(req, r4, 'read', other), null);
  assert.equal(r4.statusCode, 401);
});
