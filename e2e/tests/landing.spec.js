import { test, expect } from '@playwright/test';

test.describe('landing', () => {
  test('loads with the dark theme by default', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/OpenPrintHQ/);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  });

  test('theme switcher offers Light / Dark / Accessible', async ({ page }) => {
    await page.goto('/');
    const sw = page.locator('.theme-switch').first();
    await expect(sw).toBeVisible();
    for (const label of ['Light', 'Dark', 'Accessible']) {
      await expect(sw.getByRole('button', { name: new RegExp(label) })).toBeVisible();
    }
  });

  // Regression: switching to Light must actually apply the light palette, not
  // just flip the data-theme attribute. A CSS cascade bug once let the :root
  // dark palette win over [data-theme='light'] (equal specificity, defined
  // later), so the page stayed dark after clicking Light. We assert on the
  // *rendered* --ophq-bg custom property, which reflects the winning palette.
  test('switching Dark <-> Light actually recolors the page', async ({ page }) => {
    await page.goto('/');
    const sw = page.locator('.theme-switch').first();
    await expect(sw).toBeVisible();
    const bg = () =>
      page.evaluate(() =>
        getComputedStyle(document.documentElement).getPropertyValue('--ophq-bg').trim().toLowerCase()
      );

    // Default: dark attribute AND dark palette.
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    expect(await bg()).toBe('#0d1117');

    // Switch to Light — attribute flips AND the palette becomes light.
    await sw.getByRole('button', { name: /Light/ }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    expect(await bg(), 'light palette must win over the :root dark default').toBe('#999999');

    // And back to Dark.
    await sw.getByRole('button', { name: /Dark/ }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    expect(await bg()).toBe('#0d1117');
  });
});
