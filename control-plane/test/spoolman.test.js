// Per-tenant Spoolman naming and isolation invariants.
import test from 'node:test';
import assert from 'node:assert/strict';

process.env.OPHQ_PG_PASS = 'test-admin-password';
process.env.OPHQ_SPOOLMAN_IMAGE = 'ghcr.io/donkie/spoolman:0.26.1';
const m = await import('../src/spoolman.js');

test('names are per tenant and never the shared network', () => {
  assert.equal(m.spoolmanName('acme-co'), 'ophq-spoolman-acme-co');
  assert.equal(m.spoolmanNet('acme-co'), 'ophq-spoolman-net-acme-co');
  assert.notEqual(m.spoolmanNet('acme-co'), 'openprinthq_default');
  assert.equal(m.spoolmanUrl('acme-co'), 'http://ophq-spoolman-acme-co:8000');
});

test('enabled only with both an image and a Postgres password', () => {
  assert.equal(m.spoolmanEnabled(), true);
});

test('reconcile is a no-op without an instance', async () => {
  const r = await m.ensureSpoolman('');
  assert.equal(r.mode, 'internal');
  assert.equal(r.reason, 'no-instance');
});
