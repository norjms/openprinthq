import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveTheme, applyTheme, DEFAULT_THEME } from '../src/lib/themes.js';

test('old hub skins and unknown values resolve to glass-dark', () => {
  for (const v of ['legacy_farm', 'openprinthq', 'classic', '', undefined, 'light']) assert.equal(resolveTheme(v), DEFAULT_THEME);
  assert.equal(resolveTheme('glass-dark'), 'glass-dark');
});

test('applyTheme sets the attributes apps read', () => {
  const el = { dataset: {} };
  applyTheme(el, 'classic', 'off');
  assert.deepEqual(el.dataset, { theme: 'glass-dark', transparency: 'off' });
  applyTheme(el, 'glass-dark');
  assert.deepEqual(el.dataset, { theme: 'glass-dark' });
});
