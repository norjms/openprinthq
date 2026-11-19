// SPDX-License-Identifier: AGPL-3.0-or-later
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, stat, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  addonFor, addonVersion, readAddon, originOf, orcaPrinterPreset, presetName, desktopPrinters, writeConnection,
  desktopConnection, dropPendingConnection, CONNECTION_FILE, APPLIED_FILE
} from '../src/orcaslicer.js';

test('the add-on ships in the image, declared for this farm', async () => {
  const src = await readAddon();
  assert.match(addonVersion(src), /^\d+\.\d+\.\d+$/);
  assert.match(src, /SPDX-License-Identifier: AGPL-3\.0-or-later/);
  const out = addonFor(src, 'https://hub.example.com/apps/farm');
  assert.match(out, /^DECLARED_NETWORK = \("https:\/\/openprinthq\.com", "https:\/\/hub\.example\.com"\)$/m);
  assert.equal(out.length - src.length, ' "https://hub.example.com"'.length);
  // The managed-install support the desktop relies on (0.15.0).
  assert.match(src, /OPENPRINTHQ_URL/);
  assert.match(src, /OPENPRINTHQ_API_KEY/);
});

test('the declaration is only ever an http(s) origin', () => {
  const src = 'x\nDECLARED_NETWORK = ("https://openprinthq.com",)\ny\n';
  assert.equal(addonFor(src, 'https://openprinthq.com/x'), src);
  assert.equal(addonFor(src, 'ftp://evil'), src);
  assert.equal(addonFor(src, 'not a url'), src);
  assert.match(addonFor(src, 'http://hub.lan:8080/apps/farm?a"b'), /"http:\/\/hub\.lan:8080"\)/);
  assert.throws(() => addonFor('no line', 'https://a.b'), /DECLARED_NETWORK/);
  assert.equal(originOf('javascript:alert(1)'), null);
});

test('farm printers become OrcaSlicer printers where OrcaSlicer has one', () => {
  assert.equal(orcaPrinterPreset({ name: 'H2D', model: 'H2D', connection_type: 'bambu' }), 'Bambu Lab H2D 0.4 nozzle');
  assert.equal(orcaPrinterPreset({ name: 'Left', model: 'Bambu Lab X1C', connection_type: 'bambu' }), 'Bambu Lab X1 Carbon 0.4 nozzle');
  assert.equal(orcaPrinterPreset({ name: 'a', model: 'A1 mini', connection_type: 'bambu' }), 'Bambu Lab A1 mini 0.4 nozzle');
  assert.equal(orcaPrinterPreset({ name: 'MK4S', model: null, connection_type: 'prusalink' }), 'Prusa MK4S 0.4 nozzle');
  assert.equal(orcaPrinterPreset({ name: 'MK3S', model: null, connection_type: 'octoprint' }), 'Prusa MK3S 0.4 nozzle');
  assert.equal(orcaPrinterPreset({ name: 'shop MK4', connection_type: 'prusalink' }), 'Prusa MK4 0.4 nozzle');
  assert.equal(orcaPrinterPreset({ name: 'v', model: 'Voron 2.4 300', connection_type: 'klipper' }), 'Voron 2.4 300 0.4 nozzle');
  // Not sure which: left for the person to set up.
  assert.equal(orcaPrinterPreset({ name: 'voron01', model: 'Voron 2.4', connection_type: 'klipper' }), null);
  assert.equal(orcaPrinterPreset({ name: 'Ender', model: 'Ender 3', connection_type: 'octoprint' }), null);
  assert.equal(orcaPrinterPreset({ name: 'MK3.5', connection_type: 'prusalink' }), 'Prusa MK3.5 0.4 nozzle');
});

test('preset names are safe file names that name the printer', () => {
  assert.equal(presetName('H2D'), 'H2D - OpenPrintHQ Farm');
  assert.equal(presetName('a/b:c'), 'a-b-c - OpenPrintHQ Farm');
  assert.equal(presetName('..hidden'), 'hidden - OpenPrintHQ Farm');
  assert.equal(presetName(''), null);
  assert.equal(presetName('x'.repeat(90)).length, 60 + ' - OpenPrintHQ Farm'.length);
  const d = desktopPrinters([
    { id: 1, name: 'H2D', model: 'H2D', connection_type: 'bambu' },
    { id: 5, name: 'voron01', model: 'Voron 2.4', connection_type: 'klipper' },
    { id: 6, name: 'old', model: 'H2C', connection_type: 'bambu', is_active: false }
  ]);
  assert.deepEqual(d.printers, [{ id: 1, name: 'H2D', preset: 'H2D - OpenPrintHQ Farm', inherits: 'Bambu Lab H2D 0.4 nozzle' }]);
  assert.deepEqual(d.skipped, [{ id: 5, name: 'voron01' }]);
});

test('the connection file is the owner\'s only, and the status never shows its key', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'ophq-desk-'));
  assert.deepEqual(await desktopConnection(dir), { connected: false, pending: false, error: null, at: null, printers: [], skipped: [], scope: null, key: null });
  await writeConnection(dir, { url: 'http://h:8480/apps/farm', key: 'ophqk_secret', scope: 'submit', token_id: 't1', actor_id: 7,
    printers: [{ id: 1, name: 'H2D', preset: 'H2D - OpenPrintHQ Farm', inherits: 'x' }], skipped: [{ id: 5, name: 'v' }] });
  const st = await stat(join(dir, CONNECTION_FILE));
  assert.equal(st.mode & 0o777, 0o600);
  assert.deepEqual((await readdir(dir)).sort(), [CONNECTION_FILE]);   // no temporary file left behind
  const c = await desktopConnection(dir);
  assert.equal(c.pending, true);
  assert.equal(c.connected, false);
  assert.deepEqual(c.key, { actorId: 7, tokenId: 't1' });
  assert.ok(!JSON.stringify({ ...c, key: undefined }).includes('ophqk_secret'));
  assert.equal(JSON.parse(await readFile(join(dir, CONNECTION_FILE), 'utf8')).schema, 1);
  // The desktop takes it: the file goes, the marker (no key) stays.
  await dropPendingConnection(dir);
  await writeFile(join(dir, APPLIED_FILE), JSON.stringify({ schema: 1, applied_at: '2026-11-19T17:00:00Z', token_id: 't1', actor_id: 7,
    printers: [{ name: 'H2D', preset: 'H2D - OpenPrintHQ Farm' }], skipped: [], scope: 'submit' }));
  const after = await desktopConnection(dir);
  assert.equal(after.connected, true);
  assert.equal(after.pending, false);
  assert.equal(after.at, '2026-11-19T17:00:00Z');
  assert.deepEqual(after.printers, [{ name: 'H2D', preset: 'H2D - OpenPrintHQ Farm' }]);
  assert.equal(await desktopConnection(null), null);
  // A connection the desktop could not take says why, and is not connected.
  await writeFile(join(dir, APPLIED_FILE), JSON.stringify({ schema: 1, error: 'the add-on could not be installed' }));
  const bad = await desktopConnection(dir);
  assert.equal(bad.connected, false);
  assert.equal(bad.error, 'the add-on could not be installed');
});
