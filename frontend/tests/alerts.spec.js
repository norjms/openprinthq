// Alerts: the inbox, one alert with Fix it and Dismiss, the two guided fixes, and each person's choices. The hub's
// answers are stood in for.
import { test, expect } from '@playwright/test';

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
const NOW = new Date().toISOString();
const FIL = { id: 11, kind: 'waiting_filament', title: 'Filament needs mapping: Planter', message: "Filament doesn't match: H2D: filament 2 PETG #FFFFFF.",
  state: 'open', urgent: false, count: 1, created_at: NOW, updated_at: NOW, fixable: 'filament', subject: { job: '42' }, link: '/farm/jobs/42/filament',
  context: { kind: 'filament', job: '42', job_name: 'Planter', job_status: 'waiting_filament', printer: 'H2D', model_id: 7 } };
const READY = { id: 12, kind: 'waiting_ready', title: 'H2D: 2 jobs waiting', message: "Gear housing and 1 more can't start on H2D: its plate needs clearing.",
  state: 'open', urgent: false, count: 1, created_at: NOW, updated_at: NOW, fixable: 'ready', subject: { printer: 'H2D' }, link: '/farm/printers/H2D/ready',
  context: { kind: 'ready', printer: 'H2D', check: { id: 7, job_name: 'Planter', photo: '/api/plate-checks/7/photo' },
    waiting: [{ external_id: '1', name: 'Gear housing' }, { external_id: '2', name: 'Bracket' }] } };
const ALARM = { id: 13, kind: 'alarm', title: 'H2D alarm: nozzle clog', message: '0300-1A00', state: 'open', urgent: true, count: 3,
  created_at: NOW, updated_at: NOW, fixable: null, subject: {}, link: '/farm?view=alarms', context: { kind: null } };
const MAP = {
  requirements: [{ slot_id: 1, type: 'PLA', color: '#000000', used_grams: 42 }, { slot_id: 2, type: 'PETG', color: '#FFFFFF', used_grams: 18 }],
  printers: [
    { printer_id: 1, name: 'H2D', same_model: true, connected: true, match: { ok: false },
      loaded: [{ global_tray_id: 0, label: 'AMS A slot 1', type: 'PLA', color: '#101010', remain: 80 }, { global_tray_id: 1, label: 'AMS A slot 2', type: 'PLA', color: '#FFFFFF', remain: 20 }],
      slots: [{ slot_id: 1, suggested: 0, grade: 'close' }, { slot_id: 2, suggested: 1, grade: 'different_material' }] },
    { printer_id: 2, name: 'H2C', same_model: true, connected: true, match: { ok: true },
      loaded: [{ global_tray_id: 0, label: 'AMS A slot 1', type: 'PETG', color: '#FFFFFF', remain: 90 }], slots: [] }],
};

async function stub(page, posted) {
  await page.route('**/api/alerts?state=open', (r) => r.fulfill({ json: { alerts: [ALARM, FIL, READY], open: 3, urgent: 1 } }));
  await page.route('**/api/alerts?state=closed', (r) => r.fulfill({ json: { alerts: [], open: 3, urgent: 1 } }));
  await page.route('**/api/alerts/11', (r) => r.fulfill({ json: FIL }));
  await page.route('**/api/alerts/12', (r) => r.fulfill({ json: READY }));
  await page.route('**/api/alerts/13', (r) => r.fulfill({ json: ALARM }));
  await page.route('**/api/alerts/*/fixed', (r) => { posted.push(['fixed', r.request().postDataJSON().how]); return r.fulfill({ json: { ...FIL, state: 'fixed' } }); });
  await page.route('**/api/alerts/13/dismiss', (r) => { posted.push(['dismiss']); return r.fulfill({ json: { ...ALARM, state: 'dismissed', closed_at: NOW, closed_by: 'dev' } }); });
  await page.route('**/api/alerts/11/want-filament', (r) => { posted.push(['want', r.request().postDataJSON()]); return r.fulfill({ json: { ok: true } }); });
  await page.route('**/api/farm/jobs/42/filament?all_printers=true', (r) => r.fulfill({ json: MAP }));
  await page.route('**/api/farm/jobs/42/move', (r) => { posted.push(['move', r.request().postDataJSON()]); return r.fulfill({ json: { ok: true, printer: 'H2C' } }); });
  await page.route('**/api/mobile/v1/printers', (r) => r.fulfill({ json: { printers: [{ id: 1, name: 'H2D', online: true, ready: false }, { id: 2, name: 'H2C', online: true, ready: true }] } }));
  await page.route('**/api/mobile/v1/plate-checks', (r) => r.fulfill({ json: { waiting: [], reasons: { spaghetti: 'Spaghetti or came loose', other: 'Other' } } }));
  await page.route('**/api/mobile/v1/plate-checks/7/pass', (r) => { posted.push(['pass']); return r.fulfill({ json: { ok: true } }); });
  await page.route('**/api/mobile/v1/printers/H2D/snapshot*', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG }));
  await page.route('**/api/plate-checks/7/photo', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG }));
}

test('the inbox lists open alerts and opens one', async ({ page }, info) => {
  await stub(page, []);
  await page.goto('/alerts');
  const list = page.getByTestId('alert-list');
  await expect(list.getByText('H2D alarm: nozzle clog')).toBeVisible();
  await expect(list.getByText('Urgent')).toBeVisible();
  await expect(list.getByText('Fix it')).toHaveCount(2);
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/alerts-inbox-${info.project.name}.png` });
  await list.getByText('Filament needs mapping: Planter').click();
  await expect(page).toHaveURL(/\/alerts\/11$/);
});

test('an alert without a guided fix: Fix it goes there, Dismiss closes it', async ({ page }) => {
  const posted = [];
  await stub(page, posted);
  await page.goto('/alerts/13');
  await expect(page.getByRole('link', { name: 'Fix it' })).toHaveAttribute('href', '/farm?view=alarms');
  await page.getByRole('button', { name: 'Dismiss' }).click();
  await expect(page.getByTestId('alert-head')).toContainText('Dismissed');
  expect(posted).toEqual([['dismiss']]);
});

test('filament fix: run it on a printer that has the filament', async ({ page }, info) => {
  const posted = [];
  await stub(page, posted);
  await page.goto('/alerts/11');
  await page.getByRole('button', { name: 'Fix it' }).click();
  const fix = page.getByTestId('filament-fix');
  await fix.getByText('Load the right filament').click();
  await expect(page.getByTestId('fix-panel')).toContainText('Load PETG #FFFFFF into H2D');
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/alerts-filament-fix-${info.project.name}.png` });
  await fix.getByText('Order filament').click();
  await page.getByTestId('fix-panel').getByRole('button', { name: 'Add to shopping list' }).first().click();
  await expect(page.getByTestId('fix-panel').getByText('On the shopping list')).toBeVisible();
  await fix.getByText('Run it on another printer').click();
  await page.getByTestId('fix-panel').getByRole('button', { name: 'Move here' }).click();
  await expect.poll(() => posted.map((p) => p[0])).toEqual(['want', 'move', 'fixed']);
  expect(posted[0][1]).toMatchObject({ material: 'PETG', color: '#FFFFFF', grams: 18 });
  expect(posted[1][1]).toEqual({ printer_id: 2 });
});

test('ready fix: the plate is clear', async ({ page }, info) => {
  const posted = [];
  await stub(page, posted);
  await page.goto('/alerts/12');
  await page.getByRole('button', { name: 'Fix it' }).click();
  await page.getByTestId('ready-fix').getByText('The plate is clear').click();
  await expect(page.getByAltText('Plate after the last print on H2D')).toBeVisible();
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/alerts-ready-fix-${info.project.name}.png` });
  await page.getByRole('button', { name: 'Plate is clear: pass' }).click();
  await expect.poll(() => posted.map((p) => p[0])).toEqual(['pass', 'fixed']);
});

test('what I get: turn a kind off', async ({ page }) => {
  let choices = { groups: [{ id: 'prints', label: 'Print updates', kinds: [{ kind: 'print_started', label: 'Print started', urgent: false, on: true }] }] };
  const sent = [];
  await page.route('**/api/alerts/choices', (r) => {
    if (r.request().method() === 'PUT') { sent.push(r.request().postDataJSON()); choices.groups[0].kinds[0].on = false; }
    return r.fulfill({ json: choices });
  });
  await page.goto('/alerts?tab=choices');
  await page.getByTestId('alert-choices').getByLabel('Print started').uncheck();
  await expect.poll(() => sent).toEqual([{ on: { print_started: false } }]);
});
