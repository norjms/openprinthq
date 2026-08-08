// A live stream and a still frame up to a minute old look identical on screen.
// The badge is the only thing that distinguishes them, which matters because a
// relayed snapshot silently passing for working live video is exactly the
// failure that is hardest to notice: everything appears fine while the video is
// crossing the server the design exists to bypass.
import { test, expect } from '@playwright/test';

test('the detail view says whether video is live or a snapshot', async ({ page, request }) => {
  const res = await request.get('/api/engine/api/v1/printers/', { failOnStatusCode: false });
  if (!res.ok()) test.skip(true, 'engine unavailable on this tier');
  const body = await res.json();
  const printers = Array.isArray(body) ? body : (body.printers || body.items || []);
  if (!printers.length) test.skip(true, 'no printers on this tier');

  // Navigate directly: clicking through the list depends on card markup that is
  // not what this test is about.
  await page.goto(`/app/printers/${printers[0].id}`);
  await page.waitForLoadState('networkidle');

  const badge = page.locator('.badge').first();
  await expect(badge, 'no LIVE/SNAPSHOT badge on the camera').toBeVisible({ timeout: 25000 });
  const text = (await badge.innerText()).trim();
  expect(['LIVE', 'SNAPSHOT'], `unexpected badge text: ${text}`).toContain(text);

  // Whichever mode it reports must match what is actually rendered, or the badge
  // is worse than no badge at all.
  const live = text === 'LIVE';
  const videoVisible = await page.locator('video:not(.hidden)').count();
  if (live) expect(videoVisible, 'badge says LIVE but no video element is showing').toBeGreaterThan(0);
  else expect(await page.locator('img[src*="camera/snapshot"]').count(),
    'badge says SNAPSHOT but no snapshot image is present').toBeGreaterThan(0);
});
