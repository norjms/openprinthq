// Runs in the UNAUTHENTICATED project. It has to: the authenticated project
// carries a storageState cookie jar, and a context created inside it still
// presents those cookies, so "anonymous" there isn't anonymous at all.
//
// ICE responses can carry short-lived TURN credentials, which are billable and
// tied to the deployment's Cloudflare account. They must never be handed to an
// unauthenticated caller.
import { test, expect } from '@playwright/test';

test('anonymous callers never receive ICE credentials', async ({ request }) => {
  const res = await request.get('/api/camera/ice', { failOnStatusCode: false });
  let body = null;
  try { body = await res.json(); } catch { /* SSO login HTML — also fine */ }
  expect(body?.iceServers, 'ICE credentials leaked to an anonymous caller').toBeUndefined();
});

test('anonymous callers cannot open a WebRTC session', async ({ request }) => {
  const res = await request.post('/api/camera/webrtc/1', {
    data: { type: 'offer', sdp: 'v=0\r\n' },
    failOnStatusCode: false
  });
  let body = null;
  try { body = await res.json(); } catch { /* login page */ }
  expect(body?.answer, 'unauthenticated caller got a WebRTC answer').toBeUndefined();
  expect(body?.sdp, 'unauthenticated caller got an SDP answer').toBeUndefined();
});
