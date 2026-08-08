// Parity between the control-plane signer and the connector agent.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The two implementations live in different packages and are edited by
// different people. When they disagree the symptom is a clean "bad signature"
// rejection that looks like a key problem and costs a day to find.
//
// These tests read connector/src/agent.js as TEXT rather than importing it,
// because importing runs the agent. Crude, but it fails loudly the moment
// someone changes one side without the other, which is the entire point.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isCommand } from '../src/signing.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const agentSrc = fs.readFileSync(path.resolve(here, '../../connector/src/agent.js'), 'utf8');
const signingSrc = fs.readFileSync(path.resolve(here, '../src/signing.js'), 'utf8');

function streamKindsIn(src) {
  const m = /const STREAM_KINDS = new Set\(\[([^\]]*)\]\)/.exec(src);
  assert.ok(m, 'STREAM_KINDS declaration not found');
  return m[1].split(',').map((s) => s.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean).sort();
}

test('agent and control-plane exempt exactly the same job kinds from signing', () => {
  assert.deepEqual(streamKindsIn(agentSrc), streamKindsIn(signingSrc));
});

test('the exempt set is still just tunnel traffic', () => {
  // Anything else added here stops being verified. If this fails, the change
  // needs a reason in the commit message, not a green test.
  assert.deepEqual(streamKindsIn(signingSrc), ['tcp-close', 'tcp-data']);
  assert.equal(isCommand({ kind: 'tcp-data' }), false);
  assert.equal(isCommand({ kind: 'discover' }), true);
});

test('agent canonJob field order matches the control-plane', () => {
  const order = (src) => {
    const m = /canonJob\(j\) \{\s*return Buffer\.from\(JSON\.stringify\(\[([\s\S]*?)\]\)\)/.exec(src);
    assert.ok(m, 'canonJob body not found');
    return m[1].replace(/\s+/g, ' ').trim();
  };
  assert.equal(order(agentSrc), order(signingSrc));
});

test('agent verification fails closed when no key is pinned', () => {
  // Guards the specific regression: `if (!signPubKey) return true` shipped for
  // months and made the default configuration execute unauthenticated commands.
  assert.match(agentSrc, /if \(!signPubKey\) return CONFIG\.allowUnsigned;/);
  assert.doesNotMatch(agentSrc, /if \(!signPubKey\) \{[\s\S]{0,200}return true;/);
});

test('the isCommand check precedes the key check in the agent', () => {
  // Stream traffic must stay exempt regardless of key state, or a fail-closed
  // agent with no key would tear down every tunnel instead of just refusing
  // new commands.
  const body = /function verifyCommand\(job\) \{([\s\S]*?)\n\}/.exec(agentSrc)[1];
  assert.ok(body.indexOf('isCommand(job)') < body.indexOf('signPubKey'), 'isCommand must be checked first');
});

test('agent refuses to start unsigned without an explicit opt-out', () => {
  assert.match(agentSrc, /if \(!signPubKey && !CONFIG\.allowUnsigned\) \{[\s\S]*?fail\(/);
});

test('agent refuses a signing key that differs from the pinned one', () => {
  assert.match(agentSrc, /pinnedFp !== server\.fp/);
});
