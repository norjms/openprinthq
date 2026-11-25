import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const root = new URL('..', import.meta.url).pathname;
const tokens = JSON.parse(readFileSync(root + 'dist/tokens.json', 'utf8'));
const hashDist = () => Object.fromEntries(readdirSync(root + 'dist').sort().map((f) => [f, createHash('sha256').update(readFileSync(root + 'dist/' + f)).digest('hex')]));

test('the build is deterministic', () => {
  const a = hashDist();
  execFileSync('node', [root + 'scripts/build.mjs']);
  assert.deepEqual(hashDist(), a);
});

test('the generated files match the committed golden copies', () => {
  for (const f of readdirSync(root + 'dist')) {
    assert.equal(readFileSync(root + 'dist/' + f, 'utf8'), readFileSync(root + 'test/golden/' + f, 'utf8'), f + ' differs from test/golden; rerun the build and review');
  }
});

// WCAG contrast, compositing translucent colours over their ground.
const parse = (v) => {
  let m = v.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (m) return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16), 1];
  m = v.match(/^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/);
  return [+m[1], +m[2], +m[3], +m[4]];
};
const over = (top, under) => { const [r, g, b, a] = parse(top); return [0, 1, 2].map((i) => [r, g, b][i] * a + under[i] * (1 - a)); };
const lum = (c) => { const l = c.map((x) => { x /= 255; return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]; };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const grounds = ['page', 'page-mid', 'page-top'].map((n) => [n, parse(tokens[n]).slice(0, 3)]);

test('text on glass passes 4.5:1 over every page stop', () => {
  for (const [gname, g] of grounds) {
    const card = over(tokens.glass, g);
    for (const t of ['text', 'text-2', 'text-3', 'group-label', 'link']) {
      const r = ratio(over(tokens[t], card), card);
      assert.ok(r >= 4.5, `${t} on glass over ${gname}: ${r.toFixed(2)}`);
    }
    const large = ratio(over(tokens['text-3-large'], card), card);
    assert.ok(large >= 3, `text-3-large over ${gname}: ${large.toFixed(2)}`);
  }
});

test('chips, buttons and the selected pill pass 4.5:1', () => {
  const card = over(tokens.glass, parse(tokens['page-top']).slice(0, 3));
  for (const k of ['ok', 'warn', 'bad', 'live']) {
    const bg = over(tokens[`chip-${k}-bg`], over(tokens['chip-underlay'], card));
    const r = ratio(over(tokens[`chip-${k}-text`], bg), bg);
    assert.ok(r >= 4.5, `chip ${k}: ${r.toFixed(2)}`);
  }
  assert.ok(ratio(parse(tokens['on-accent']).slice(0, 3), parse(tokens.accent).slice(0, 3)) >= 4.5, 'on-accent on accent');
  const sel = over(tokens.selected, card);
  assert.ok(ratio(parse(tokens['text-on-selected']).slice(0, 3), sel) >= 4.5, 'selected pill');
});
