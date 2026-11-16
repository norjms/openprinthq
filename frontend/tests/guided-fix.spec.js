// Guided fixes the hub describes: the ways to fix an alert, their values, and the button that runs one on the hub.
import { test, expect } from '@playwright/test';

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
const NOW = new Date().toISOString();
const base = { state: 'open', urgent: true, count: 1, created_at: NOW, updated_at: NOW, fixable: 'guided', link: '/farm' };
const ALARM = { ...base, id: 21, kind: 'alarm', title: 'H2C alarm: The nozzle is clogged', message: 'The nozzle is clogged',
  subject: { printer: 'H2C', code: '0300-1A00-0002-0001' },
  context: { kind: 'guided', note: null, camera: 'H2C', photos: [],
    summary: [['Printer', 'H2C'], ['Code', '0300-1A00-0002-0001'], ['What it means', 'The nozzle is clogged'], ['Now', 'paused'], ['Print', 'Gear housing']],
    paths: [
      { id: 'resume', title: 'I fixed it: resume the print', detail: 'Clear the alarm and carry on printing.', icon: 'play', button: 'Resume', closes: true, fields: [], open: null, off: null },
      { id: 'clear', title: 'Clear the alarm', detail: 'Take it off the alarm list.', icon: 'clear', button: 'Clear it', closes: true, fields: [], open: null, off: null },
      { id: 'mute', title: 'Stop alerts for this code on H2C', detail: 'No alarm or notification for it again.', icon: 'mute', button: 'Stop these alerts', closes: true, confirm: 'Stop alerts for 0300-1A00-0002-0001 on H2C?', fields: [], open: null, off: null },
      { id: 'stop', title: 'Stop the print', detail: 'Cancel it.', icon: 'stop', button: 'Stop the print', closes: true, confirm: 'Stop Gear housing on H2C?', fields: [], open: null, off: null },
      { id: 'task', title: 'Open its maintenance task', detail: 'The alarm opened a maintenance task.', icon: 'wrench', button: 'Open maintenance', closes: false, fields: [], open: { web: '/farm', app: 'farm:maintenance' }, off: null },
    ] } };
const DONE = { ...base, id: 22, urgent: false, kind: 'print_done', title: 'H2D finished Planter', message: 'Clear the plate and pass the check.',
  subject: { printer: 'H2D', check: 7 },
  context: { kind: 'guided', note: null, camera: 'H2D', photos: [{ label: 'When it ended', url: '/api/plate-checks/7/photo' }],
    summary: [['Printer', 'H2D'], ['Print', 'Planter'], ['How it ended', 'Finished']],
    paths: [
      { id: 'pass', title: 'The print is good and the plate is clear', detail: 'Pass the plate check.', icon: 'check', button: 'Pass the plate check', closes: true, fields: [], open: null, off: null },
      { id: 'fail', title: 'Something is wrong with the print', detail: 'Fail the plate check with a reason.', icon: 'report', button: 'Fail the plate check', closes: true, open: null, off: null,
        fields: [{ name: 'reason', label: 'What went wrong', type: 'choice', options: [['spaghetti', 'Spaghetti or came loose'], ['layer_shift', 'Layer shift']], value: 'spaghetti' },
          { name: 'note', label: 'Note', type: 'text', hint: 'Optional', value: '' },
          { name: 'reprint', label: 'Print it again', type: 'switch', value: true }] },
    ] } };

async function stub(page, posted) {
  await page.route('**/api/alerts/21', (r) => r.fulfill({ json: ALARM }));
  await page.route('**/api/alerts/22', (r) => r.fulfill({ json: DONE }));
  await page.route('**/api/alerts/*/do/*', (r) => {
    const u = r.request().url();
    posted.push([u.split('/do/')[1], r.request().postDataJSON()]);
    const a = u.includes('/21/') ? ALARM : DONE;
    return r.fulfill({ json: { message: 'Done', closed: true, alert: { ...a, state: 'fixed', closed_at: NOW, closed_by: 'dev', fix: 'alarm cleared' } } });
  });
  await page.route('**/api/mobile/v1/printers/*/snapshot*', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG }));
  await page.route('**/api/plate-checks/7/photo', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG }));
}

test('an alarm: the ways to fix it, then one clears it', async ({ page }, info) => {
  const posted = [];
  await stub(page, posted);
  await page.goto('/alerts/21');
  await page.getByRole('button', { name: 'Fix it' }).click();
  const fix = page.getByTestId('guided-fix');
  for (const t of ['I fixed it: resume the print', 'Clear the alarm', 'Stop alerts for this code on H2C', 'Stop the print', 'Open its maintenance task']) {
    await expect(fix.getByText(t, { exact: true })).toBeVisible();
  }
  await expect(fix.getByText('The nozzle is clogged').first()).toBeVisible();
  await fix.getByText('Open its maintenance task', { exact: true }).click();
  await expect(fix.getByRole('link', { name: 'Open maintenance' })).toHaveAttribute('href', '/farm');
  await fix.getByText('Clear the alarm', { exact: true }).click();
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/guided-alarm-${info.project.name}.png` });
  await fix.getByRole('button', { name: 'Clear it' }).click();
  await expect(page.getByTestId('alert-head').getByText('Fixed', { exact: true })).toBeVisible();
  expect(posted).toEqual([['clear', { values: {} }]]);
});

test('a stop asks first', async ({ page }) => {
  const posted = [];
  await stub(page, posted);
  await page.goto('/alerts/21');
  await page.getByRole('button', { name: 'Fix it' }).click();
  const fix = page.getByTestId('guided-fix');
  await fix.getByText('Stop the print').first().click();
  page.once('dialog', (d) => d.dismiss());
  await fix.getByRole('button', { name: 'Stop the print' }).click();
  expect(posted).toEqual([]);
  page.once('dialog', (d) => d.accept());
  await fix.getByRole('button', { name: 'Stop the print' }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0][0]).toBe('stop');
});

test('a finished print: fail the plate check with a reason', async ({ page }, info) => {
  const posted = [];
  await stub(page, posted);
  await page.goto('/alerts/22');
  await page.getByRole('button', { name: 'Fix it' }).click();
  const fix = page.getByTestId('guided-fix');
  await fix.getByText('Something is wrong with the print').click();
  await fix.getByLabel('What went wrong').selectOption('layer_shift');
  await fix.getByLabel('Note').fill('Shifted at layer 40');
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/guided-plate-${info.project.name}.png` });
  await fix.getByRole('button', { name: 'Fail the plate check' }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(['fail', { values: { reason: 'layer_shift', note: 'Shifted at layer 40', reprint: true } }]);
});
