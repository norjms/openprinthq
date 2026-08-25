// A connector's session being replaced must be visible, and repeated
// replacement must be named for what it is.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Twice now (2026-08-08, 2026-08-24) two agents holding one connector token
// evicted each other for hours. The mechanism was never in doubt once someone
// looked; the cost was that nothing said it out loud, so the search started at
// "why are the Bambu printers flapping offline" instead of "something is
// replacing this connector's session forty times a minute".
//
// These tests read connector.js as TEXT and evaluate only the eviction block,
// following the pattern in agent-parity.test.js: importing the module pulls in
// db.js, whose connection pools would keep the test runner alive. The tradeoff
// is that a rename can silently stop exercising the real code, so the locator
// below asserts loudly rather than skipping.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.resolve(here, '../src/connector.js'), 'utf8');

function loadEvictionBlock(sink) {
  // Starts at the identity block, not at EVICTION_WINDOW_MS: evictPrevious now
  // calls describeIdentity and sameHost to name the machine on each side, so
  // extracting the eviction code alone leaves those undefined.
  const start = src.indexOf('const IDENTITY_MAX_B64');
  const end = src.indexOf('\n\nexport function registerConnectorRoutes');
  assert.ok(start > -1, 'IDENTITY_MAX_B64 not found in connector.js — did the identity block move or get renamed?');
  assert.ok(end > start, 'registerConnectorRoutes not found after the eviction block');
  const block = src.slice(start, end).replace(/^export /gm, '');
  const make = new Function(
    'console', 'Buffer',
    `${block}\nreturn { evictPrevious, connectorEvictionCount, connectorHasDuplicateAgents, EVICTION_ALARM_COUNT };`
  );
  return make({ log: (m) => sink.push(m) }, Buffer);
}

const conn = { id: 10, user_id: 1 };
// A previous session as connector.js holds it. The interval is real so that
// clearInterval() has something to clear, and unref'd so it cannot hold the
// test runner open.
const priorSession = (name, transport) => ({
  name,
  transport,
  heartbeat: setInterval(() => {}, 1e6).unref(),
  closeSession() {},
  lastSeen: Date.now() - 1234
});

test('no previous session is a silent no-op', () => {
  const lines = [];
  const E = loadEvictionBlock(lines);
  E.evictPrevious(null, conn, { name: 'a', transport: 'ws' });
  assert.equal(lines.length, 0, 'a first connection should not log an eviction');
  assert.equal(E.connectorEvictionCount(conn.id), 0);
});

test('an eviction names both sessions, at info level, not behind a debug flag', () => {
  const lines = [];
  const E = loadEvictionBlock(lines);
  E.evictPrevious(priorSession('test3', 'sse'), conn, {
    name: 'test3', transport: 'ws', ip: '203.0.113.5'
  });
  assert.equal(lines.length, 1);
  const l = lines[0];
  assert.match(l, /connectorId=10/);
  assert.match(l, /outgoing=\{name:"test3",transport:sse,/);
  assert.match(l, /incoming=\{name:"test3",transport:ws,ip:203\.0\.113\.5,/);
  assert.match(l, /replacements_in_last_5min=1/, 'the running count is what turns one line into a pattern');
  // Agents predating identity reporting must be described as such rather than
  // left blank, so the line never reads as if the host were simply unknown.
  assert.match(l, /unidentified/);
});

test('an eviction names the machine on each side when the agents report it', () => {
  const lines = [];
  const E = loadEvictionBlock(lines);
  const idy = (host, install) => ({ hostname: host, install_id: install, pid: 7, version: '0.0.18', platform: 'win32', arch: 'x64' });
  E.evictPrevious(
    { ...priorSession('test3', 'ws'), identity: idy('DRYAN01', 'aaaaaaaaaaaa') },
    conn,
    { name: 'test3', transport: 'ws', ip: '203.0.113.5', identity: idy('DRYAN01', 'bbbbbbbbbbbb') }
  );
  const l = lines[0];
  assert.match(l, /host=DRYAN01/);
  // Two installs on one box (different install ids, one hostname) — a service
  // and the app, or a duplicate autostart entry. The line must say SAME HOST,
  // because telling someone to look for a second machine would waste their day.
  assert.match(l, /SAME HOST/);
});

test('two different machines are called out as such, because the fix differs', () => {
  const lines = [];
  const E = loadEvictionBlock(lines);
  const idy = (host) => ({ hostname: host, install_id: null, pid: 7 });
  E.evictPrevious(
    { ...priorSession('test3', 'ws'), identity: idy('DRYAN01') },
    conn,
    { name: 'test3', transport: 'ws', identity: idy('LAPTOP') }
  );
  assert.match(lines[0], /DIFFERENT HOSTS/);
});

test('a single replacement is not an alarm — a reconnecting agent does this', () => {
  const lines = [];
  const E = loadEvictionBlock(lines);
  E.evictPrevious(priorSession('test3', 'ws'), conn, { name: 'test3', transport: 'ws' });
  assert.ok(!lines.some((l) => l.includes('DUPLICATE AGENT ALARM')));
  assert.equal(E.connectorHasDuplicateAgents(conn.id), false);
});

test('sustained replacement raises the alarm once, and says what to do', () => {
  const lines = [];
  const E = loadEvictionBlock(lines);
  for (let i = 0; i < E.EVICTION_ALARM_COUNT + 2; i++) {
    E.evictPrevious(priorSession('test3', 'ws'), conn, { name: 'test3', transport: 'ws' });
  }
  const alarms = lines.filter((l) => l.includes('DUPLICATE AGENT ALARM'));
  assert.equal(alarms.length, 1, 'the alarm must not repeat on every subsequent eviction');
  // The alarm has to bridge symptom to cause, because the symptom is what the
  // operator arrives with.
  assert.match(alarms[0], /printers flapping offline/);
  assert.match(alarms[0], /two agents sharing one connector token/);
  assert.equal(E.connectorHasDuplicateAgents(conn.id), true);
  assert.equal(E.connectorEvictionCount(conn.id), E.EVICTION_ALARM_COUNT + 2);
});

test('connectors are counted independently', () => {
  const lines = [];
  const E = loadEvictionBlock(lines);
  for (let i = 0; i < E.EVICTION_ALARM_COUNT + 1; i++) {
    E.evictPrevious(priorSession('a', 'ws'), conn, { name: 'a', transport: 'ws' });
  }
  assert.equal(E.connectorHasDuplicateAgents(conn.id), true);
  assert.equal(E.connectorEvictionCount(99), 0, 'an unrelated connector must not inherit the alarm');
  assert.equal(E.connectorHasDuplicateAgents(99), false);
});

test('the eviction helper is actually wired into both transports', () => {
  // The counter is worthless if a transport still tears down the old session by
  // hand. Both call sites must go through evictPrevious.
  const handRolled = src.match(/if \(prev\) \{ try \{ clearInterval\(prev\.heartbeat\); prev\.closeSession/g) || [];
  assert.equal(handRolled.length, 0, 'a transport is still evicting the previous session directly, bypassing the log and the counter');
  const wired = src.match(/evictPrevious\(prev, conn, \{/g) || [];
  assert.equal(wired.length, 2, 'expected both the SSE and websocket transports to call evictPrevious');
});
