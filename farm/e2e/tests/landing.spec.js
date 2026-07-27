import { test, expect } from '@playwright/test';

test.describe('landing', () => {
  test('loads with the light theme by default', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/OpenPrintHQ/);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('theme switcher offers Light / Dark / Accessible', async ({ page }) => {
    await page.goto('/');
    const sw = page.locator('.theme-switch').first();
    await expect(sw).toBeVisible();
    for (const label of ['Light', 'Dark', 'Accessible']) {
      await expect(sw.getByRole('button', { name: new RegExp(label) })).toBeVisible();
    }
  });
});
