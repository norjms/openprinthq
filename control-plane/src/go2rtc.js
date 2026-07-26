// OpenPrintHQ control-plane — go2rtc dynamic stream registration
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The browser streams cameras peer-to-peer from go2rtc (co-located with the
// printers); the control-plane only brokers the SDP handshake. This module
// registers a printer's camera as a go2rtc stream ON DEMAND (when a browser
// starts a WebRTC session), reading the RTSP creds straight from that user's
// engine so nothing is hard-coded and a newly-added printer "just works".
//
// The engine stores its data in SQLite (/app/data/bambuddy.db) inside the
// per-tenant container. The control-plane has the Docker socket + CLI (same
// access the provisioner uses), so it reads the printer's creds via a scoped
// `docker exec` into THAT user's own engine container — which also makes the
// lookup implicitly ownership-safe (a user can only reach printers in their
// own engine). Stream names are namespaced per user (`u<uid>_p<pid>`) so
// tenants never collide on a shared go2rtc.
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const pexec = promisify(execFile);
const GO2RTC_URL = process.env.OPHQ_GO2RTC_URL || 'http://openprinthq-go2rtc:1984';
const RTSP_PORT = 322; // Bambu RTSP-over-TLS camera port

export function streamName(userId, printerId) { return `u${userId}_p${printerId}`; }

// Mirror of the engine's supports_rtsp(): which Bambu models expose an RTSP
// camera (vs the A1/P1 chamber-image protocol, which isn't a go2rtc source).
function supportsRtsp(model) {
  if (!model) return false;
  const m = String(model).toUpperCase();
  if (/^(X1|X2|H2|P2)/.test(m)) return true;
  return ['BL-P001', 'C13', 'N6', 'O1D', 'O1C', 'O1C2', 'O1S', 'O1E', 'O2D', 'N7'].includes(m);
}

// Read one printer's camera fields from the user's engine SQLite. execFile (no
// shell) passes the Python as a single arg — no quoting hazards.
async function enginePrinter(subdomain, printerId) {
  const pid = Number(printerId);
  if (!Number.isInteger(pid)) return null;
  const py =
    'import sqlite3, json\n' +
    "row = sqlite3.connect('/app/data/bambuddy.db').execute(" +
    '"select connection_type, ip_address, access_code, model from printers where id=?", (' + pid + ',)).fetchone()\n' +
    "print(json.dumps(row) if row else 'null')";
  try {
    const { stdout } = await pexec('docker', ['exec', `ophq-${subdomain}`, 'python3', '-c', py], { timeout: 8000 });
    const row = JSON.parse(stdout.trim());
    if (!row) return null;
    const [connection_type, ip_address, access_code, model] = row;
    return { connection_type, ip_address, access_code, model };
  } catch { return null; }
}

// Idempotently register the printer's camera as a go2rtc stream. Returns the
// stream name if the printer is a WebRTC-capable Bambu, else null (caller then
// 404s and the browser falls back to snapshot polling).
export async function ensureStream(instance, userId, printerId) {
  if (!instance?.subdomain) return null;
  const p = await enginePrinter(instance.subdomain, printerId);
  if (!p || p.connection_type !== 'bambu' || !p.access_code || !supportsRtsp(p.model)) return null;
  const src = `rtsps://bblp:${p.access_code}@${p.ip_address}:${RTSP_PORT}/streaming/live/1`;
  const name = streamName(userId, printerId);
  try {
    const r = await fetch(`${GO2RTC_URL}/api/streams?name=${encodeURIComponent(name)}&src=${encodeURIComponent(src)}`, { method: 'PUT' });
    if (!r.ok && r.status !== 200) return null;
    return name;
  } catch { return null; }
}

export { GO2RTC_URL };
