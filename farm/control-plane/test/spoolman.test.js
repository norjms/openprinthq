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

test('the egress network is per tenant and is neither the private nor the shared network', async () => {
  const m = await import('../src/spoolman.js');
  const e = m.spoolmanEgressNet('acme');
  assert.equal(e, 'ophq-spoolman-egress-acme');
  assert.notEqual(e, m.spoolmanNet('acme'));
  assert.notEqual(e, process.env.OPHQ_DOCKER_NETWORK || 'openprinthq_default');
  assert.notEqual(m.spoolmanEgressNet('acme'), m.spoolmanEgressNet('other'));
});

test('Spoolman takes the engine resolvers, and a container without them is not current', async () => {
  const m = await import('../src/spoolman.js');
  assert.deepEqual(m.dnsArgs([]), []);
  assert.deepEqual(m.dnsArgs(['1.1.1.1', '1.0.0.1']), ['--dns', '1.1.1.1', '--dns', '1.0.0.1', '--dns-search', '.']);
  assert.equal(m.dnsCurrent([], []), true);                       // none configured: anything goes
  assert.equal(m.dnsCurrent(null, ['1.1.1.1']), false);           // configured but missing: recreate
  assert.equal(m.dnsCurrent(['1.1.1.1', '1.0.0.1'], ['1.1.1.1', '1.0.0.1']), true);
  assert.equal(m.dnsCurrent(['8.8.8.8'], ['1.1.1.1']), false);
  const prev = process.env.OPHQ_ENGINE_DNS;
  process.env.OPHQ_ENGINE_DNS = '1.1.1.1, 1.0.0.1';
  assert.deepEqual(m.spoolmanDns(), ['1.1.1.1', '1.0.0.1']);
  if (prev === undefined) delete process.env.OPHQ_ENGINE_DNS; else process.env.OPHQ_ENGINE_DNS = prev;
});
