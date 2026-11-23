// The Farm group in the sidebar, and the reset page (/account/password), at both desktop sizes: no page scroll,
// nothing cut off. The Farm group shows only when the hub links to an installed farm, so its check answers
// /api/nav/v1 the way a hub with the farm at /apps/farm does. The reset page shows its form when the hub
// has mail and LLDAP set up (RESET_FORM=1) and the "ask the hub's owner" note otherwise.
import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const OUT = process.env.SHOTS_DIR || 'test-results/screens';
const FARM = 'https://hub.example/apps/farm';

function fits() {
  const vw = innerWidth, vh = innerHeight, se = document.scrollingElement;
  const clipped = [];
  for (const el of document.body.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || el.closest('.sr-only, #svelte-announcer, [aria-hidden="true"], svg')) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const hides = (v) => v === 'hidden' || v === 'clip';
    if (cs.textOverflow !== 'ellipsis' && ((hides(cs.overflowX) && el.scrollWidth > el.clientWidth + 2) || (hides(cs.overflowY) && el.scrollHeight > el.clientHeight + 2))) clipped.push(el.tagName + '.' + el.className);
    if (r.right > vw + 1 || r.bottom > vh + 1) {
      let p = el.parentElement, boxed = false;
      while (p && p !== document.body) { const ps = getComputedStyle(p); if (ps.overflowX !== 'visible' || ps.overflowY !== 'visible' || ps.position === 'fixed') { boxed = true; break; } p = p.parentElement; }
      if (!boxed && cs.position !== 'fixed') clipped.push('outside: ' + el.tagName + '.' + el.className);
    }
  }
  return { pageScroll: se.scrollHeight > vh + 1 || se.scrollWidth > vw + 1, clipped };
}

async function shot(page, info, name) {
  const dir = `${OUT}/${info.project.name}`;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/${name}.png` });
}

test('the Farm group follows Print when the hub links to an installed farm', async ({ page }, info) => {
  await page.route('**/api/nav/v1', async (route) => {
    const list = await (await page.request.get('/nav.json')).json();
    // As the hub answers with no optional craft on: the crafts' groups are left out (docs/crafts.md).
    const groups = list.groups.filter((g) => !g.craft).map((g) => ({ ...g, items: g.items.map((i) => (i.farm_path
      ? { ...i, href: FARM + i.farm_path, app: 'farm' } : { ...i, app: 'hub' })) }));
    await route.fulfill({ json: { version: 1, product: 'OpenPrintHQ', installation: 'Artisan Prints',
      farm: { kind: 'openprinthq', label: 'Farm', app: FARM }, groups } });
  });
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await expect(page.locator('.ah-sidebar__nav .ah-group-label')).toHaveText(['Print', 'Farm', 'Library', 'Shop', 'Admin']);
  const farm = await page.$$eval('.ah-sidebar__nav > *', (els) => {
    const out = []; let g = null;
    for (const el of els) { if (el.classList.contains('ah-group-label')) g = el.textContent.trim(); else if (g === 'Farm' && el.matches('a.ah-nav')) out.push([el.textContent.trim(), el.getAttribute('href')]); }
    return out;
  });
  expect(farm).toEqual([
    ['Files', FARM + '/app/files'], ['Slicer', FARM + '/app/slicer'], ['Projects', FARM + '/app/projects'],
    ['Statistics', FARM + '/app/statistics'], ['Print reports', FARM + '/app/reports'],
    ['Timelapses', FARM + '/app/timelapses'], ['Farm settings', FARM + '/app/settings']
  ]);
  await shot(page, info, 'sidebar-with-farm');
  const r = await page.evaluate(fits);
  expect(r.pageScroll, 'the page scrolls').toBe(false);
  expect(r.clipped, 'cut off').toEqual([]);
  // The last item can be reached: the sidebar scrolls inside itself when the list is taller than the window.
  const settings = page.locator('.ah-sidebar__nav a.ah-nav', { hasText: /^Settings$/ });
  await settings.scrollIntoViewIfNeeded();
  await expect(settings).toBeInViewport();
});

test('with every craft on, their groups follow Farm and the sidebar still fits', async ({ page }, info) => {
  await page.route('**/api/nav/v1', async (route) => {
    const list = await (await page.request.get('/nav.json')).json();
    const groups = list.groups.map((g) => ({ ...g, items: g.items.map((i) => (i.farm_path
      ? { ...i, href: FARM + i.farm_path, app: 'farm' } : { ...i, app: i.app || 'hub' })) }));
    await route.fulfill({ json: { version: 1, product: 'OpenPrintHQ', installation: 'Artisan Prints',
      farm: { kind: 'openprinthq', label: 'Farm', app: FARM }, groups } });
  });
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await expect(page.locator('.ah-sidebar__nav .ah-group-label')).toHaveText(
    ['Print', 'Farm', 'Laser Crafting', 'Sublimation', 'Graphic Art', 'Library', 'Shop', 'Admin']);
  await expect(page.locator('.ah-sidebar__nav a.ah-nav', { hasText: /^Convert files$/ })).toHaveAttribute('href', '/apps/laser/');
  await expect(page.locator('.ah-sidebar__nav a.ah-nav', { hasText: /^Sublimation tools$/ })).toHaveAttribute('href', '/crafts/sublimation');
  await shot(page, info, 'sidebar-with-crafts');
  const r = await page.evaluate(fits);
  expect(r.pageScroll, 'the page scrolls').toBe(false);
  expect(r.clipped, 'cut off').toEqual([]);
  const settings = page.locator('.ah-sidebar__nav a.ah-nav', { hasText: /^Settings$/ });
  await settings.scrollIntoViewIfNeeded();
  await expect(settings).toBeInViewport();
});

test('without an installed farm there is no Farm group', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' });
  await expect(page.locator('.ah-sidebar__nav .ah-group-label')).toHaveText(['Print', 'Library', 'Shop', 'Admin']);
});

test('the reset page', async ({ page }, info) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/account/password', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await expect(page.locator('h1')).toHaveText('Reset your password');
  const families = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
  expect(families).toContain('Manrope');
  if (process.env.RESET_FORM === '1') {
    await expect(page.getByLabel('Username or email')).toBeVisible();
    await shot(page, info, 'account-password');
    let r = await page.evaluate(fits);
    expect(r.pageScroll).toBe(false);
    expect(r.clipped).toEqual([]);
    await page.getByLabel('Username or email').fill('nobody-at-all');
    await page.getByRole('button', { name: 'Send reset link' }).click();
    await expect(page.getByRole('status')).toHaveText('If that account exists, a link is on its way. It works once, for 30 minutes.');
    await shot(page, info, 'account-password-sent');
    r = await page.evaluate(fits);
    expect(r.pageScroll).toBe(false);
  } else {
    await expect(page.locator('form')).toHaveCount(0);
    await expect(page.locator('main')).toContainText("Ask the hub's owner");
    await shot(page, info, 'account-password-no-mail');
    const r = await page.evaluate(fits);
    expect(r.pageScroll).toBe(false);
    expect(r.clipped).toEqual([]);
  }
  expect(errors).toEqual([]);
});
