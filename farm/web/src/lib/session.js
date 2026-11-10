// Sign-in expiry detection for the browser app.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Every /api call except the public ones passes Authentik forward-auth at the
// gateway. When the outpost session expires, forward-auth answers a 302 to the
// Authentik sign-in page. fetch() follows it, the hop to auth.openprinthq.com is
// cross-origin with no CORS headers, and the browser reports a bare network
// error ("NetworkError when attempting to fetch resource", "Load failed",
// "Failed to fetch"). Pages render that as "Engine unreachable", and repeating
// the same fetch can never succeed: only a top-level navigation can go through
// the sign-in redirect. A page refresh works because it is one. Authentik's own
// session usually outlives the outpost's, so that navigation bounces straight
// back signed in, with no login form.
//
// So: when a request fails at the network level, ask /api/me once with
// redirect: 'manual'. An opaque redirect (or a 401) there means the sign-in
// expired, not the engine. Callers then reauth() instead of retrying.
import { writable, get } from 'svelte/store';
import { base as basePath } from '$app/paths';

export const sessionExpired = writable(false);

let probing = null;

/** True when the gateway is redirecting /api to sign-in. */
export function probeSession() {
  if (probing) return probing;
  probing = (async () => {
    try {
      const r = await fetch(basePath + '/api/me', { credentials: 'include', redirect: 'manual', cache: 'no-store' });
      const expired = r.type === 'opaqueredirect' || r.status === 401;
      if (expired) sessionExpired.set(true);
      return expired;
    } catch {
      return false; // genuinely offline or the gateway is down: not an auth problem
    } finally {
      setTimeout(() => { probing = null; }, 0);
    }
  })();
  return probing;
}

export function isSessionExpired() {
  return get(sessionExpired);
}

/** Go through the sign-in redirect and come back to this page. */
export function reauth() {
  window.location.reload();
}

/**
 * What every "Retry" on an error card should call. If the sign-in has expired a
 * plain retry is pointless, so re-authenticate instead; otherwise run the
 * page's own loader.
 */
export async function retryOrReauth(fn) {
  if (isSessionExpired() || (await probeSession())) return reauth();
  return fn?.();
}

export function authExpiredError() {
  return Object.assign(new Error('Your sign-in has expired. Sign in again to reconnect.'), {
    status: 401,
    authExpired: true
  });
}
