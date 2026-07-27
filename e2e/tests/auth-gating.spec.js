import { test, expect } from '@playwright/test';

test.describe('authentication gating', () => {
  test('/app redirects an unauthenticated visitor to Authentik', async ({ page }) => {
    await page.goto('/app');
    await expect(page).toHaveURL(/auth\.nnlink\.org/);
  });

  test('public pages are reachable without auth', async ({ page }) => {
    for (const path of ['/', '/signup', '/legal']) {
      const res = await page.goto(path);
      expect(res.status(), path + ' status').toBe(200);
      expect(page.url(), path + ' should not bounce to auth').not.toContain('internal.example.com');
    }
  });
});
