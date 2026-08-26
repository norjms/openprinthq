// Does the camera frame cache actually persist frames?
// The existing suite never checked this: it verifies badges and playback, but
// nothing asserts a frame is written to localStorage, which is the whole point
// of the instant-load design.
import { test, expect } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

test('camera frames are written to the localStorage cache', async ({ page }) => {
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  await page.goto('/app/cameras');
  await page.waitForLoadState('networkidle');
  // Give the tiles time to paint a frame and the cache time to write.
  await page.waitForTimeout(20000);

  const state = await page.evaluate(() => {
    const keys = Object.keys(localStorage).filter((k) => k.startsWith('ophq_cam'));
    return {
      keys,
      sizes: keys.map((k) => [k, (localStorage.getItem(k) || '').length]),
      total: Object.keys(localStorage).length,
      allKeys: Object.keys(localStorage)
    };
  });
  console.log('CACHE KEYS:', JSON.stringify(state.sizes));
  console.log('ALL LOCALSTORAGE KEYS:', JSON.stringify(state.allKeys));
  if (errors.length) console.log('CONSOLE ERRORS:', JSON.stringify(errors.slice(0, 10)));

  const tiles = await page.locator('img, video').count();
  console.log('TILES (img+video):', tiles);

  expect(state.keys.length, 'expected at least one cached camera frame').toBeGreaterThan(0);
});
