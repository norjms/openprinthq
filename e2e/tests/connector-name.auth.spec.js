// Regression for issue #4: creating a connector with an empty name must show a
// "Name is required" message instead of silently doing nothing.
// (Connectors now live under Settings → Printers tab.)
import { test, expect } from '@playwright/test';

test('empty connector name shows a required-name error', async ({ page }) => {
  await page.goto('/app/settings');
  // Local Connectors moved out of General into the new "Printers" settings tab.
  await page.getByRole('tab', { name: 'Printers' }).click();
  const addBtn = page.getByRole('button', { name: /New connector/i });
  await expect(addBtn).toBeVisible();
  await addBtn.click();
  await expect(page.getByText(/Name is required/i)).toBeVisible();
});
