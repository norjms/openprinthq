import { test, expect } from '@playwright/test';

test.describe('API surface', () => {
  test('GET /api/pub/signup-info returns config JSON', async ({ request }) => {
    const res = await request.get('/api/pub/signup-info');
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty('enabled');
    expect(body).toHaveProperty('inviteRequired');
  });

  test('gated /api/me is never served without auth', async ({ request }) => {
    const res = await request.get('/api/me', { maxRedirects: 0 });
    expect(res.status(), 'must not return 200 unauthenticated').not.toBe(200);
    expect([301, 302, 303, 401, 403]).toContain(res.status());
  });
});
