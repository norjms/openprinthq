// Regression for the model-name mapping feature (P4/P5): the owner-only Printer
// Names tab lists mappings, and the learn endpoint fills-when-empty.
import { test, expect } from '@playwright/test';

test('Printer Names tab shows the model-name mappings', async ({ page }) => {
  await page.goto('/app/settings');
  await page.getByRole('tab', { name: 'Printer Names' }).click();
  // The section label renders as an .eyebrow span (not a semantic heading).
  await expect(page.getByText('Printer names', { exact: true })).toBeVisible();
  // seeded mapping should be visible (O1D -> H2D)
  await expect(page.getByText('H2D', { exact: true }).first()).toBeVisible();
});

test('learn is fill-when-empty and respects locks (API)', async ({ request }) => {
  // O1D is seeded + locked as H2D; a learn attempt must NOT overwrite it.
  await request.post('/api/model-names/learn', { data: { vendor: 'bambu', code: 'O1D', friendly_name: 'SHOULD-NOT-WIN' } });
  const r = await (await request.get('/api/model-names/lookup?vendor=bambu&code=O1D')).json();
  expect(r.friendly_name).toBe('H2D');
  // a brand-new code learns fine
  const code = 'TESTCODE' + Date.now();
  await request.post('/api/model-names/learn', { data: { vendor: 'bambu', code, friendly_name: 'TestModel' } });
  const r2 = await (await request.get('/api/model-names/lookup?vendor=bambu&code=' + code)).json();
  expect(r2.friendly_name).toBe('TestModel');
});
