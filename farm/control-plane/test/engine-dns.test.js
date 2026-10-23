import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseEngineDns, engineDnsArgs } from '../src/engine-dns.js';

test('parses space and comma separated servers', () => {
  assert.deepEqual(parseEngineDns(' 192.0.2.53, 198.51.100.53 '), ['192.0.2.53', '198.51.100.53']);
  assert.deepEqual(parseEngineDns(''), []);
  assert.deepEqual(parseEngineDns(undefined), []);
});

test('configured servers win and clear the search domain', () => {
  const inspect = { HostConfig: { Dns: ['203.0.113.1'], DnsSearch: ['example.com'] } };
  assert.deepEqual(engineDnsArgs(['192.0.2.53'], inspect),
    ['--dns', '192.0.2.53', '--dns-search', '.']);
});

test('unset carries the container settings forward', () => {
  const inspect = { HostConfig: { Dns: ['192.0.2.53', '198.51.100.53'], DnsSearch: ['.'] } };
  assert.deepEqual(engineDnsArgs([], inspect),
    ['--dns', '192.0.2.53', '--dns', '198.51.100.53', '--dns-search', '.']);
});

test('unset with nothing to carry adds nothing', () => {
  assert.deepEqual(engineDnsArgs([]), []);
  assert.deepEqual(engineDnsArgs([], { HostConfig: { Dns: null, DnsSearch: null } }), []);
});
