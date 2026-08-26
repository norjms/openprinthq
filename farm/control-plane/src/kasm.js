// OpenPrintHQ control-plane -- Kasm Workspaces Developer API client
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Backs the in-browser slicer: the Slice tab asks for a session, we make sure a
// Kasm account exists for the caller, reuse their running session if there is
// one, and hand back a connect URL.
//
// All config is env-driven so the tier is swappable with no code change. Nothing
// here knows or cares where the Kasm deployment actually lives.
import { randomBytes } from 'node:crypto';
import { request as httpsRequest } from 'node:https';

// Public base URL. This is what the BROWSER is given in the connect link, so it
// must always be the internet-facing name even when we talk to Kasm privately.
const KASM_URL = (process.env.OPHQ_KASM_URL || '').replace(/\/+$/, '');

// Optional private base URL for server-to-server API calls, so a session launch
// does not have to leave the network and come back. Falls back to the public URL.
const KASM_API_URL = (process.env.OPHQ_KASM_API_URL || '').replace(/\/+$/, '') || KASM_URL;

const API_KEY = process.env.OPHQ_KASM_API_KEY || '';
const API_SECRET = process.env.OPHQ_KASM_API_SECRET || '';

// Provisioned users are placed in this Kasm group. It carries the slicer
// workspace images and nothing else, so a tenant never needs Administrators
// (which is where the OrcaSlicer Nightly image was originally assigned).
const SLICER_GROUP = process.env.OPHQ_KASM_GROUP_ID || '';

// engine key -> Kasm image_id. Same engine keys the Slice tab picker uses, so
// adding a slicer is a config entry rather than a code change.
let IMAGES = {};
try { IMAGES = JSON.parse(process.env.OPHQ_KASM_IMAGES || '{}'); } catch { IMAGES = {}; }

// A private endpoint is typically an IP, and the origin usually presents a
// self-signed certificate, so neither the public hostname nor the IP will
// validate against a public trust store. Rather than turning verification off,
// the origin certificate is pinned as the trust anchor and matched against the
// CN it actually carries (OPHQ_KASM_API_CA_NAME).
const API_HOST = (() => { try { return new URL(KASM_URL).host; } catch { return ''; } })();
const PRIVATE_API = KASM_API_URL !== KASM_URL;
const CA_PEM = process.env.OPHQ_KASM_API_CA_B64
  ? Buffer.from(process.env.OPHQ_KASM_API_CA_B64, 'base64').toString('utf8')
  : null;
const CA_NAME = process.env.OPHQ_KASM_API_CA_NAME || '';

if (PRIVATE_API && !(CA_PEM && CA_NAME)) {
  throw new Error('OPHQ_KASM_API_URL requires OPHQ_KASM_API_CA_B64 and OPHQ_KASM_API_CA_NAME');
}

// fetch() cannot be given a CA or an SNI override without pulling in undici as a
// dependency, so the private path uses node:https directly. Same contract back:
// { status, text }.
function privatePost(pathname, payload) {
  const u = new URL(KASM_API_URL);
  return new Promise((resolve, reject) => {
    const req = httpsRequest({
      host: u.hostname,
      port: u.port || 443,
      path: pathname,
      method: 'POST',
      ca: CA_PEM,
      servername: CA_NAME,
      headers: {
        'content-type': 'application/json',
        // Kasm routes on Host; the URL carries an IP here, so the public name
        // has to be supplied explicitly.
        host: API_HOST,
        'content-length': Buffer.byteLength(payload)
      }
    }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ status: res.statusCode, text: body }));
    });
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

export function kasmConfigured() { return !!(KASM_URL && API_KEY && API_SECRET); }
export function kasmEngines() { return Object.keys(IMAGES); }
export function kasmImageFor(engine) { return IMAGES[engine] || null; }

async function kasm(endpoint, body = {}) {
  if (!kasmConfigured()) throw new Error('kasm not configured');
  const payload = JSON.stringify({ api_key: API_KEY, api_key_secret: API_SECRET, ...body });
  let status, text;
  if (PRIVATE_API) {
    ({ status, text } = await privatePost(`/api/public/${endpoint}`, payload));
  } else {
    const r = await fetch(`${KASM_API_URL}/api/public/${endpoint}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: payload
    });
    status = r.status;
    text = await r.text();
  }
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-json */ }
  if (status < 200 || status >= 300) throw new Error(data?.error_message || `HTTP ${status}`);
  // Kasm answers 200 with an error_message body on permission and validation
  // failures, so status alone is not enough to call it a success.
  if (data?.error_message) throw new Error(data.error_message);
  return data || {};
}

const norm = (s) => String(s || '').replace(/-/g, '');

// ---- users --------------------------------------------------------------
// One Kasm account per OpenPrintHQ user, created on first launch. The password
// is random and thrown away: nobody ever logs into Kasm directly, we only ever
// act as the user through the Developer API.
function kasmUsernameFor(email) {
  return `ophq-${String(email).toLowerCase().replace(/[^a-z0-9._-]+/g, '-')}`;
}

export async function ensureKasmUser(email, { existingId = null } = {}) {
  if (existingId) {
    // Trust but verify: the account can be deleted out from under us in the
    // Kasm admin UI, in which case we fall through and make a new one.
    try {
      const { user } = await kasm('get_user', { target_user: { user_id: existingId } });
      if (user?.user_id) return { userId: user.user_id, username: user.username, created: false };
    } catch { /* gone, recreate below */ }
  }
  const username = kasmUsernameFor(email);
  try {
    const { user } = await kasm('create_user', {
      target_user: { username, password: randomBytes(24).toString('base64url'), locked: false, disabled: false }
    });
    await addToSlicerGroup(user.user_id);
    return { userId: user.user_id, username: user.username, created: true };
  } catch (e) {
    // Races and re-provisioning both land here. Fall back to a lookup by name.
    let users = [];
    try { ({ users } = await kasm('get_users', {})); } catch { /* ignore */ }
    const hit = (users || []).find((u) => u.username === username);
    if (hit) {
      await addToSlicerGroup(hit.user_id);
      return { userId: hit.user_id, username: hit.username, created: false };
    }
    throw e;
  }
}

// Membership is what authorises the workspace image; without it request_kasm
// fails with "Image Not Authorized" even though the user exists. Idempotent:
// re-adding an existing member is not an error worth failing a launch over.
async function addToSlicerGroup(kasmUserId) {
  if (!SLICER_GROUP) return;
  try {
    await kasm('add_user_group', {
      target_user: { user_id: kasmUserId },
      target_group: { group_id: SLICER_GROUP }
    });
  } catch (e) {
    if (!/already/i.test(e.message)) throw e;
  }
}

export async function deleteKasmUser(kasmUserId) {
  await kasm('delete_user', { target_user: { user_id: kasmUserId } });
}

// ---- sessions -----------------------------------------------------------
export async function findSession(kasmUserId, imageId) {
  const { kasms } = await kasm('get_kasms', {});
  return (kasms || []).find((k) =>
    norm(k.user_id) === norm(kasmUserId) &&
    norm(k.image_id) === norm(imageId) &&
    !['destroying', 'stopped'].includes(String(k.operational_status || '').toLowerCase())
  ) || null;
}

export async function sessionStatus(kasmUserId, kasmId) {
  const d = await kasm('get_kasm_status', { user_id: kasmUserId, kasm_id: kasmId });
  return d.kasm || null;
}

export async function destroySession(kasmUserId, kasmId) {
  await kasm('destroy_kasm', { user_id: kasmUserId, kasm_id: kasmId });
}

// The connect URL is a fragment route, so the token never reaches the server as
// a query string and stays out of access logs.
export function connectUrl(kasmId, kasmUserId, sessionToken) {
  return `${KASM_URL}/#/connect/kasm/${norm(kasmId)}/${norm(kasmUserId)}/${sessionToken}`;
}

// Reuse-or-create.
//
// Two Kasm behaviours drive this, both confirmed against the live deployment
// rather than assumed:
//
//  1. request_kasm ALWAYS starts a new container. Calling it twice for the same
//     user and image yields two sessions, not one. So it must never be the
//     "check if one exists" call.
//  2. The JWT it returns outlives the session it belongs to. Observed exp was
//     3 days out against an 8h session expiry. So the token is not the thing
//     that goes stale, the session is. Store the token and reuse it, and let a
//     vanished session (not an expired token) trigger a new request.
//
// `stored` is the caller's persisted {kasmId, sessionToken} or null.
export async function ensureSession(kasmUserId, imageId, stored = null) {
  if (stored?.kasmId && stored?.sessionToken) {
    const live = await findSession(kasmUserId, imageId);
    if (live && norm(live.kasm_id) === norm(stored.kasmId)) {
      return {
        kasmId: stored.kasmId,
        sessionToken: stored.sessionToken,
        status: live.operational_status,
        url: connectUrl(stored.kasmId, kasmUserId, stored.sessionToken),
        reused: true
      };
    }
  }
  // No usable session on record. Tear down any stray one for this user+image
  // first, so a lost token cannot orphan a running 4GB container.
  const stray = await findSession(kasmUserId, imageId);
  if (stray) { try { await destroySession(kasmUserId, stray.kasm_id); } catch { /* best effort */ } }

  const d = await kasm('request_kasm', { user_id: kasmUserId, image_id: imageId, enable_sharing: false });
  return {
    kasmId: d.kasm_id,
    sessionToken: d.session_token,
    status: d.status,
    url: connectUrl(d.kasm_id, d.user_id, d.session_token),
    reused: false
  };
}
