// A desktop app inside the hub (docs/apps.md): its sidebar item opens /open/<id>, where the hub's shell stays and
// the desktop shows below a bar with Pop out, Open in a new tab and Full screen. The test hub runs no desktop, so
// the sidebar is the hub's answer with one app added, and the frame's address answers a plain page.
import { test, expect } from '@playwright/test';

const APP = { id: 'app-bambustudio', label: 'Bambu Studio', href: '/open/bambustudio', icon: 'tv', app: 'hub',
              frame: '/apps/bambustudio/' };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/nav/v1', async (route) => {
    const doc = await (await route.fetch()).json();
    doc.groups[0].items.push(APP);
    await route.fulfill({ json: doc });
  });
  await page.route('**/apps/bambustudio/', (route) =>
    route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>desk</title><p>the desktop</p>' }));
});

test('a desktop app opens inside the hub, with pop out, a new tab and full screen', async ({ page }) => {
  await page.goto('/farm', { waitUntil: 'networkidle' });
  await page.locator('.ah-sidebar__nav').getByText('Bambu Studio', { exact: true }).click();
  await expect(page).toHaveURL(/\/open\/bambustudio$/);
  // The hub's sidebar is still there, and the desktop is in the page.
  await expect(page.locator('.ah-sidebar__nav')).toBeVisible();
  const ws = page.getByTestId('app-workspace');
  await expect(ws.getByText('Bambu Studio', { exact: true })).toBeVisible();
  await expect(page.frameLocator('[data-testid="app-frame"] iframe').getByText('the desktop')).toBeVisible();
  await expect(page.getByTestId('new-tab')).toHaveAttribute('href', '/apps/bambustudio/');
  await expect(page.getByTestId('new-tab')).toHaveAttribute('target', '_blank');
  // The desktop fills the page below the header, and the page itself never scrolls (as every hub screen).
  const fits = await page.evaluate(() => document.scrollingElement.scrollHeight <= innerHeight + 1);
  expect(fits).toBe(true);
  const out = process.env.SHOTS_DIR || 'test-results/screens';
  await page.screenshot({ path: `${out}/${test.info().project.name}/open_bambustudio.png` });

  await page.getByTestId('full-screen').click();
  await expect(ws).toHaveClass(/full/);
  await page.keyboard.press('Escape');
  await expect(ws).not.toHaveClass(/full/);

  const [popup] = await Promise.all([page.waitForEvent('popup'), page.getByTestId('pop-out').click()]);
  await expect(page.getByText('Bambu Studio is open in its own window')).toBeVisible();
  await expect(page.getByTestId('app-frame')).toHaveCount(0);
  await popup.close();
  await expect(page.getByTestId('app-frame')).toBeVisible({ timeout: 5000 });
});

test('a file sent from the farm is named while the desktop opens it', async ({ page }) => {
  await page.goto('/open/bambustudio?sent=benchy.3mf', { waitUntil: 'networkidle' });
  await expect(page.getByTestId('app-sent')).toContainText('benchy.3mf');
});

test('an app this hub does not run says so', async ({ page }) => {
  await page.goto('/open/nothing', { waitUntil: 'networkidle' });
  await expect(page.getByText('This hub has no app called nothing')).toBeVisible({ timeout: 6000 });
});

test('a tab in the background lets go of the desktop, and connects again when it is back', async ({ page }) => {
  await page.clock.install();
  await page.goto('/open/bambustudio', { waitUntil: 'networkidle' });
  await expect(page.getByTestId('app-frame')).toBeVisible();
  const hide = (state) => page.evaluate((s) => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => s });
    document.dispatchEvent(new Event('visibilitychange'));
  }, state);
  await hide('hidden');
  await page.clock.fastForward(20_000);
  await expect(page.getByTestId('app-frame')).toBeVisible();
  await page.clock.fastForward(11_000);
  await expect(page.getByTestId('app-away')).toBeVisible();
  await expect(page.getByTestId('app-frame')).toHaveCount(0);
  await hide('visible');
  await expect(page.frameLocator('[data-testid="app-frame"] iframe').getByText('the desktop')).toBeVisible();
});
