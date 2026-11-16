// Farm > Map filament: a waiting job's model filaments, the printer's loaded filament for each, and the answer.
// The hub's answers are stood in for.
import { test, expect } from '@playwright/test';

const tray = (gid, label, type, color, remain = 80) => ({ global_tray_id: gid, label, type, color, remain });
const OPTS = {
  item_id: 7, job_name: 'Gear housing', status: 'pending', level: 'close', sliced_for_model: 'H2D',
  requirements: [{ slot_id: 1, type: 'PLA', color: '#000000', used_grams: 42 }, { slot_id: 2, type: 'PETG', color: '#FFFFFF', used_grams: 18 }],
  unmatched: { level: 'close', printers: [{ printer_id: 1, printer_name: 'H2D', summary: 'filament 2 PETG #FFFFFF (closest: PLA #FFFFFF)' }] },
  can_reslice: true, reslice_unavailable: null, reslice: null,
  printers: [
    { printer_id: 1, name: 'H2D', model: 'H2D', connected: true, reports_filament: true, same_model: true, match: { ok: false },
      loaded: [tray(0, 'AMS A slot 1', 'PLA', '#101010'), tray(1, 'AMS A slot 2', 'PLA', '#FFFFFF'), tray(2, 'AMS A slot 3', 'PETG', '#F4F4F4')],
      slots: [{ slot_id: 1, suggested: 0, grade: 'close', delta_e: 3.1 }, { slot_id: 2, suggested: 1, grade: 'different_material', delta_e: null }] },
  ],
};

for (const [w, h] of [[1920, 1080], [1280, 860]]) {
  test(`map a job's filament and start it (${w}x${h})`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    let posted = null;
    await page.route('**/api/farm/jobs/7/filament**', (r) => {
      if (r.request().method() === 'POST') {
        posted = r.request().postDataJSON();
        return r.fulfill({ json: { ok: true, started: true, reslice: false, printer: 'H2D' } });
      }
      return r.fulfill({ json: OPTS });
    });
    await page.goto('/farm/jobs/7/filament');
    await expect(page.getByText("Why it's waiting:")).toBeVisible();
    await expect(page.getByText('Different material: reslice')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reslice and start' })).toBeVisible();
    if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/map-filament-${w}.png` });
    // PETG is loaded in slot 3: pick it and the job starts without a reslice.
    await page.locator('select').nth(1).selectOption({ label: 'AMS A slot 3: PETG #F4F4F4 · 80% left' });
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole('button', { name: 'Start', exact: true }).click();
    await expect(page.getByText('Gear housing is starting on H2D')).toBeVisible();
    expect(posted).toEqual({ printer_id: 1, slots: [{ slot_id: 1, global_tray_id: 0 }, { slot_id: 2, global_tray_id: 2 }], remember: true });
  });
}

test('the same loaded filament twice is refused', async ({ page }) => {
  await page.route('**/api/farm/jobs/7/filament**', (r) => r.fulfill({ json: OPTS }));
  await page.goto('/farm/jobs/7/filament');
  await page.locator('select').nth(1).selectOption({ label: 'AMS A slot 1: PLA #101010 · 80% left' });
  await expect(page.getByText('Used twice').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reslice and start' })).toBeDisabled();
});
