// Farm > Settings > Phone app notifications: the disclaimer from the relay's own status, a person's choice and
// phones, and the installation switch. The push answers are stood in for.
import { test, expect } from '@playwright/test';

const STATE = {
  mode: 'on', relay_url: 'https://push.example.com', available: true, choice: 'on', mapping_in_app: true,
  this_device: null,
  devices: [{ id: 3, platform: 'apns', label: "James's iPhone", app_version: '0.9.0', created_at: '2026-11-16T18:00:00Z',
    last_seen_at: '2026-11-16T18:00:00Z', last_sent_at: '2026-11-16T19:10:00Z', last_error: null }],
  disclaimer: {
    title: 'How phone notifications work',
    points: [
      'Your hub encrypts every notification for your phone before it leaves the hub. Only this phone has the key that opens it; it is made on the phone and never leaves it.',
      'The push relay (push.example.com) passes the sealed message to Apple or Google. It cannot read it, and neither can Apple, Google or whoever runs the relay.',
      'The relay keeps nothing: no database, no files, no record of who sent what. It sees your phone\'s push address and the size of each message while passing it on.',
      'Your hub keeps your phone\'s public key and push address so it can reach you, and forgets them when you turn notifications off or remove the phone.',
      'This relay is in testing and writes one line per delivery (time, a short code for the phone, size, result; never the message). That log is turned off for general use.',
      'Rather not use the relay? Choose email instead: your alerts come by email from your hub, and filament mapping stays on the hub\'s web pages.'
    ],
    relay: 'https://push.example.com',
    relay_status: { reachable: true, version: '0.1.0', delivery_log: true },
    source: 'https://github.com/norjms/openprinthq-push-relay'
  }
};

test('phone notifications panel', async ({ page }, info) => {
  let state = structuredClone(STATE);
  await page.route('**/api/push/state', (r) => r.fulfill({ json: state }));
  await page.route('**/api/push/choice', async (r) => {
    state = { ...state, choice: r.request().postDataJSON().choice, devices: [], mapping_in_app: false };
    return r.fulfill({ json: state });
  });
  await page.route('**/api/farm', (r) => r.fulfill({ json: {
    printers: [{ name: 'H2D', online: true, busy: false }], checks: [], queue: [], timeline: [], plates: [], history: [],
    maintenance: [] } }));
  await page.route('**/api/farm/hidden-errors', (r) => r.fulfill({ json: { hidden: [] } }));
  const ev = { print_done: 'Print finished, plate waiting', alarm: 'Printer alarm', order_new: 'New paid order' };
  await page.route('**/api/farm/settings', (r) => r.fulfill({ json: {
    plate_checks: true, order_routing: 'hub', ship_within_days: 5, max_retries: 1, route_test_orders: false,
    stagger_minutes: 0, turnaround_minutes: 5, channels: ['push', 'email', 'ntfy', 'telegram', 'slack'],
    channels_configured: ['push', 'email'], urgent_events: ['alarm'], alert_events: ev,
    alert_rules: { events: { print_done: ['push'], alarm: ['push', 'email'], order_new: ['push'] },
      quiet_start: '22:00', quiet_end: '07:00', timezone: 'America/Chicago', quiet_mode: 'urgent' } } }));
  await page.goto('/farm?view=settings');
  const panel = page.getByTestId('phone-push');
  await expect(panel.getByText('How phone notifications work')).toBeVisible();
  await expect(panel.getByText('It cannot read it')).toBeVisible();
  await expect(panel.getByText("James's iPhone")).toBeVisible();
  await expect(panel.getByText('delivery log on (testing)')).toBeVisible();
  await panel.scrollIntoViewIfNeeded();
  if (process.env.SHOTS_DIR) await panel.screenshot({ path: `${process.env.SHOTS_DIR}/phone-push-${info.project.name}.png` });
  await panel.getByLabel('Email instead, and map filament on the web').check();
  await expect(panel.getByText("James's iPhone")).toHaveCount(0);
});
