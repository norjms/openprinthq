// Crafts (docs/crafts.md): Settings, Crafts turns an optional craft on and off for the whole hub, and the
// sidebar follows at once. The test puts the crafts back as it found them (all off).
import { test, expect } from '@playwright/test';

const groups = (page) => page.locator('.ah-sidebar__nav .ah-group-label');

test('Settings, Crafts turns Laser Crafting on and off and the sidebar follows', async ({ page }) => {
  await page.goto('/settings?tab=crafts', { waitUntil: 'networkidle' });
  await expect(page.getByText('Always on', { exact: true })).toBeVisible();
  const laser = page.getByRole('switch', { name: 'Laser Crafting' });
  await expect(laser).toHaveAttribute('aria-checked', 'false');
  await expect(groups(page)).not.toContainText(['Laser Crafting']);

  await laser.click();
  await expect(laser).toHaveAttribute('aria-checked', 'true');
  await expect(groups(page).filter({ hasText: /^Laser Crafting$/ })).toHaveCount(1);

  // This hub has no OpenPrintHQ Laser app behind /apps/laser: the overview says so.
  await page.goto('/crafts/laser', { waitUntil: 'networkidle' });
  await expect(page.getByText('The laser tools are not installed on this hub')).toBeVisible();

  await page.goto('/settings?tab=crafts', { waitUntil: 'networkidle' });
  await page.getByRole('switch', { name: 'Laser Crafting' }).click();
  await expect(page.getByRole('switch', { name: 'Laser Crafting' })).toHaveAttribute('aria-checked', 'false');
  await expect(groups(page).filter({ hasText: /^Laser Crafting$/ })).toHaveCount(0);
});

test('a craft that is off says so on its page', async ({ page }) => {
  await page.goto('/crafts/sublimation', { waitUntil: 'networkidle' });
  await expect(page.getByRole('heading', { name: 'Sublimation', level: 2 })).toBeVisible();
  await expect(page.getByText('This craft is off')).toBeVisible();
});
