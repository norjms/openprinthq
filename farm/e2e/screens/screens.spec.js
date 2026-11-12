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
// The pages are checked as an installed farm shows them: with the hub answering
// (the second stand-in, see playwright.screens.config.js). hubnav.spec.js covers
// the farm without the hub.
const ORIGIN = `http://127.0.0.1:${Number(process.env.SCREENS_PORT || 4173) + 1}`;
const at = (r) => ORIGIN + BASE + r;
const SHOTS = process.env.SHOTS_DIR || '';

// Every page of the farm. The farm has no landing, sign-in or sign-up page of
// its own (the hub's sign-in sits in front of it): those addresses lead to the app.
export const ROUTES = [
  '/app', '/app/printers', '/app/printers/1', '/app/printers/3', '/app/printers/4', '/app/printers/add',
  '/app/printers/groups', '/app/printers/1/camera', '/app/cameras', '/app/timelapses',
  '/app/queue', '/app/files', '/app/files/500', '/app/slicer', '/app/filament', '/app/projects',
  '/app/statistics', '/app/reports', '/app/settings', '/app/genfilament', '/legal', '/t/ABC123'
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
        await page.goto(at(r), { waitUntil: 'networkidle' });
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

test.describe('no pages of its own for signing in', () => {
  for (const r of ['/', '/login', '/signup', '/app/cameras?tab=timelapses']) {
    test(`${r} leads into the app`, async ({ page }) => {
      await page.goto(at(r), { waitUntil: 'networkidle' });
      expect(new URL(page.url()).pathname).toMatch(new RegExp('^' + BASE + '/app'));
      await expect(page.locator('.ah-sidebar')).toBeVisible();
    });
  }
});

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 } });
  for (const r of ['/legal', '/t/ABC123']) {
    test(`${r} fits a phone`, async ({ page }) => {
      await page.goto(at(r), { waitUntil: 'networkidle' });
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/${slug(r)}-390x844.png` });
      expect(await page.evaluate(layoutProblems)).toEqual([]);
    });
  }
});

// Every text colour the app's own names resolve to must pass 4.5:1 over the
// lightest stop of the page gradient, which is where glass is at its brightest.
test('text colours pass 4.5:1 over the lightest page stop', async ({ page }) => {
  await page.goto(at('/legal'), { waitUntil: 'networkidle' });
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
