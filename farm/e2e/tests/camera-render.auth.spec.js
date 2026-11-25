// Does a camera actually RENDER in a browser?
//
// Every other camera check in this suite asserts that bytes come back from an
// API. That is necessary but not sufficient: a feed can return valid JPEG and
// still show nothing, because the element never gets a src, the poll never
// starts, or the image fails to decode. This drives a real browser and asks the
// only question that matters to a user: are there pixels on screen.
import { test, expect } from '@playwright/test';

test.describe('camera renders in the browser', () => {
  test('the printers page shows a decoded camera image', async ({ page }) => {
    await page.goto('/app/printers');
    await page.waitForLoadState('networkidle');

    const imgs = page.locator('img[src*="camera/snapshot"]');
    const count = await imgs.count();
    if (!count) test.skip(true, 'no camera-backed printers on this tier');

    // naturalWidth is 0 until the browser has actually decoded the bytes, so it
    // distinguishes "rendered" from "element exists with a src that 404s".
    await expect.poll(async () => {
      return await imgs.first().evaluate((el) => el.complete && el.naturalWidth);
    }, { timeout: 45000, message: 'camera image never decoded in the browser' }).toBeGreaterThan(0);

    const dims = await imgs.first().evaluate((el) => ({ w: el.naturalWidth, h: el.naturalHeight }));
    expect(dims.w).toBeGreaterThan(100);
    expect(dims.h).toBeGreaterThan(100);
    // eslint-disable-next-line no-console
    console.log(`  camera rendered at ${dims.w}x${dims.h}`);
  });

  test('every camera-backed printer renders, not just the first', async ({ page }) => {
    await page.goto('/app/printers');
    await page.waitForLoadState('networkidle');
    const imgs = page.locator('img[src*="camera/snapshot"]');
    const n = await imgs.count();
    if (!n) test.skip(true, 'no camera-backed printers on this tier');

    const results = [];
    for (let i = 0; i < n; i++) {
      const ok = await imgs.nth(i).evaluate(async (el) => {
        for (let t = 0; t < 40; t++) {
          if (el.complete && el.naturalWidth > 0) return el.naturalWidth;
          await new Promise((r) => setTimeout(r, 1000));
        }
        return 0;
      });
      results.push(ok);
    }
    // eslint-disable-next-line no-console
    console.log('  rendered widths:', results.join(', '));
    expect(results.filter((w) => w > 0).length, `only ${results.filter((w) => w > 0).length}/${n} cameras rendered`).toBe(n);
  });
});
