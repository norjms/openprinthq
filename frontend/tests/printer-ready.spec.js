// Farm > a printer's Get ready page, which the "jobs waiting" alert opens: the plate photos, the waiting jobs, and
// one button to pass the plate check (or set the printer ready). The hub's answers are stood in for.
import { test, expect } from '@playwright/test';

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');

for (const withCheck of [false, true]) {
  test(`get a printer ready ${withCheck ? 'by passing its plate check' : 'by setting it ready'}`, async ({ page }, info) => {
    let ready = false;
    const posted = [];
    await page.route('**/api/mobile/v1/printers/H2D', (r) => r.fulfill({ json: { name: 'H2D', online: true, busy: false, ready, state: 'finish' } }));
    await page.route('**/api/mobile/v1/printers/H2D/snapshot*', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG }));
    await page.route('**/api/plate-checks/7/photo', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG }));
    await page.route('**/api/mobile/v1/plate-checks', (r) => r.fulfill({ json: { waiting: withCheck && !ready ? [{ id: 7, printer: 'H2D', job_name: 'Planter', photo: '/api/plate-checks/7/photo' }] : [], recent: [] } }));
    await page.route('**/api/mobile/v1/queue', (r) => r.fulfill({ json: { jobs: [
      { id: 1, name: 'Gear housing', status: 'queued', printer: 'H2D', material: 'PLA' },
      { id: 2, name: 'Bracket', status: 'queued', printer: 'H2D', material: 'PETG' },
      { id: 3, name: 'Elsewhere', status: 'queued', printer: 'MK4S' }] } }));
    await page.route('**/api/mobile/v1/plate-checks/7/pass', (r) => { posted.push('pass'); ready = true; return r.fulfill({ json: { ok: true } }); });
    await page.route('**/api/mobile/v1/printers/H2D/command', (r) => { posted.push(r.request().postDataJSON().command); ready = true; return r.fulfill({ json: { ok: true } }); });
    await page.goto('/farm/printers/H2D/ready');
    await expect(page.getByTestId('ready-state')).toContainText('Gear housing and 1 more are waiting for H2D');
    await expect(page.getByText('Elsewhere')).toHaveCount(0);
    if (withCheck) await expect(page.getByAltText('Plate after the last print on H2D')).toBeVisible();
    if (process.env.SHOTS_DIR && withCheck) await page.screenshot({ path: `${process.env.SHOTS_DIR}/printer-ready-${info.project.name}.png` });
    await page.getByRole('button', { name: withCheck ? 'Plate is clear: pass' : 'Plate is clear: set ready' }).click();
    await expect(page.getByTestId('ready-state')).toContainText('H2D is ready');
    expect(posted).toEqual([withCheck ? 'pass' : 'set_ready']);
  });
}
