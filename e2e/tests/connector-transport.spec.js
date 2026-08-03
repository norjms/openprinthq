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
});
