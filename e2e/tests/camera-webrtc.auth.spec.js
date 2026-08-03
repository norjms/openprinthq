// Guards the signalling contract for direct browser<->connector video. The
// media path itself needs a real printer and a remote network, so it can't be
// asserted here; these cover the parts that CAN regress silently.
import { test, expect } from '@playwright/test';

test.describe('camera WebRTC signalling', () => {
  test('ICE endpoint returns servers and leads with STUN', async ({ request }) => {
    const res = await request.get('/api/camera/ice');
    expect(res.ok()).toBeTruthy();
    const { iceServers } = await res.json();
    expect(Array.isArray(iceServers)).toBeTruthy();
    expect(iceServers.length).toBeGreaterThan(0);
    const urls = iceServers.flatMap((s) => [].concat(s.urls || []));
    expect(urls.some((u) => u.startsWith('stun:'))).toBeTruthy();
  });

  test('ICE requires a session', async ({ playwright, baseURL }) => {
    const anon = await playwright.request.newContext({ baseURL });
    const res = await anon.get('/api/camera/ice', { failOnStatusCode: false, maxRedirects: 0 });
    expect([301, 302, 401, 403]).toContain(res.status());
    await anon.dispose();
  });

  test('capability endpoint reports routing and relay availability', async ({ request }) => {
    const res = await request.get('/api/camera/capability/1', { failOnStatusCode: false });
    // 409 when the test account has no running instance — still a valid shape.
    expect([200, 409]).toContain(res.status());
    if (res.status() === 200) {
      const b = await res.json();
      expect(b).toHaveProperty('routed_via_connector');
      expect(b).toHaveProperty('webrtc');
      expect(b).toHaveProperty('relay_available');
      expect(b.fallback).toBe('snapshot');
    }
  });

  test('an offer for an unknown printer fails cleanly, never 500', async ({ request }) => {
    const res = await request.post('/api/camera/webrtc/999999', {
      data: { type: 'offer', sdp: 'v=0\r\n' },
      failOnStatusCode: false
    });
    expect(res.status()).toBeLessThan(500);
  });
});
