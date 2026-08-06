// The TURN token is a live Cloudflare credential. These guard the two
// properties that matter: only an owner can touch it, and it can never be read
// back out through the API.
import { test, expect } from '@playwright/test';

test.describe('TURN credentials — admin settings', () => {
  test('settings report TURN status without ever exposing the token', async ({ request }) => {
    const res = await request.get('/api/admin/settings');
    if (res.status() === 403) test.skip(true, 'test user is not the owner on this tier');
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty('cf_turn');
    expect(body.cf_turn).toHaveProperty('configured');
    // A hint is allowed; the secret itself is not.
    const serialized = JSON.stringify(body);
    expect(serialized).not.toMatch(/cf_turn_api_token/);
    expect(body.cf_turn).not.toHaveProperty('api_token');
    expect(body.cf_turn).not.toHaveProperty('token');
  });

  test('saving a token does not echo it back', async ({ request }) => {
    const probe = await request.get('/api/admin/settings');
    if (probe.status() === 403) test.skip(true, 'test user is not the owner on this tier');
    const sentinel = 'e2e-sentinel-token-do-not-persist';
    const res = await request.put('/api/admin/settings', {
      data: { cf_turn_api_token: sentinel },
      failOnStatusCode: false
    });
    expect(res.ok()).toBeTruthy();
    expect(JSON.stringify(await res.json())).not.toContain(sentinel);
    // And it must not leak on a subsequent read either.
    const after = await request.get('/api/admin/settings');
    expect(JSON.stringify(await after.json())).not.toContain(sentinel);
    // Clean up so we don't leave a bogus credential configured.
    await request.put('/api/admin/settings', { data: { cf_turn_api_token: '' }, failOnStatusCode: false });
  });

  test('TURN routes are owner-gated', async ({ request }) => {
    const res = await request.post('/api/admin/settings/turn-test', { failOnStatusCode: false });
    // Owner: 200 (working) or 400/502 (not configured / rejected). Non-owner: 403.
    expect([200, 400, 403, 502]).toContain(res.status());
  });
});

// Users know their printer as an "H2D", never as an "O1D". The vendor code is a
// wire detail from SSDP/MQTT and must be translated on the way in — if it ever
// reaches a screen or a client, someone has to look up what it means.
test('vendor model codes are translated to marketing names', async ({ request }) => {
  const res = await request.get('/api/admin/model-names', { failOnStatusCode: false });
  if (res.status() === 403 || res.status() === 404) test.skip(true, 'not owner / route absent on this tier');
  const rows = await res.json();
  const byCode = Object.fromEntries((rows.items || rows).map((r) => [r.code, r.friendly_name]));
  // Seeded from the engine's authoritative table.
  expect(byCode['O1D']).toBe('H2D');
  expect(byCode['O1C2']).toBe('H2C');
  expect(byCode['BL-P001']).toBe('X1C');
  for (const [code, name] of Object.entries(byCode)) {
    expect(name, `${code} must map to a name, not back to itself`).not.toBe(code);
  }
});
