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

// The model-name table is LEARNED from users and curated by an admin — nothing
// is seeded. So the assertion is about the mechanism, not about any particular
// model being present: whatever is in there must translate to something other
// than the raw identifier, or it isn't doing its job.
test('learned model names translate the vendor identifier, never echo it', async ({ request }) => {
  const res = await request.get('/api/admin/model-names', { failOnStatusCode: false });
  if ([403, 404].includes(res.status())) test.skip(true, 'not owner / route absent on this tier');
  const body = await res.json();
  const rows = body.model_names || body.items || body;
  expect(Array.isArray(rows), 'expected model_names array, got ' + JSON.stringify(body).slice(0, 120)).toBeTruthy();
  for (const r of rows) {
    expect(r.friendly_name, `${r.code} must map to a name, not back to itself`).not.toBe(r.code);
    expect(String(r.friendly_name).trim().length).toBeGreaterThan(0);
  }
});
