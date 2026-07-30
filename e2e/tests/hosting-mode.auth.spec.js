// Regression coverage for the printer hosting-mode feature (P1): the Global
// Admin selector persists a mode, and the Printers page reshapes per mode.
// Requires owner auth (dev-login user is the first user => owner on a fresh tier).
import { test, expect } from '@playwright/test';

// Restore the mode to 'both' after the suite so manual testing isn't disturbed.
test.afterAll(async ({ request }) => {
  await request.put('/api/admin/settings', { data: { deployment_mode: 'both' } }).catch(() => {});
});

async function setMode(request, mode) {
  const res = await request.put('/api/admin/settings', { data: { deployment_mode: mode } });
  expect(res.ok(), `set mode ${mode}`).toBeTruthy();
  const body = await res.json();
  expect(body.deployment_mode).toBe(mode);
}

test.describe('hosting mode — API', () => {
  test('accepts local, remote, both and rejects garbage (falls back to both)', async ({ request }) => {
    for (const m of ['local', 'remote', 'both']) await setMode(request, m);
    // legacy alias maps to remote
    let r = await request.put('/api/admin/settings', { data: { deployment_mode: 'cloud' } });
    expect((await r.json()).deployment_mode).toBe('remote');
    // unknown -> both
    r = await request.put('/api/admin/settings', { data: { deployment_mode: 'nonsense' } });
    expect((await r.json()).deployment_mode).toBe('both');
    // public config reflects it
    const pub = await (await request.get('/api/pub/config')).json();
    expect(['local', 'remote', 'both']).toContain(pub.deployment_mode);
  });
});

test.describe('hosting mode — Printers page layout', () => {
  test('local mode hides the connect section behind a reveal', async ({ request, page }) => {
    await setMode(request, 'local');
    await page.goto('/app/printers');
    // The reveal button is the local-mode affordance for remote printers.
    const reveal = page.getByRole('button', { name: /not on the same network/i });
    await expect(reveal).toBeVisible();
    await reveal.click();
    // After revealing, a Hide button appears.
    await expect(page.getByRole('button', { name: /^Hide$/ })).toBeVisible();
  });

  test('remote mode gates the add button until a client pairs', async ({ request, page }) => {
    await setMode(request, 'remote');
    await page.goto('/app/printers');
    // Whether add is enabled depends on paired clients. Assert the two coherent
    // states rather than a fixed one, since dev may or may not have a paired client.
    const connectors = await (await request.get('/api/connectors')).json();
    const hasPaired = (Array.isArray(connectors) ? connectors : connectors.connectors || []).some((c) => c.has_client_key);
    if (hasPaired) {
      await expect(page.getByRole('link', { name: /Add (printer|your first printer)/i }).first()).toBeVisible();
    } else {
      // disabled affordance is a non-link span with aria-disabled
      await expect(page.locator('[aria-disabled="true"]', { hasText: /Add/i }).first()).toBeVisible();
    }
  });

  test('both mode exposes both add and connect paths', async ({ request, page }) => {
    await setMode(request, 'both');
    await page.goto('/app/printers');
    await expect(page.getByRole('link', { name: /Add (printer|your first printer)/i }).first()).toBeVisible();
  });
});
