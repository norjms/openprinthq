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

// A real camera picture is needed for the cache's canvas: a 64 x 36 grey JPEG.
const JPEG = Buffer.from(
  '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAAkAEADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDNooorpOYKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigD//2Q==',
  'base64');

async function farm(page, snapshot) {
  await page.route('**/api/farm', (r) => r.fulfill({ json: {
    printers: [{ name: 'H2D', online: true, busy: true, progress: 64 }],
    checks: [], queue: [], timeline: [], plates: [], history: [], maintenance: [] } }));
  await page.route('**/api/farm/hidden-errors', (r) => r.fulfill({ json: { hidden: [] } }));
  await page.route('**/api/mobile/v1/printers/*/snapshot*', snapshot);
}

test('the last picture stays while the next one loads, with a spinner and its age', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  let release;
  let n = 0;
  await farm(page, async (r) => {
    if (n++ === 0) return r.fulfill({ status: 200, contentType: 'image/jpeg', body: JPEG });
    await new Promise((ok) => (release = ok));            // the second picture is slow, as a Bambu camera is
    return r.fulfill({ status: 200, contentType: 'image/jpeg', body: JPEG });
  });
  await page.clock.install();
  await page.goto('/farm?view=cameras');
  const tile = page.locator('[data-camera="H2D"]');
  await expect(tile.getByAltText('Camera on H2D')).toBeVisible();
  await expect(tile.getByText('just now')).toBeVisible();
  await page.clock.runFor(10_500);
  await expect(tile.getByRole('status')).toHaveText('Refreshing');
  await expect(tile.getByAltText('Camera on H2D')).toBeVisible();   // still the last picture
  await expect(tile.getByText(/s ago$/)).toBeVisible();
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/farm-cameras-refreshing-1920x1080.png` });
  release();
  await expect(tile.getByRole('status')).toHaveCount(0);
});

test('a camera that stops answering keeps its last picture, marked with its age', async ({ page }) => {
  let n = 0;
  await farm(page, (r) => (n++ === 0
    ? r.fulfill({ status: 200, contentType: 'image/jpeg', body: JPEG })
    : r.fulfill({ status: 503, json: { detail: 'camera busy' } })));
  await page.clock.install();
  await page.goto('/farm?view=cameras');
  const tile = page.locator('[data-camera="H2D"]');
  await expect(tile.getByAltText('Camera on H2D')).toBeVisible();
  await page.clock.runFor(10_500);
  await expect(tile.getByText(/^Last picture/)).toBeVisible();
  await expect(tile.getByAltText('Camera on H2D')).toBeVisible();
});

test('the page opens with the last picture this browser saw, then a fresh one', async ({ page }) => {
  let hold = true;
  let release;
  await farm(page, async (r) => {
    if (hold) await new Promise((ok) => (release = ok));
    return r.fulfill({ status: 200, contentType: 'image/jpeg', body: JPEG });
  });
  hold = false;
  await page.goto('/farm?view=cameras');
  await expect(page.locator('[data-camera="H2D"]').getByAltText('Camera on H2D')).toBeVisible();
  await expect.poll(() => page.evaluate(() => !!localStorage.getItem('ahub_cam_H2D'))).toBe(true);
  hold = true;
  await page.reload();
  const tile = page.locator('[data-camera="H2D"]');
  await expect(tile.getByAltText('Camera on H2D')).toBeVisible();            // the cached copy, at once
  await expect(tile.getByRole('status')).toHaveText('Refreshing');
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/farm-cameras-cached-1280x860.png` });
  release();
  await expect(tile.getByRole('status')).toHaveCount(0);
});
