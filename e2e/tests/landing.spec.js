import { test, expect } from '@playwright/test';

test.describe('landing', () => {
  test('loads with the glass theme', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/OpenPrintHQ/);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'glass-dark');
  });

  test('takes its colours from the shared ArtisanHub UI values', async ({ page }) => {
    await page.goto('/');
    const v = (name) => page.evaluate((n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim().toLowerCase(), name);
    expect(await v('--ah-page')).toBe('#172028');
    expect(await v('--ah-accent')).toBe('#8fd3ff');
    // The app's own names follow the shared values.
    const accent = await page.evaluate(() => {
      const el = document.createElement('i');
      el.style.color = 'var(--ophq-primary)';
      document.body.appendChild(el);
      const c = getComputedStyle(el).color; el.remove(); return c;
    });
    expect(accent).toBe('rgb(143, 211, 255)');
  });

  test('there is no theme picker: one look everywhere', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.theme-switch')).toHaveCount(0);
  });
});
