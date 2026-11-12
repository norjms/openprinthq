// The farm draws the hub's sidebar: from the hub's GET /api/nav/v1 when the hub
// answers, from its built-in copy when it does not. Farm pages link inside the
// farm (under the base path); hub pages link to the hub's root.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { test, expect } from '@playwright/test';

const BASE = process.env.BASE_PATH || '';
const port = Number(process.env.SCREENS_PORT || 4173);
const NO_HUB = `http://127.0.0.1:${port}${BASE}`;
const WITH_HUB = `http://127.0.0.1:${port + 1}${BASE}`;

const GROUPS = {
  Print: ['Farm overview', 'Printers', 'Cameras', 'Print queue', 'Filament', 'Timelapses', 'Slicing'],
  Library: ['Models', 'Add model', 'Find models', 'Licenses'],
  Shop: ['Orders', 'Quotes', 'Inventory', 'Shopping list', 'Reports'],
  Admin: ['Sync health', 'People', 'Connections', 'Devices', 'Settings']
};

async function sidebar(page) {
  return page.locator('.ah-sidebar__nav').evaluate((nav) => {
    const out = [];
    for (const el of nav.children) {
      if (el.classList.contains('ah-group-label')) out.push({ label: el.textContent.trim(), items: [] });
      else if (el.matches('a.ah-nav')) {
        const c = el.cloneNode(true);
        c.querySelectorAll('.ah-badge').forEach((b) => b.remove());
        out.at(-1).items.push({ label: c.textContent.trim(), href: el.getAttribute('href'), current: el.getAttribute('aria-current') === 'page' });
      }
    }
    return out;
  });
}

for (const [name, origin] of [['the hub unreachable', NO_HUB], ['the hub answering', WITH_HUB]]) {
  test.describe(`with ${name}`, () => {
    test('the sidebar is the hub\'s, with Timelapses after Filament', async ({ page }) => {
      await page.goto(origin + '/app/printers', { waitUntil: 'networkidle' });
      const groups = await sidebar(page);
      expect(groups.map((g) => g.label)).toEqual(Object.keys(GROUPS));
      for (const g of groups) expect(g.items.map((i) => i.label), g.label).toEqual(GROUPS[g.label]);
    });

    test('farm pages stay in the farm and hub pages go to the hub', async ({ page }) => {
      await page.goto(origin + '/app/printers', { waitUntil: 'networkidle' });
      const items = (await sidebar(page)).flatMap((g) => g.items);
      const href = (l) => items.find((i) => i.label === l).href;
      expect(href('Printers')).toBe(BASE + '/app/printers');
      expect(href('Cameras')).toBe(BASE + '/app/cameras');
      expect(href('Print queue')).toBe(BASE + '/app/queue');
      expect(href('Filament')).toBe(BASE + '/app/filament');
      expect(href('Timelapses')).toBe(BASE + '/app/timelapses');
      expect(href('Farm overview')).toBe('/farm');
      expect(href('Orders')).toBe('/orders');
      expect(href('Settings')).toBe('/settings');
      expect(items.filter((i) => i.current).map((i) => i.label)).toEqual(['Printers']);
      await expect(page.locator('.ah-header__crumbs')).toHaveText(/Print\s*\/\s*Printers/);
    });

    test('a farm item opens inside the farm', async ({ page }) => {
      await page.goto(origin + '/app/printers', { waitUntil: 'networkidle' });
      await page.locator('a.ah-nav', { hasText: 'Timelapses' }).click();
      await expect(page).toHaveURL(origin + '/app/timelapses');
      await expect(page.locator('h1')).toHaveText('Timelapses');
      await expect(page.locator('a.ah-nav[aria-current="page"]')).toHaveText(/Timelapses/);
    });
  });
}

test('the hub names the installation and counts for its badges', async ({ page }) => {
  await page.goto(WITH_HUB + '/app/printers', { waitUntil: 'networkidle' });
  await expect(page.locator('.inst')).toHaveText('Artisan Prints');
  await expect(page.locator('a.ah-nav', { hasText: 'Orders' }).locator('.ah-badge')).toHaveText('3');
  await expect(page.locator('a.ah-nav', { hasText: 'Printers' }).locator('.ah-badge')).toHaveText('4');
});

test('without the hub the header falls back to ArtisanHub', async ({ page }) => {
  await page.goto(NO_HUB + '/app/printers', { waitUntil: 'networkidle' });
  await expect(page.locator('.inst')).toHaveText('ArtisanHub');
  await expect(page.locator('a.ah-nav', { hasText: 'Orders' }).locator('.ah-badge')).toHaveCount(0);
});

test('farm pages outside the sidebar are in the jump-to palette', async ({ page }) => {
  await page.goto(NO_HUB + '/app/printers', { waitUntil: 'networkidle' });
  await page.keyboard.press('Control+k');
  await page.locator('#jt-q').fill('statis');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(NO_HUB + '/app/statistics');
  await expect(page.locator('.ah-header__crumbs')).toHaveText(/Farm\s*\/\s*Statistics/);
});
