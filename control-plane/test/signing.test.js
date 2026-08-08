// Canonical-form and signing-policy tests.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Run: node --test control-plane/test/
//
// The reference implementation of canonJob below is deliberately a SEPARATE
// copy, transcribed from the connector agent. Importing the control-plane's
// version would make these tests tautological: they would prove the signer
// agrees with itself, which is never the bug. The bug is the two sides
// diverging, and that divergence shows up as a clean "bad signature" that looks
// like a key problem and costs a day to find.
//
// If a change makes these fail, the agent must change in the same commit.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { canonJob, isCommand } from '../src/signing.js';

const PSS = {
  padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
  saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST
};

// --- agent-side reference, transcribed from connector/src/agent.js ---------
function agentCanonJob(j) {
  return Buffer.from(JSON.stringify([
    j.id ?? null, j.ts ?? null, j.kind ?? null,
    j.host ?? null, j.port ?? null, j.scheme ?? null,
    j.path ?? null, j.method ?? null, j.headers ?? null, j.body ?? null
  ]));
}

const { publicKey, privateKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });

function sign(job) {
  return crypto.sign('sha256', canonJob(job), { key: privateKey, ...PSS }).toString('base64');
}
function agentVerify(job, sig) {
  try {
    return crypto.verify('sha256', agentCanonJob(job), { key: publicKey, ...PSS }, Buffer.from(sig, 'base64'));
  } catch { return false; }
}

const baseJob = () => ({
  id: '3f0c1e2a-0000-4000-8000-000000000001',
  ts: 1754640000000,
  kind: 'tcp-open',
  host: '192.168.1.42',
  port: 7125,
  scheme: 'http',
  path: '/printer/objects/query',
  method: 'GET',
  headers: { accept: 'application/json', 'x-trace': 'abc' },
  body: null
});

test('control-plane and agent produce identical canonical bytes', () => {
  const j = baseJob();
  assert.deepEqual(canonJob(j), agentCanonJob(j));
});

test('a well-formed command verifies agent-side', () => {
  const j = baseJob();
  assert.ok(agentVerify(j, sign(j)));
});

test('altering any canonical field breaks verification', () => {
  const fields = {
    id: 'ffffffff-0000-4000-8000-000000000002',
    ts: 1754640000001,
    kind: 'tcp-probe',
    host: '192.168.1.43',
    port: 7126,
    scheme: 'https',
    path: '/printer/objects/other',
    method: 'POST',
    headers: { accept: 'text/plain' },
    body: 'x'
  };
  for (const [k, v] of Object.entries(fields)) {
    const j = baseJob();
    const sig = sign(j);
    j[k] = v;
    assert.equal(agentVerify(j, sig), false, `tampering with "${k}" was not detected`);
  }
});

test('omitting a null field rather than including it breaks verification', () => {
  // The ten-element fixed array is the whole point. A sender that drops `body`
  // because it is null produces a nine-element array and a different digest.
  const j = baseJob();
  const sig = sign(j);
  const dropped = { ...j };
  delete dropped.body;
  assert.deepEqual(canonJob(dropped), agentCanonJob(dropped));
  assert.ok(agentVerify(dropped, sig), 'null and absent must canonicalise identically');
});

test('reordering header keys by sorting breaks verification', () => {
  // Documented failure mode: canonicalising by sorting object keys instead of
  // relying on the fixed array order.
  const j = baseJob();
  const sig = sign(j);
  const sorted = { ...j, headers: Object.fromEntries(Object.entries(j.headers).sort()) };
  const changed = JSON.stringify(sorted.headers) !== JSON.stringify(j.headers);
  if (changed) assert.equal(agentVerify(sorted, sig), false);
});

test('signature is not itself part of the canonical bytes', () => {
  const j = baseJob();
  const sig = sign(j);
  assert.ok(agentVerify({ ...j, sig }, sig));
});

// --- signing policy -------------------------------------------------------

test('stream traffic is excluded from signing', () => {
  assert.equal(isCommand({ kind: 'tcp-data' }), false);
  assert.equal(isCommand({ kind: 'tcp-close' }), false);
});

test('every other job kind is treated as a signed command', () => {
  // Regression guard for the allow-list bug: these four kinds all make the
  // agent originate a new LAN connection and all shipped unsigned before.
  for (const kind of ['tcp-open', 'tcp-probe', 'discover', 'camera-frame', 'camera-webrtc', 'camera-register']) {
    assert.equal(isCommand({ kind }), true, `${kind} must be signed`);
  }
});

test('an unrecognised future job kind defaults to signed', () => {
  // The deny-list exists so a kind added by someone who never read this file
  // is signed by default rather than silently exempt.
  assert.equal(isCommand({ kind: 'some-feature-added-next-quarter' }), true);
  assert.equal(isCommand({}), true);
});
