// Every route at desktop sizes: the page itself never scrolls (content scrolls inside its cards) and nothing is
// cut off: no element overflows a box that hides its overflow, and nothing sits outside the window. Each screen
// is saved to SHOTS_DIR (default test-results/screens) as <size>/<route>.png.
import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const ROUTES = [
  '/', '/new', '/find', '/find?platform=makerworld', '/licenses', '/models/1',
  '/farm', '/farm?view=printers', '/farm?view=cameras', '/farm?view=queue', '/farm?view=filament',
  '/slicing', '/slicing?tab=grid', '/slicing?tab=problems', '/slicing?tab=printers',
  '/orders', '/quotes', '/inventory', '/planner', '/reports',
  '/sync', '/people', '/people?view=hub', '/connections', '/devices',
  '/settings', '/settings?tab=slicing', '/settings?tab=sites', '/settings?tab=keys', '/settings?tab=crafts',
  '/crafts/laser', '/crafts/sublimation', '/crafts/graphic-art',
  '/setup', '/invite/not-a-real-invite'
];
const MOVED = { '/team': '/people', '/platform': '/people?view=hub', '/add-on': '/devices', '/tv?code=ABCD1234': '/devices?code=ABCD1234', '/printers': '/slicing?tab=printers' };
const OUT = process.env.SHOTS_DIR || 'test-results/screens';
const fileName = (r) => (r === '/' ? 'home' : r.slice(1).replace(/[/?=&]+/g, '_'));

// Runs in the page: what scrolls the page, and what is cut off.
function inspect() {
  const vw = innerWidth, vh = innerHeight, se = document.scrollingElement;
  const out = { pageScroll: se.scrollHeight > vh + 1 || se.scrollWidth > vw + 1, size: [se.scrollWidth, se.scrollHeight], clipped: [] };
  const name = (el) => `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}.${[...el.classList].slice(0, 3).join('.')} "${(el.innerText || '').trim().slice(0, 40)}"`;
  for (const el of document.body.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    // Hidden on purpose: screen-reader-only text (sr-only, SvelteKit's route announcer) and decoration.
    if (cs.display === 'none' || cs.visibility === 'hidden' || el.closest('.sr-only, #svelte-announcer, [aria-hidden="true"], svg')) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const hides = (v) => v === 'hidden' || v === 'clip';
    const ellipsis = cs.textOverflow === 'ellipsis' || cs.webkitLineClamp !== 'none' && cs.webkitLineClamp !== '';
    if (!ellipsis && el.tagName !== 'IMG' && el.tagName !== 'VIDEO' &&
        ((hides(cs.overflowX) && el.scrollWidth > el.clientWidth + 2) || (hides(cs.overflowY) && el.scrollHeight > el.clientHeight + 2))) {
      out.clipped.push(`overflows its hidden box: ${name(el)} (${el.scrollWidth}x${el.scrollHeight} in ${el.clientWidth}x${el.clientHeight})`);
    }
    // outside the window, with no scrolling box between it and the page
    if (r.right > vw + 1 || r.bottom > vh + 1) {
      let p = el.parentElement, boxed = false;
      while (p && p !== document.body) {
        const ps = getComputedStyle(p);
        if (ps.overflowX !== 'visible' || ps.overflowY !== 'visible' || ps.position === 'fixed') { boxed = true; break; }
        p = p.parentElement;
      }
      if (!boxed && cs.position !== 'fixed') out.clipped.push(`outside the window: ${name(el)} (${Math.round(r.right)}, ${Math.round(r.bottom)})`);
    }
  }
  return out;
}

for (const route of ROUTES) {
  test(`screen ${route}`, async ({ page }, info) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(route, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(400);
    const dir = `${OUT}/${info.project.name}`;
    mkdirSync(dir, { recursive: true });
    await page.screenshot({ path: `${dir}/${fileName(route)}.png` });
    const r = await page.evaluate(inspect);
    expect(r.pageScroll, `the page scrolls (${r.size.join(' x ')})`).toBe(false);
    expect(r.clipped, 'cut off').toEqual([]);
    expect(errors, 'script errors').toEqual([]);
    const families = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
    expect(families).toContain('Manrope');
  });
}

test('pages that moved redirect', async ({ page }) => {
  for (const [from, to] of Object.entries(MOVED)) {
    await page.goto(from, { waitUntil: 'networkidle' });
    expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe(to);
  }
});

test('the sidebar is the approved list, grouped', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' });
  const groups = await page.$$eval('.ah-sidebar__nav .ah-group-label', (els) => els.map((e) => e.textContent.trim()));
  expect(groups).toEqual(['Print', 'Library', 'Shop', 'Admin']);
  const items = await page.$$eval('.ah-sidebar__nav a.ah-nav', (els) => els.map((e) => e.textContent.replace(/\d+$/, '').trim()));
  expect(items).toEqual(['Farm overview', 'Printers', 'Cameras', 'Print queue', 'Filament', 'Slicing', 'Models', 'Add model',
    'Find models', 'Licenses', 'Orders', 'Quotes', 'Inventory', 'Shopping list', 'Reports', 'Sync health', 'People',
    'Connections', 'Devices', 'Settings']);
  await expect(page.locator('.ah-sidebar__nav a[aria-current="page"]')).toHaveText(/Models/);
});
