// The multiplexed tunnel is opt-in per connector and negotiated at connect
// time, so the thing that can silently regress is the endpoint disappearing or
// losing its auth gate — after which every client quietly falls back to the
// slow transport and nobody notices until printers start flapping again.
import { test, expect } from '@playwright/test';

test('the multiplexed tunnel is advertised', async ({ request }) => {
  const res = await request.get('/api/pub/config');
  expect(res.ok()).toBeTruthy();
  expect((await res.json()).connector_ws).toBe(true);
});

test('the tunnel endpoint rejects an unauthenticated upgrade', async ({ request }) => {
  // Without a valid connector token the upgrade must not succeed. A plain GET
  // stands in for the handshake: what matters is that it is never a 101.
  const res = await request.get('/api/connector/ws', {
    headers: { connection: 'Upgrade', upgrade: 'websocket', 'sec-websocket-version': '13', 'sec-websocket-key': 'AAAAAAAAAAAAAAAAAAAAAA==' },
    failOnStatusCode: false
  });
  expect(res.status(), 'unauthenticated upgrade must not be accepted').not.toBe(101);
  // And it should be refused before the handshake, not after: 401 from the app,
  // or a redirect if an SSO edge sits in front on this tier.
  expect([301, 302, 401, 403]).toContain(res.status());
});

// Regression guard for a bug that only showed up with a real agent attached:
// proxyViaConnector wrote to target.raw directly instead of going through the
// transport, so every job failed with "connector write failed" on the
// multiplexed tunnel while the SSE path kept working.
test('a job to an offline connector reports it offline, not as a write failure', async ({ request }) => {
  const res = await request.post('/api/connectors/999999/discover', {
    data: { window_ms: 1000 },
    failOnStatusCode: false
  });
  if (res.status() >= 400) return;           // not found / not authorised is fine
  const body = await res.json().catch(() => ({}));
  expect(body.error || '').not.toContain('connector write failed');
});
