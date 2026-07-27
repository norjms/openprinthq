import { test, expect } from '@playwright/test';

// Regression for the mobile "banner" fix: the header must not overflow on phones.
test.describe('responsive header (390px phone)', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('no horizontal overflow on the landing page', async ({ page }) => {
    await page.goto('/');
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test('the "Launch your HQ" action stays within the viewport', async ({ page }) => {
    await page.goto('/');
    const launch = page.locator('.site-header').getByRole('link', { name: /Launch your HQ/ });
    await expect(launch).toBeVisible();
    const box = await launch.boundingBox();
    expect(box.x + box.width).toBeLessThanOrEqual(391);
  });
});
