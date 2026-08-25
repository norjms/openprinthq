// Parity between the control-plane signer and the connector agent.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The two implementations live in different packages and are edited by
// different people. When they disagree the symptom is a clean "bad signature"
// rejection that looks like a key problem and costs a day to find.
//
// The agent is read as TEXT rather than imported, because importing runs it.
// Crude, but it fails loudly the moment someone changes one side without the
// other, which is the entire point.
//
// It reads the agent that actually SHIPS, from openprinthq-cloud-client. Until
// 2026-08-25 it read an in-repo copy at connector/src/agent.js that nothing
// deployed any more, so the parity it proved was with a file no user ran. CI
// fetches the shipped agent and points OPHQ_AGENT_SRC at it; see ci.yml.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isCommand } from '../src/signing.js';

const here = path.dirname(fileURLToPath(import.meta.url));
// Resolution order: an explicit path (CI sets this to the freshly fetched
// agent), then a sibling checkout of the client repo for local work. Failing
// loudly beats skipping: a parity test that quietly does not run is worse than
// no parity test, because it reads as a passing guarantee.
function resolveAgentSrc() {
  const explicit = process.env.OPHQ_AGENT_SRC;
  if (explicit) return explicit;
  const sibling = path.resolve(here, '../../../openprinthq-cloud-client/agent/src/agent.js');
  if (fs.existsSync(sibling)) return sibling;
  throw new Error(
    'Cannot find the connector agent to check parity against.\n' +
    'Set OPHQ_AGENT_SRC to a checkout of openprinthq-cloud-client/agent/src/agent.js, e.g.\n' +
    '  curl -sSL https://raw.githubusercontent.com/norjms/openprinthq-cloud-client/main/agent/src/agent.js -o /tmp/agent.js\n' +
    '  OPHQ_AGENT_SRC=/tmp/agent.js npm test'
  );
}
const agentSrc = fs.readFileSync(resolveAgentSrc(), 'utf8');
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
