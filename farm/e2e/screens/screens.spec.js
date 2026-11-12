// Screen checks: every page of the web app, rendered against the stand-in
// backend (server.mjs) at the two desktop sizes, and the public pages on a
// phone. Each page must fit the window without the page itself scrolling and
// with nothing hidden (see check.mjs). A screenshot of each is kept in
// SHOTS_DIR when it is set.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import { layoutProblems } from './check.mjs';

const BASE = process.env.BASE_PATH || '';
const SHOTS = process.env.SHOTS_DIR || '';

// The landing page is a long marketing page and scrolls by design; it is
// checked for horizontal overflow only.
export const ROUTES = [
  '/app', '/app/printers', '/app/printers/1', '/app/printers/3', '/app/printers/4', '/app/printers/add',
  '/app/printers/groups', '/app/printers/1/camera', '/app/cameras', '/app/cameras?tab=timelapses',
  '/app/queue', '/app/files', '/app/files/500', '/app/slicer', '/app/filament', '/app/projects',
  '/app/statistics', '/app/reports', '/app/settings', '/app/genfilament',
  '/login', '/signup', '/legal', '/t/ABC123'
];
const SIZES = [[1920, 1080], [1280, 860]];

const slug = (r) => (r === '/' ? 'home' : r.replace(/^\//, '').replace(/[\/?=&]/g, '_'));

for (const [w, h] of SIZES) {
  test.describe(`${w}x${h}`, () => {
    test.use({ viewport: { width: w, height: h } });
    for (const r of ROUTES) {
      test(`${r} fits the window`, async ({ page }) => {
        const errors = [];
        page.on('pageerror', (e) => errors.push(e.message));
        await page.goto(BASE + r, { waitUntil: 'networkidle' });
        await page.waitForTimeout(600);
        if (SHOTS) {
          fs.mkdirSync(SHOTS, { recursive: true });
          await page.screenshot({ path: `${SHOTS}/${slug(r)}-${w}x${h}.png` });
        }
        expect(await page.evaluate(layoutProblems)).toEqual([]);
        expect(errors).toEqual([]);
      });
    }
  });
}

test.describe('landing', () => {
  for (const [w, h] of [...SIZES, [390, 844]]) {
    test(`no sideways scroll at ${w}x${h}`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: h });
      await page.goto(BASE + '/', { waitUntil: 'networkidle' });
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/home-${w}x${h}.png` });
      const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(over).toBeLessThanOrEqual(1);
    });
  }
});

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 } });
  for (const r of ['/login', '/signup', '/t/ABC123']) {
    test(`${r} fits a phone`, async ({ page }) => {
      await page.goto(BASE + r, { waitUntil: 'networkidle' });
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/${slug(r)}-390x844.png` });
      expect(await page.evaluate(layoutProblems)).toEqual([]);
    });
  }
});

// Every text colour the app's own names resolve to must pass 4.5:1 over the
// lightest stop of the page gradient, which is where glass is at its brightest.
test('text colours pass 4.5:1 over the lightest page stop', async ({ page }) => {
  await page.goto(BASE + '/login', { waitUntil: 'networkidle' });
  const results = await page.evaluate(() => {
    const names = ['--ophq-text', '--ophq-text-2', '--ophq-muted', '--ophq-faint', '--ophq-primary-2', '--ophq-danger', '--ophq-success', '--ophq-warn', '--ophq-accent', '--ophq-info'];
    const probe = document.createElement('i');
    document.body.appendChild(probe);
    const rgb = (v) => { probe.style.color = v; return getComputedStyle(probe).color.match(/[\d.]+/g).map(Number); };
    const lum = ([r, g, b]) => [r, g, b].map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; })
      .reduce((a, c, i) => a + c * [0.2126, 0.7152, 0.0722][i], 0);
    const ground = rgb(getComputedStyle(document.documentElement).getPropertyValue('--ah-page-top'));
    const out = {};
    for (const n of names) {
      const [r, g, b, a = 1] = rgb(`var(${n})`);
      // Blend a translucent colour over the ground first.
      const c = [r, g, b].map((x, i) => x * a + ground[i] * (1 - a));
      const l1 = lum(c), l2 = lum(ground);
      out[n] = Math.round(((Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)) * 100) / 100;
    }
    probe.remove();
    return out;
  });
  for (const [n, ratio] of Object.entries(results)) expect(ratio, `${n} is ${ratio}:1`).toBeGreaterThanOrEqual(4.5);
});
