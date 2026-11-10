// Single-tenant mode: one engine and one Spoolman by URL, no Docker.
import test from 'node:test';
import assert from 'node:assert/strict';

const t = await import('../src/tenancy.js');

function withEnv(env, fn) {
  const saved = {};
  for (const k of Object.keys(env)) { saved[k] = process.env[k]; if (env[k] == null) delete process.env[k]; else process.env[k] = env[k]; }
  try { return fn(); } finally {
    for (const k of Object.keys(saved)) { if (saved[k] === undefined) delete process.env[k]; else process.env[k] = saved[k]; }
  }
}

test('without OPHQ_ENGINE_URL each tenant has its own engine container', () => {
  withEnv({ OPHQ_ENGINE_URL: null, OPHQ_SPOOLMAN_URL: 'http://spoolman:8000' }, () => {
    assert.equal(t.singleTenant(), false);
    assert.equal(t.engineBaseFor('acme'), 'http://ophq-acme:8000');
    assert.equal(t.engineBaseOf({ subdomain: 'acme' }, 9000), 'http://ophq-acme:9000');
    assert.equal(t.externalSpoolmanUrl(), '', 'a Spoolman URL means nothing outside single-tenant mode');
  });
});

test('with OPHQ_ENGINE_URL every tenant shares that engine', () => {
  withEnv({ OPHQ_ENGINE_URL: 'http://openprinthq-engine:8000/', OPHQ_SPOOLMAN_URL: 'http://spoolman:8000/' }, () => {
    assert.equal(t.singleTenant(), true);
    assert.equal(t.engineBaseFor('acme'), 'http://openprinthq-engine:8000');
    assert.equal(t.engineBaseFor('other'), 'http://openprinthq-engine:8000');
    assert.equal(t.externalSpoolmanUrl(), 'http://spoolman:8000');
  });
});

test('no instance, no engine, in either mode', () => {
  withEnv({ OPHQ_ENGINE_URL: 'http://e:8000' }, () => {
    assert.equal(t.engineBaseOf(null), null);
    assert.equal(t.engineBaseFor(''), null);
  });
  withEnv({ OPHQ_ENGINE_URL: null }, () => assert.equal(t.engineBaseOf({}), null));
});

test('single-tenant Spoolman is used by URL and never started', async () => {
  process.env.OPHQ_ENGINE_URL = 'http://openprinthq-engine:8000';
  process.env.OPHQ_SPOOLMAN_URL = 'http://spoolman:8000';
  delete process.env.OPHQ_SPOOLMAN_IMAGE;
  const s = await import('../src/spoolman.js');
  assert.equal(s.spoolmanEnabled(), true, 'enabled by the URL alone, no image or Postgres password');
  assert.equal(s.spoolmanUrl('acme'), 'http://spoolman:8000');
  delete process.env.OPHQ_SPOOLMAN_URL;
  assert.equal(s.spoolmanEnabled(), false, 'single-tenant without a Spoolman URL keeps the built-in inventory');
  delete process.env.OPHQ_ENGINE_URL;
});

test('the single-tenant paths never run docker', async () => {
  // A static guarantee: every docker call in the modules that start containers
  // sits behind a singleTenant() check, or in a function only multi-tenant code reaches.
  const { readFile } = await import('node:fs/promises');
  const src = async (f) => readFile(new URL(`../src/${f}`, import.meta.url), 'utf8');
  assert.doesNotMatch(await src('go2rtc.js'), /docker/, 'camera registration reads the engine over HTTP');
  assert.doesNotMatch(await src('cameras.js'), /child_process/);
  assert.match(await src('routing.js'), /async function writeLanProxyMap[\s\S]{0,400}if \(singleTenant\(\)\) return;/);
  const prov = await src('provisioner.js');
  assert.match(prov, /export async function ensureEngineBucketMount[^{]*\{\s*if \(singleTenant\(\)\)/);
  assert.match(prov, /export async function ensureVault[^{]*\{\s*if \(singleTenant\(\)\)/);
  assert.match(prov, /export async function joinVaultNetwork[^{]*\{\s*if \(!vaultEnabled\(\)\)/);
  assert.match(prov, /if \(singleTenant\(\)\) \{\s*configureEngine\(subdomain\);/);
  assert.match(await src('spoolman.js'), /const p = \(async \(\) => \{\s*if \(singleTenant\(\)\) \{/);
});
