// Named printer cameras: sources, names, redaction, and the go2rtc sync.
import test from 'node:test';
import assert from 'node:assert/strict';

const c = await import('../src/cameras.js');

test('a Bambu Lab printer with RTSP gets its RTSPS feed; the A1 and P1 do not', () => {
  const h2d = { connection_type: 'bambu', ip_address: '192.0.2.10', access_code: 'ab12/cd', model: 'O1D' };
  assert.equal(c.builtinSource(h2d), 'rtsps://bblp:ab12%2Fcd@192.0.2.10:322/streaming/live/1');
  assert.equal(c.builtinSource({ ...h2d, model: 'N2S' }), null, 'A1 mini: chamber-image protocol, not a stream');
  assert.equal(c.builtinSource({ ...h2d, access_code: null }), null);
  assert.equal(c.builtinSource({ ...h2d, ip_address: '' }), null);
});

test('a Klipper printer uses the webcam Moonraker reports, else the usual path', () => {
  const k = { connection_type: 'klipper', ip_address: '192.0.2.20' };
  assert.equal(c.builtinSource(k, [{ enabled: true, stream_url: '/webcam2/?action=stream' }]),
    'http://192.0.2.20/webcam2/?action=stream');
  assert.equal(c.builtinSource(k, [{ enabled: false, stream_url: '/off' }, { stream_url: 'http://198.51.100.5:8080/s' }]),
    'http://198.51.100.5:8080/s');
  assert.equal(c.builtinSource(k, null), 'http://192.0.2.20/webcam/?action=stream');
  assert.equal(c.builtinSource({ connection_type: 'klipper', ip_address: '2001:db8::5' }), 'http://[2001:db8::5]/webcam/?action=stream');
});

test('OctoPrint has its webcam; PrusaLink and unknown printers have no built-in source', () => {
  assert.equal(c.builtinSource({ connection_type: 'octoprint', ip_address: '192.0.2.30' }), 'http://192.0.2.30/webcam/?action=stream');
  assert.equal(c.builtinSource({ connection_type: 'prusalink', ip_address: '192.0.2.31' }), null);
});

test("the engine's own external camera is used only when switched on and a stream URL", () => {
  assert.equal(c.engineCameraSource({ external_camera_enabled: true, external_camera_url: 'rtsp://192.0.2.40/live' }), 'rtsp://192.0.2.40/live');
  assert.equal(c.engineCameraSource({ external_camera_enabled: false, external_camera_url: 'rtsp://192.0.2.40/live' }), null);
  assert.equal(c.engineCameraSource({ external_camera_enabled: true, external_camera_url: 'file:///etc/passwd' }), null);
});

test('only URLs go2rtc pulls from are accepted for an added camera', () => {
  assert.equal(c.validSource('rtsp://user:pw@192.0.2.50:554/stream1'), 'rtsp://user:pw@192.0.2.50:554/stream1');
  assert.equal(c.validSource(' http://192.0.2.51/mjpeg '), 'http://192.0.2.51/mjpeg');
  for (const bad of ['exec:ffmpeg -i x', 'ffmpeg:rtsp://x', 'file:///etc/passwd', 'echo:hi', '', 'rtsp://a b', 'x'.repeat(2000)]) {
    assert.equal(c.validSource(bad), null, bad.slice(0, 30));
  }
});

test('listings never show a password', () => {
  assert.equal(c.redact('rtsps://bblp:secret@192.0.2.10:322/streaming/live/1'), 'rtsps://192.0.2.10:322/streaming/live/1');
  assert.equal(c.redact('http://192.0.2.51/mjpeg'), 'http://192.0.2.51/mjpeg');
  const api = c.forApi([{ printer_id: 1, camera_name: 'x', stream_name: 'p1', _source: 'rtsp://u:p@h/' }]);
  assert.equal('_source' in api[0], false);
});

test('stream names: per printer in single-tenant mode, per account otherwise', () => {
  delete process.env.OPHQ_ENGINE_URL;
  assert.equal(c.builtinStreamName(7, 3), 'u7_p3');
  assert.equal(c.extraStreamName(7, 3, 12), 'u7_p3_c12');
  assert.equal(c.scopeFor({ subdomain: 'acme' }), 't:acme');
  assert.equal(c.scopeFor(null), null);
  process.env.OPHQ_ENGINE_URL = 'http://openprinthq-engine:8000';
  assert.equal(c.builtinStreamName(7, 3), 'p3');
  assert.equal(c.engineCameraStreamName(7, 3), 'p3_ext');
  assert.equal(c.extraStreamName(9, 3, 12), 'p3_c12', 'every account sees the same streams');
  assert.equal(c.scopeFor(null), 'single');
  delete process.env.OPHQ_ENGINE_URL;
});

test('a chamber camera comes first within its printer', () => {
  const list = c.forApi([
    { printer_id: 2, camera_name: 'Printer camera', id: null },
    { printer_id: 1, camera_name: 'Top', id: 5 },
    { printer_id: 1, camera_name: 'Chamber', id: 9 },
  ]);
  assert.deepEqual(list.map((x) => `${x.printer_id}:${x.camera_name}`), ['1:Chamber', '1:Top', '2:Printer camera']);
});

test('sync registers enabled cameras and removes the ones that went away', async () => {
  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => { calls.push(`${init.method || 'GET'} ${decodeURIComponent(String(url))}`); return new Response('', { status: 200 }); };
  try {
    const r = await c.syncStreams([
      { stream_name: 'p1', enabled: true, _source: 'rtsps://bblp:x@192.0.2.10:322/streaming/live/1' },
      { stream_name: 'p1_c4', enabled: false, _source: 'rtsp://192.0.2.60/s' },
    ], new Set(['p1', 'p1_c4', 'p2']));
    assert.equal(r.registered, 1);
    assert.deepEqual([...r.streams], ['p1']);
    assert.ok(calls.some((x) => x.startsWith('PUT ') && x.includes('name=p1&src=rtsps://')));
    assert.ok(calls.includes(`DELETE ${c.GO2RTC_URL}/api/streams?src=p1_c4`));
    assert.ok(calls.includes(`DELETE ${c.GO2RTC_URL}/api/streams?src=p2`));
    assert.ok(!calls.some((x) => x.startsWith('DELETE') && x.endsWith('=p1')));
  } finally { globalThis.fetch = realFetch; }
});

test('a frame is a JPEG, and its result is the camera health', async () => {
  const realFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 1]), { status: 200 });
    assert.ok(await c.frame('p5'));
    assert.equal(c.healthOf('p5').state, 'ONLINE');
    globalThis.fetch = async () => new Response('stream not found', { status: 404 });
    assert.equal(await c.frame('p5'), null);
    assert.equal(c.healthOf('p5').state, 'OFFLINE');
    globalThis.fetch = async () => { throw new Error('timeout'); };
    assert.equal(await c.frame('p6'), null);
    assert.equal(c.healthOf('p6').error, 'timeout');
    assert.equal(c.healthOf('never').state, 'UNKNOWN');
  } finally { globalThis.fetch = realFetch; }
});
