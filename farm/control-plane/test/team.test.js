import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  teamMode, parseAccessMap, accessFor, inTeam, allows, onlyPrinter, printerIdFromPath,
  checkEngineRequest, filterEngineAnswer, filterPrinters, answerHidden, isAll
} from '../src/team.js';

const MAP = parseAccessMap({
  version: 1,
  people: { 'Ana@Shop.test': 'all', 'ben@shop.test': [2, '3'], 'cy@shop.test': [5], 'dee@shop.test': [] },
  owners: ['Owner@Shop.test']
}).map;

test('team mode follows OPHQ_TEAM_MODE', () => {
  delete process.env.OPHQ_TEAM_MODE; assert.equal(teamMode(), false);
  process.env.OPHQ_TEAM_MODE = '1'; assert.equal(teamMode(), true);
  process.env.OPHQ_TEAM_MODE = '0'; assert.equal(teamMode(), false);
  delete process.env.OPHQ_TEAM_MODE;
});

test('the pushed list is checked and normalised', () => {
  assert.deepEqual(MAP.people['ana@shop.test'], 'all');
  assert.deepEqual(MAP.people['ben@shop.test'], [2, 3]);
  assert.deepEqual(MAP.owners, ['owner@shop.test']);
  assert.match(parseAccessMap({ version: 2, people: {} }).error, /version/);
  assert.match(parseAccessMap({ version: 1, people: [] }).error, /people/);
  assert.match(parseAccessMap({ version: 1, people: { nope: 'all' } }).error, /email/);
  assert.match(parseAccessMap({ version: 1, people: { 'a@b.c': 'some' } }).error, /all/);
  assert.match(parseAccessMap({ version: 1, people: { 'a@b.c': [0] } }).error, /printer id/);
  assert.match(parseAccessMap({ version: 1, people: { 'a@b.c': ['x'] } }).error, /printer id/);
  assert.match(parseAccessMap(null).error, /object/);
});

test('owners get every printer; missing people get none', () => {
  assert.ok(isAll(accessFor(MAP, 'x@y.z', true)));
  assert.ok(isAll(accessFor(MAP, 'OWNER@shop.test', false)));
  assert.ok(isAll(accessFor(MAP, 'ana@shop.test', false)));
  assert.deepEqual([...accessFor(MAP, 'BEN@shop.test', false)], [2, 3]);
  assert.equal(accessFor(MAP, 'stranger@shop.test', false).size, 0);
  assert.equal(accessFor(null, 'ben@shop.test', false).size, 0);
  assert.ok(inTeam(MAP, 'dee@shop.test'));
  assert.ok(inTeam(MAP, 'owner@shop.test'));
  assert.ok(!inTeam(MAP, 'stranger@shop.test'));
});

test('allows and the one-printer case', () => {
  const ben = accessFor(MAP, 'ben@shop.test', false);
  assert.ok(allows(ben, 2)); assert.ok(allows(ben, '3')); assert.ok(!allows(ben, 4)); assert.ok(!allows(ben, 'x'));
  assert.equal(onlyPrinter(ben), null);
  assert.equal(onlyPrinter(accessFor(MAP, 'cy@shop.test', false)), 5);
  assert.equal(printerIdFromPath('/api/v1/printers/12/status?x=1'), 12);
  assert.equal(printerIdFromPath('/api/v1/printers/'), null);
});

test('engine requests: paths, queries, bodies', () => {
  const ben = accessFor(MAP, 'ben@shop.test', false);
  const ok = (r) => assert.equal(r.ok, true, JSON.stringify(r));
  const no = (r, status) => { assert.equal(r.ok, false); assert.equal(r.status, status); };
  ok(checkEngineRequest('all', { method: 'DELETE', path: '/api/v1/printers/9' }));
  ok(checkEngineRequest(ben, { method: 'GET', path: '/api/v1/printers/' }));
  no(checkEngineRequest(ben, { method: 'POST', path: '/api/v1/printers/', body: {} }), 403);
  ok(checkEngineRequest(ben, { method: 'GET', path: '/api/v1/printers/2/status' }));
  no(checkEngineRequest(ben, { method: 'GET', path: '/api/v1/printers/4/status' }), 404);
  no(checkEngineRequest(ben, { method: 'PATCH', path: '/api/v1/printers/2' }), 403);
  no(checkEngineRequest(ben, { method: 'DELETE', path: '/api/v1/printers/2/' }), 403);
  ok(checkEngineRequest(ben, { method: 'POST', path: '/api/v1/printers/2/control/pause', body: {} }));
  no(checkEngineRequest(ben, { method: 'GET', path: '/api/v1/archives/', search: 'printer_id=4' }), 404);
  ok(checkEngineRequest(ben, { method: 'GET', path: '/api/v1/archives/', search: 'printer_id=3' }));
  no(checkEngineRequest(ben, { method: 'POST', path: '/api/v1/queue/', body: { file_id: 1, printer_id: 9 } }), 404);
  no(checkEngineRequest(ben, { method: 'POST', path: '/api/v1/queue/5/assign', body: '{"nested":{"printer_ids":[2,7]}}' }), 404);
  no(checkEngineRequest(ben, { method: 'POST', path: '/api/v1/library/files/add-to-queue', body: { file_ids: [1] } }), 400);
  const cy = accessFor(MAP, 'cy@shop.test', false);
  const r = checkEngineRequest(cy, { method: 'POST', path: '/api/v1/library/files/add-to-queue', body: { file_ids: [1] } });
  assert.equal(r.ok, true); assert.equal(r.body.printer_id, 5);
  const mp = Buffer.from('--b\r\nContent-Disposition: form-data; name="printer_id"\r\n\r\n7\r\n--b--');
  no(checkEngineRequest(ben, { method: 'POST', path: '/api/v1/library/files', body: mp, isMultipart: true }), 404);
  ok(checkEngineRequest(ben, { method: 'POST', path: '/api/v1/library/files', body: Buffer.from('x'), isMultipart: true }));
});

test('engine answers are filtered', () => {
  const ben = accessFor(MAP, 'ben@shop.test', false);
  assert.deepEqual(filterEngineAnswer(ben, '/api/v1/printers/', [{ id: 1 }, { id: 2 }, { id: 3 }]).map((p) => p.id), [2, 3]);
  assert.deepEqual(filterEngineAnswer(ben, '/api/v1/printers', { printers: [{ id: 4 }, { id: 3 }] }).printers, [{ id: 3 }]);
  const q = filterEngineAnswer(ben, '/api/v1/queue/', [{ id: 1, printer_id: 2 }, { id: 2, printer_id: 9 }, { id: 3, printer_id: null }]);
  assert.deepEqual(q.map((x) => x.id), [1, 3]);
  const nested = filterEngineAnswer(ben, '/api/v1/archives/', { items: [{ printer_id: 9 }, { printer_id: 3 }], total: 2 });
  assert.deepEqual(nested.items, [{ printer_id: 3 }]);
  assert.deepEqual(Object.keys(filterEngineAnswer(ben, '/api/v1/printers/statuses', { 2: {}, 9: {} })), ['2']);
  assert.ok(answerHidden(ben, { id: 1, printer_id: 9 }));
  assert.ok(!answerHidden(ben, { id: 1, printer_id: 2 }));
  assert.ok(!answerHidden('all', { printer_id: 9 }));
  assert.deepEqual(filterPrinters(ben, [{ id: 2 }, { id: 8 }]), [{ id: 2 }]);
  assert.equal(filterEngineAnswer('all', '/api/v1/queue/', 'x'), 'x');
});
