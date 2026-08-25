// Client identity: who is actually on the other end of this connector.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Self-reported and trivially forgeable, so it is decoration, never
// authentication — that remains the token plus the pinned client key. These
// tests hold the line on two things: a hostile or malformed header must cost
// nothing, and the same-host distinction must be right, because same host and
// different hosts need opposite fixes.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.resolve(here, '../src/connector.js'), 'utf8');

// Same text-extraction approach as the sibling suites: importing connector.js
// pulls in db.js, whose pools keep the runner alive.
function load(sink) {
  const start = src.indexOf('const IDENTITY_MAX_B64');
  const end = src.indexOf('\n\nexport function registerConnectorRoutes');
  assert.ok(start > -1, 'identity block not found — renamed or moved?');
  assert.ok(end > start, 'registerConnectorRoutes not found after the identity block');
  const block = src.slice(start, end).replace(/^export /gm, '');
  const make = new Function('console', 'Buffer',
    `${block}\nreturn { parseClientIdentity, describeIdentity, sameHost };`);
  return make({ log: (m) => sink.push(m) }, Buffer);
}

const hdr = (o) => ({ headers: { 'x-ophq-client-identity': Buffer.from(JSON.stringify(o)).toString('base64') } });
const IDY = {
  install_id: 'a1b2c3d4e5f6', hostname: 'DRYAN01', pid: 4242,
  platform: 'win32', arch: 'x64', version: '0.0.18', node: '22.0.0',
  started_at: '2026-08-25T12:00:00.000Z'
};

test('a well-formed identity round-trips', () => {
  const M = load([]);
  const got = M.parseClientIdentity(hdr(IDY));
  assert.equal(got.hostname, 'DRYAN01');
  assert.equal(got.install_id, 'a1b2c3d4e5f6');
  assert.equal(got.pid, 4242);
  assert.equal(got.version, '0.0.18');
});

test('a missing header is not an error, just absent', () => {
  const M = load([]);
  assert.equal(M.parseClientIdentity({ headers: {} }), null);
  assert.equal(M.parseClientIdentity({}), null);
  assert.equal(M.parseClientIdentity(undefined), null);
});

test('garbage never throws', () => {
  const M = load([]);
  for (const bad of ['not-base64!!', Buffer.from('{oh no').toString('base64'),
                     Buffer.from('"a string"').toString('base64'),
                     Buffer.from('null').toString('base64'), '', 12345]) {
    assert.doesNotThrow(() => M.parseClientIdentity({ headers: { 'x-ophq-client-identity': bad } }));
  }
});

test('an oversized header is refused outright rather than decoded', () => {
  const M = load([]);
  const huge = 'A'.repeat(5000);
  assert.equal(M.parseClientIdentity({ headers: { 'x-ophq-client-identity': huge } }), null);
});

test('fields are clamped, so a hostile agent cannot flood the logs', () => {
  // Stays under the whole-header cap on purpose: that cap already rejects the
  // enormous case, and this is about the merely-too-long one that gets through
  // it and would otherwise reach a log line.
  const M = load([]);
  const got = M.parseClientIdentity(hdr({ ...IDY, hostname: 'h'.repeat(400), version: 'v'.repeat(200) }));
  assert.ok(got, 'a header under the size cap must still parse');
  assert.equal(got.hostname.length, 128);
  assert.equal(got.version.length, 32);
});

test('a non-numeric pid becomes null instead of leaking a string into the log', () => {
  const M = load([]);
  assert.equal(M.parseClientIdentity(hdr({ ...IDY, pid: '; rm -rf /' })).pid, null);
});

test('describeIdentity says so when an agent is too old to report', () => {
  const M = load([]);
  assert.match(M.describeIdentity(null), /unidentified/);
  assert.match(M.describeIdentity(null), /predates/);
});

test('describeIdentity leads with the host, which is the useful part', () => {
  const M = load([]);
  const d = M.describeIdentity(IDY);
  assert.match(d, /^host=DRYAN01/);
  assert.match(d, /install=a1b2c3d4e5f6/);
  assert.match(d, /pid=4242/);
});

test('same install id means the same machine', () => {
  const M = load([]);
  assert.equal(M.sameHost(IDY, { ...IDY, pid: 999 }), true);
});

test('two installs on one machine are still one machine', () => {
  // The commonest cause of this whole fault: a background service and the
  // desktop app on one box, each with its own client key and so its own install
  // id. Calling that "two machines" would send someone hunting a second
  // computer that does not exist.
  const M = load([]);
  assert.equal(M.sameHost(IDY, { ...IDY, install_id: 'ffffffffffff' }), true);
});

test('a different hostname is what makes it two machines', () => {
  const M = load([]);
  assert.equal(M.sameHost(IDY, { ...IDY, install_id: 'ffffffffffff', hostname: 'LAPTOP' }), false);
});

test('the same install id settles it even without hostnames', () => {
  const M = load([]);
  const a = { install_id: 'a1b2c3d4e5f6', hostname: null };
  assert.equal(M.sameHost(a, { ...a }), true);
});

test('hostname is the fallback when an install id is missing', () => {
  const M = load([]);
  const a = { ...IDY, install_id: null };
  assert.equal(M.sameHost(a, { ...a }), true);
  assert.equal(M.sameHost(a, { ...a, hostname: 'OTHERBOX' }), false);
});

test('unknowable is null, not a guess', () => {
  // An agent predating identity reporting must not be reported as "same host"
  // or "different hosts" — either would send someone looking in the wrong place.
  const M = load([]);
  assert.equal(M.sameHost(null, IDY), null);
  assert.equal(M.sameHost(IDY, null), null);
  assert.equal(M.sameHost({ install_id: null, hostname: null }, IDY), null);
});

test('identity is parsed on both transports', () => {
  const wired = src.match(/const identity = parseClientIdentity\(req\);/g) || [];
  assert.equal(wired.length, 2, 'expected both the SSE and websocket transports to parse identity');
  const stored = src.match(/transport: '(sse|ws)', identity,/g) || [];
  assert.equal(stored.length, 2, 'both sessions must keep the identity for the API to report it');
});
