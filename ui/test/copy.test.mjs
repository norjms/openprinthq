import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const root = new URL('..', import.meta.url).pathname;
const files = execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard'], { cwd: root, encoding: 'utf8' }).split('\n').filter(Boolean);
const text = files.filter((f) => !/\.(woff2?|png|jpg)$/.test(f) && !f.startsWith('package-lock'));

test('no em dashes in any file', () => {
  const dash = String.fromCharCode(0x2014);
  for (const f of text) assert.ok(!readFileSync(root + f, 'utf8').includes(dash), f);
});
