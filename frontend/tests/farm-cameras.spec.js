// Farm > Cameras: a camera whose first frame fails (one that is slow to wake) comes back on its own at the next
// 10 s refresh, without reloading the page. The hub's answers are stood in for.
import { test, expect } from '@playwright/test';

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');

test('a camera that missed its first frame shows again on the next refresh', async ({ page }) => {
  await page.clock.install();
  await page.route('**/api/farm', (r) => r.fulfill({ json: {
    printers: [{ name: 'H2D', online: true, busy: true, progress: 64 }, { name: 'MK3S', online: true, busy: false }],
    checks: [], queue: [], timeline: [], plates: [], history: [], maintenance: [] } }));
  await page.route('**/api/farm/hidden-errors', (r) => r.fulfill({ json: { hidden: [] } }));
  let mk3s = 0;
  await page.route('**/api/mobile/v1/printers/*/snapshot*', (r) => {
    const name = decodeURIComponent(new URL(r.request().url()).pathname.split('/')[5]);
    if (name === 'MK3S' && mk3s++ === 0) return r.fulfill({ status: 404, json: { detail: 'no camera frame for MK3S' } });
    return r.fulfill({ status: 200, contentType: 'image/png', body: PNG });
  });
  await page.goto('/farm?view=cameras');
  await expect(page.getByText('No camera frame from MK3S')).toBeVisible();
  await expect(page.getByAltText('Camera on H2D')).toBeVisible();
  await page.clock.runFor(10_500);
  await expect(page.getByText('No camera frame from MK3S')).toHaveCount(0);
  await expect(page.getByAltText('Camera on MK3S')).toBeVisible();
});
