import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, stat, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { desktopSettings, inboxFor, inboxName, sendToInbox } from '../src/desktop.js';

test('a file goes to the shared desktop, or to the person\'s own and nobody else\'s', () => {
  assert.equal(inboxFor(desktopSettings({}), 'ana'), null);
  const shared = desktopSettings({ OPHQ_ORCASLICER_INBOX: '/srv/inbox/' });
  assert.equal(inboxFor(shared, 'ana'), '/srv/inbox/shared');
  assert.equal(inboxFor(shared, null), '/srv/inbox/shared');
  const people = desktopSettings({ OPHQ_ORCASLICER_INBOX: '/srv/inbox', OPHQ_ORCASLICER_DESKTOPS: 'per-person' });
  assert.equal(inboxFor(people, 'Ana'), '/srv/inbox/people/ana');
  for (const bad of [null, '', '../ben', 'a/b', 'a..b', '.ana', 'ana b']) {
    assert.equal(inboxFor(people, bad), null, String(bad));
  }
});

test('a name keeps its model extension and cannot leave the folder or look like a marker', () => {
  assert.equal(inboxName('Witch_Hello_Kitty_0.2mm.3mf'), 'Witch_Hello_Kitty_0.2mm.3mf');
  assert.equal(inboxName('../../etc/cube.STL'), 'cube.STL');
  assert.equal(inboxName('.open-x.3mf'), 'open-x.3mf');
  assert.equal(inboxName('notes.txt'), null);
  assert.equal(inboxName(''), null);
  assert.equal(inboxName('a'.repeat(300) + '.3mf').length, 180);
});

test('the file is written whole, then its marker, both open to the desktop\'s user', async () => {
  const root = await mkdtemp(join(tmpdir(), 'inbox-'));
  try {
    const dir = join(root, 'people', 'ana');
    await sendToInbox(dir, 'cube.3mf', Buffer.from('model'));
    assert.deepEqual((await readdir(dir)).sort(), ['.open-cube.3mf', 'cube.3mf']);
    assert.equal(await readFile(join(dir, 'cube.3mf'), 'utf8'), 'model');
    assert.equal((await stat(dir)).mode & 0o777, 0o777);
    assert.equal((await stat(join(dir, 'cube.3mf'))).mode & 0o777, 0o666);
  } finally { await rm(root, { recursive: true, force: true }); }
});
