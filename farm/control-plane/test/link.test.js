import { test } from 'node:test';
import assert from 'node:assert/strict';
import { linkPrinters, linkConfigured, fetchLinkPrinters } from '../src/link.js';

const STATUS = {
  sites: [
    { id: 'a', name: 'Home', connected: true, printers: [
      { overlay: '198.18.0.4', ip: '192.168.1.224', family: 'bambu', name: 'H2D', model: 'O1D', serial: '0948AD5C0204357', online: true },
      { overlay: '198.18.0.1', ip: '192.168.1.37', family: 'prusalink', name: 'prusa-mk4', online: true },
      { ip: '192.168.1.9', family: 'bambu' } // no address yet: left out
    ] },
    { id: 'b', name: 'Shed', connected: false, printers: [
      { overlay: '198.18.0.9', ip: '10.0.0.5', family: 'moonraker', name: 'voron', online: true }
    ] }
  ]
};

test('Link\'s status becomes one list, each printer with its site and where it was found', () => {
  const list = linkPrinters(STATUS);
  assert.equal(list.length, 3);
  assert.deepEqual(list[0], { name: 'H2D', family: 'bambu', model: 'O1D', serial: '0948AD5C0204357',
    ip_address: '198.18.0.4', site: 'Home', found_at: '192.168.1.224', online: true, link: true });
  // A printer at a site that is not connected is not online, whatever the site last said.
  assert.equal(list.find((p) => p.name === 'voron').online, false);
  assert.deepEqual(linkPrinters(null), []);
});

test('Link is asked only with its address and key, with the key as a bearer token', async () => {
  assert.equal(linkConfigured({}), false);
  assert.equal(linkConfigured({ OPHQ_LINK_URL: 'http://link:8080' }), false);
  assert.deepEqual(await fetchLinkPrinters({}), { enabled: false, printers: [] });
  let seen;
  const fake = async (url, opts) => { seen = { url, auth: opts.headers.authorization };
    return { ok: true, json: async () => STATUS }; };
  const got = await fetchLinkPrinters({ OPHQ_LINK_URL: 'http://link:8080/', OPHQ_LINK_TOKEN: 'k' }, fake);
  assert.deepEqual(seen, { url: 'http://link:8080/api/status', auth: 'Bearer k' });
  assert.equal(got.enabled, true);
  assert.equal(got.printers.length, 3);
  await assert.rejects(fetchLinkPrinters({ OPHQ_LINK_URL: 'http://l', OPHQ_LINK_TOKEN: 'k' },
    async () => ({ ok: false, status: 401 })), /401/);
});
