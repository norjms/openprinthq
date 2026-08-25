// A live stream and a still frame up to a minute old look identical on screen.
// The badge is the only thing that distinguishes them, which matters because a
// relayed snapshot silently passing for working live video is exactly the
// failure that is hardest to notice: everything appears fine while the video is
// crossing the server the design exists to bypass.
import { test, expect } from '@playwright/test';

test('the detail view says whether video is live or a snapshot', async ({ page, request }) => {
  const res = await request.get('/api/engine/api/v1/printers/', { failOnStatusCode: false });
  if (!res.ok()) test.skip(true, 'engine unavailable on this tier');
  const body = await res.json();
  const printers = Array.isArray(body) ? body : (body.printers || body.items || []);
  if (!printers.length) test.skip(true, 'no printers on this tier');

  // Pick a printer that actually has a camera rather than whichever happens to
  // be first. On a tier whose first printer has no camera there is no badge to
  // assert on, and the test was failing for a reason it does not care about.
  const printer = printers.find((p) => p.external_camera_enabled && p.external_camera_url) || printers[0];

  // Establish up front whether this tier has a camera that serves frames at all.
  // A missing badge otherwise conflates two very different things: the camera
  // panel is absent because the camera is unreachable (an environment condition
  // — a machine switched off, a camera off the network), or it is absent because
  // the UI broke, which is the regression this test exists to catch. Only the
  // second is a reason to fail. Asking first also keeps the whole test inside
  // its timeout, which waiting on the badge and then asking did not.
  const snap = await request.get(
    `/api/engine/api/v1/printers/${printer.id}/camera/snapshot?t=${Date.now()}`,
    { failOnStatusCode: false, timeout: 15000 }
  ).catch(() => null);
  const bytes = snap && snap.ok() ? (await snap.body()).length : 0;
  test.skip(
    bytes === 0,
    `camera for printer ${printer.id} serves no frames on this tier (snapshot HTTP ${snap ? snap.status() : 'error'}) — nothing to badge`
  );

  // Navigate directly: clicking through the list depends on card markup that is
  // not what this test is about.
  await page.goto(`/app/printers/${printer.id}`);
  await page.waitForLoadState('networkidle');

  // The camera serves frames, so a badge is required. Its absence is a real
  // regression, not an unavailable tier.
  const badge = page.locator('.badge').first();
  await expect(badge, `camera returns ${bytes} bytes but the page renders no LIVE/SNAPSHOT badge`)
    .toBeVisible({ timeout: 20000 });
  const text = (await badge.innerText()).trim();
  expect(['LIVE', 'SNAPSHOT'], `unexpected badge text: ${text}`).toContain(text);

  // Whichever mode it reports must match what is actually rendered, or the badge
  // is worse than no badge at all.
  const live = text === 'LIVE';
  const videoVisible = await page.locator('video:not(.hidden)').count();
  if (live) {
    expect(videoVisible, 'badge says LIVE but no video element is showing').toBeGreaterThan(0);

    // A visible video element is not a moving picture. The badge once flipped to
    // LIVE the instant a track was negotiated, which threw away the snapshot
    // fallback and left a paused element showing black, or a single frozen
    // frame, under a badge asserting live video. That is the precise failure
    // this whole badge exists to prevent, so assert on playback rather than on
    // the element's presence.
    const advanced = await page.evaluate(async () => {
      const v = document.querySelector('video:not(.hidden)');
      if (!v) return { ok: false, why: 'no video element' };
      const t0 = v.currentTime;
      await new Promise((r) => setTimeout(r, 1500));
      return { ok: !v.paused && v.videoWidth > 0 && v.currentTime > t0,
               paused: v.paused, w: v.videoWidth, t0, t1: v.currentTime };
    });
    expect(advanced.ok,
      `badge says LIVE but the video is not playing: ${JSON.stringify(advanced)}`).toBe(true);
  } else {
    expect(await page.locator('img[src*="camera/snapshot"]').count(),
      'badge says SNAPSHOT but no snapshot image is present').toBeGreaterThan(0);
  }
});

// The Cameras grid is the case that actually broke, and it differs from the
// detail page in the two ways that mattered: it is reachable without a click on
// a card first (so the browser may refuse to autoplay), and it mounts several
// videos at once. Every tile claiming LIVE must be playing.
test('every tile on the Cameras grid that claims LIVE is actually playing', async ({ page, request }) => {
  const res = await request.get('/api/engine/api/v1/printers/', { failOnStatusCode: false });
  if (!res.ok()) test.skip(true, 'engine unavailable on this tier');
  const body = await res.json();
  const printers = Array.isArray(body) ? body : (body.printers || body.items || []);
  if (!printers.length) test.skip(true, 'no printers on this tier');

  await page.goto('/app/cameras');
  await page.waitForLoadState('networkidle');

  // Give negotiation a fair chance; a tile still on snapshots is a valid state
  // and is not what this test is about.
  await page.waitForTimeout(12000);

  const verdict = await page.evaluate(async () => {
    const tiles = [...document.querySelectorAll('.feed')];
    const before = tiles.map((f) => {
      const b = f.parentElement.querySelector('.badge') || f.querySelector('.badge');
      const v = f.querySelector('video');
      return { live: !!(b && b.textContent.trim() === 'LIVE'), t: v ? v.currentTime : null };
    });
    await new Promise((r) => setTimeout(r, 1500));
    return tiles.map((f, i) => {
      const v = f.querySelector('video');
      return { i, claimsLive: before[i].live, paused: v ? v.paused : null,
               w: v ? v.videoWidth : null,
               advanced: v ? v.currentTime > before[i].t : null };
    });
  });

  const lying = verdict.filter((t) => t.claimsLive && !(t.advanced && !t.paused && t.w > 0));
  expect(lying, `tiles badged LIVE that are not playing: ${JSON.stringify(lying)}`).toEqual([]);
});
