// Regression guards for the agent-local connection model.
//
// The broker/rendezvous experiment required the user to forward an inbound port
// to the connector host, which cannot work behind CGNAT — the deployment this
// product exists to serve. It was reverted. These tests fail if it comes back,
// and if the public source/download links regress to the retired Gitea host.
import { test, expect } from '@playwright/test';

test.describe('agent-local model — no inbound broker path', () => {
  for (const path of [
    '/api/connector/register-endpoint',
    '/api/connector/broker-endpoint',
  ]) {
    test(`${path} is not served`, async ({ request }) => {
      const res = await request.post(path, { data: {}, failOnStatusCode: false });
      // 404 = route gone. Anything that looks like a live endpoint (2xx, or a
      // 400/401 meaning "route exists, your input was wrong") is a regression.
      expect(res.status(), `${path} should not exist`).toBe(404);
    });
  }

  test('the connector tunnel is still outbound-only and token-gated', async ({ request }) => {
    const res = await request.get('/api/connector/stream?name=probe', {
      headers: { authorization: 'Bearer not-a-real-token' },
      failOnStatusCode: false,
    });
    expect(res.status()).toBe(401);
  });
});

test.describe('public links point at the live forge', () => {
  test('legal page source links use GitHub, not the retired Gitea host', async ({ page }) => {
    await page.goto('/legal');
    const hrefs = await page.locator('a[href*="openprinthq"]').evaluateAll(
      (as) => as.map((a) => a.getAttribute('href') || '')
    );
    const repoLinks = hrefs.filter((h) => /github\.com|nnlink\.org/.test(h));
    expect(repoLinks.length, 'expected repository links on /legal').toBeGreaterThan(0);
    for (const h of repoLinks) {
      expect(h, 'internal hostname leaked into a public link').not.toContain('nnlink.org');
      expect(h).toContain('github.com/norjms/');
    }
  });
});
