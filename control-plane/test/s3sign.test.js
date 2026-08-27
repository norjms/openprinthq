import { test } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { presign, amzDates } from '../src/s3sign.js';

// AWS publishes a signing-key derivation vector in the SigV4 docs. If this
// drifts, every signature is wrong, so it is checked before anything else.
test('signing key derivation matches the AWS reference vector', () => {
  const h = (k, d) => crypto.createHmac('sha256', k).update(d, 'utf8').digest();
  const kDate = h('AWS4' + 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY', '20150830');
  const kRegion = h(kDate, 'us-east-1');
  const kService = h(kRegion, 'iam');
  const kSigning = h(kService, 'aws4_request');
  assert.equal(kSigning.toString('hex'),
    'c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9');
});

test('amzDates produces the two formats SigV4 wants', () => {
  const { amzDate, dateStamp } = amzDates(new Date('2026-08-27T15:45:00.000Z'));
  assert.equal(amzDate, '20260827T154500Z');
  assert.equal(dateStamp, '20260827');
});

test('presigned URL carries the required query parameters', () => {
  const r = presign({
    method: 'PUT', endpoint: 'https://s3.example.com', bucket: 'b', key: 'k.gcode',
    accessKeyId: 'AKID', secretAccessKey: 'SECRET', region: 'garage'
  });
  const u = new URL(r.url);
  assert.equal(u.searchParams.get('X-Amz-Algorithm'), 'AWS4-HMAC-SHA256');
  assert.equal(u.searchParams.get('X-Amz-SignedHeaders'), 'host');
  assert.match(u.searchParams.get('X-Amz-Credential'), /^AKID\/\d{8}\/garage\/s3\/aws4_request$/);
  assert.match(u.searchParams.get('X-Amz-Signature'), /^[0-9a-f]{64}$/);
  assert.equal(u.pathname, '/b/k.gcode');
});

test('object keys keep slashes but escape each segment', () => {
  const r = presign({
    method: 'PUT', endpoint: 'https://s3.example.com', bucket: 'b',
    key: 'plates/my plate (v2).3mf',
    accessKeyId: 'AKID', secretAccessKey: 'SECRET'
  });
  assert.ok(r.url.includes('/b/plates/my%20plate%20%28v2%29.3mf'));
});

// The sub-path case: the client hits /s3/..., the proxy strips it, the store
// sees /bucket/key. The signature must cover the STORE's view or it fails.
test('pathPrefix appears in the URL but not in the signed path', () => {
  const common = {
    method: 'PUT', endpoint: 'https://x.example.com', bucket: 'b', key: 'k',
    accessKeyId: 'AKID', secretAccessKey: 'SECRET', now: new Date('2026-08-27T00:00:00Z')
  };
  const plain = presign(common);
  const prefixed = presign({ ...common, pathPrefix: '/s3/' });
  assert.ok(prefixed.url.includes('/s3/b/k?'));
  assert.equal(new URL(plain.url).searchParams.get('X-Amz-Signature'),
    new URL(prefixed.url).searchParams.get('X-Amz-Signature'));
});

test('missing inputs are rejected rather than signed as empty', () => {
  assert.throws(() => presign({ bucket: 'b', key: 'k', accessKeyId: 'a', secretAccessKey: 's' }), /endpoint/);
  assert.throws(() => presign({ endpoint: 'https://x', key: 'k', accessKeyId: 'a', secretAccessKey: 's' }), /bucket/);
  assert.throws(() => presign({ endpoint: 'https://x', bucket: 'b', key: 'k' }), /credentials/);
});

// The three endpoints must produce three different signatures even for the same
// object, because the host is part of what is signed. If this ever collapses to
// one, a caller is being handed an address it cannot reach.
test('different endpoints yield different signatures for the same object', () => {
  const common = {
    method: 'PUT', bucket: 'b', key: 'plates/p.3mf',
    accessKeyId: 'AKID', secretAccessKey: 'SECRET',
    now: new Date('2026-08-27T00:00:00Z')
  };
  const sig = (endpoint) =>
    new URL(presign({ ...common, endpoint }).url).searchParams.get('X-Amz-Signature');
  const pub = sig('https://s3.example.com');
  const lan = sig('http://10.0.0.62:3900');
  const eng = sig('http://172.17.0.1:3900');
  assert.notEqual(pub, lan);
  assert.notEqual(lan, eng);
  assert.notEqual(pub, eng);
});
