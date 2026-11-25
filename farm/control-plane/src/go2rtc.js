// OpenPrintHQ control-plane — go2rtc dynamic stream registration
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The browser streams cameras peer-to-peer from go2rtc (co-located with the
// printers); the control-plane only brokers the SDP handshake. This module
// makes sure a printer's camera is registered as a go2rtc stream when a browser
// starts a WebRTC session, reading the printer's camera details from that
// account's engine over HTTP, so nothing is hard-coded and a newly-added printer
// "just works". It needs no Docker access: the engine is reached by address,
// and an account can only reach printers in its own engine. Stream names and
// sources come from cameras.js, which also keeps the named camera list.
import { getSecretSetting } from './db.js';
import { engineBaseOf } from './tenancy.js';
import {
  GO2RTC_URL, supportsRtsp, builtinSource, builtinStreamName, registerStream
} from './cameras.js';

export { supportsRtsp };
export function streamName(userId, printerId) { return builtinStreamName(userId, printerId); }

// Read one printer's record from the account's engine.
async function enginePrinter(instance, printerId) {
  const pid = Number(printerId);
  const base = engineBaseOf(instance);
  if (!Number.isInteger(pid) || !base) return null;
  try {
    const r = await fetch(`${base}/api/v1/printers/${pid}`, { signal: AbortSignal.timeout(8000) });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

// Idempotently register the printer's built-in camera as a go2rtc stream.
// Returns the stream name if the printer has a camera go2rtc can pull, else null
// (caller then 404s and the browser falls back to snapshot polling).
export async function ensureStream(instance, userId, printerId) {
  if (!instance?.subdomain) return null;
  const p = await enginePrinter(instance, printerId);
  if (!p) return null;
  const src = builtinSource(p);
  if (!src) return null;
  const name = streamName(userId, printerId);
  try {
    return (await registerStream(name, src)) ? name : null;
  } catch { return null; }
}

export { GO2RTC_URL };

// ---- ICE servers (STUN always; Cloudflare TURN for remote/CGNAT) ----------
// Cloudflare issues SHORT-LIVED TURN credentials, so the control-plane mints a
// fresh set per session and hands them to the browser (no static secret that
// expires).
//
// Credentials come from the owner's admin settings (encrypted at rest), falling
// back to env for existing deployments. Settings win so the owner can rotate a
// leaked token from the UI without a redeploy.
//
// Why TURN matters here: printers behind CGNAT are exactly the population where
// hole-punching fails, so the browser<->connector leg needs a relay to fall back
// to. Cloudflare's STUN is free and their TURN has a free monthly allowance, and
// it can relay over TLS/443, which survives networks that block UDP outright.
const CF_TURN_KEY_ENV = process.env.OPHQ_CF_TURN_KEY_ID || '';
const CF_TURN_TOKEN_ENV = process.env.OPHQ_CF_TURN_API_TOKEN || '';

export async function turnCredentials() {
  const keyId = (await getSecretSetting('cf_turn_key_id')) || CF_TURN_KEY_ENV;
  const token = (await getSecretSetting('cf_turn_api_token')) || CF_TURN_TOKEN_ENV;
  return { keyId: keyId || '', token: token || '' };
}

// Ask Cloudflare for a short-lived credential set. Returns null when TURN is not
// configured or Cloudflare rejects us; callers degrade to STUN-only.
export async function mintTurn(ttl = 3600) {
  const { keyId, token } = await turnCredentials();
  if (!keyId || !token) return null;
  try {
    const r = await fetch(`https://rtc.live.cloudflare.com/v1/turn/keys/${keyId}/credentials/generate`, {
      method: 'POST',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: JSON.stringify({ ttl })
    });
    if (!r.ok) return { error: `Cloudflare returned HTTP ${r.status}` };
    const j = await r.json();
    return j && j.iceServers ? { iceServers: j.iceServers } : { error: 'no iceServers in response' };
  } catch (e) {
    return { error: e.message || 'request failed' };
  }
}

export async function iceServers(ttl = 3600) {
  const list = [
    // Cloudflare's STUN is free and unmetered; Google's is the historical
    // fallback and costs nothing to keep as a second option.
    { urls: 'stun:stun.cloudflare.com:3478' },
    { urls: 'stun:stun.l.google.com:19302' }
  ];
  const t = await mintTurn(ttl);
  if (t && t.iceServers) list.push(t.iceServers);
  return list;
}
