import { test, expect } from '@playwright/test';

test.describe('signup', () => {
  test('page loads with the account form', async ({ page }) => {
    await page.goto('/signup');
    await expect(page.getByRole('heading', { name: /Create your account/ })).toBeVisible();
    await expect(page.getByRole('textbox', { name: /Email/i })).toBeVisible();
  });

  test('invite field presence matches signup-info', async ({ page, request }) => {
    const info = await (await request.get('/api/pub/signup-info')).json();
    await page.goto('/signup');
    const invite = page.getByPlaceholder('ophq-…');
    if (info.inviteRequired) {
      await expect(invite).toBeVisible();
    } else {
      await expect(invite).toHaveCount(0);
    }
  });
});
