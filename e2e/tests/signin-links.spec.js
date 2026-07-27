import { test, expect } from '@playwright/test';

// Regression: a client-side <a href="/app"> bypasses Authentik forward-auth
// (which only runs on a full page load), dropping users onto an unauthenticated
// /app where every API call fails ("control-plane unreachable"). Every sign-in
// entry link must carry data-sveltekit-reload to force a real navigation.
test.describe('sign-in links force a full page load', () => {
  async function assertReload(link) {
    const val = await link.getAttribute('data-sveltekit-reload');
    expect(val, 'data-sveltekit-reload must be present').not.toBeNull();
  }

  test('signup page: "Sign in"', async ({ page }) => {
    await page.goto('/signup');
    await assertReload(page.getByRole('link', { name: /^Sign in$/ }));
  });

  test('footer: "Launch your HQ"', async ({ page }) => {
    await page.goto('/');
    await assertReload(page.locator('footer').getByRole('link', { name: /Launch your HQ/ }));
  });

  test('header: "Sign in"', async ({ page }) => {
    await page.goto('/');
    await assertReload(page.locator('.site-header').getByRole('link', { name: /Sign in/ }));
  });

  test('clicking signup "Sign in" routes through Authentik (not a broken /app)', async ({ page }) => {
    await page.goto('/signup');
    await page.getByRole('link', { name: /^Sign in$/ }).click();
    await expect(page).toHaveURL(/auth\.nnlink\.org/);
  });
});
