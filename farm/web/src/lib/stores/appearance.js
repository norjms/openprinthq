// Per-user appearance store
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Holds the live appearance config (transparency, text scale, a11y, branding,
// navigation). `load()` pulls the saved config from the control-plane and
// applies it; `save()` persists it, applies it, and refreshes the pre-paint
// cookie. Branding (site name / tagline / logo) is read reactively by Logo,
// Footer, the hero, printed outputs, and page <title>s.

import { writable, derived, get } from 'svelte/store';
import { api } from '$lib/api';
import {
  DEFAULT_CONFIG, DEFAULT_BRANDING, normalizeConfig,
  applyAppearance, writeAppearanceCookie, resolveLogo
} from '$lib/theme';

export const appearance = writable(normalizeConfig(DEFAULT_CONFIG));
export const branding = derived(appearance, ($a) => $a.branding || { ...DEFAULT_BRANDING });
// The uploaded logo, if any.
export const activeLogo = derived(appearance, ($a) => resolveLogo($a.branding));

let loaded = false;
let authedLoaded = false; // true once the signed-in user's own config loaded

// Fetch + apply the saved config. Safe to call on any page; no-ops if unauthed.
export async function loadAppearance() {
  if (loaded) return get(appearance);
  loaded = true;
  try {
    // GET /api/appearance returns { config: <saved-or-null> }. Unwrap it before
    // normalizing, or the saved settings would be read as empty.
    const resp = await api.appearance();
    const cfg = normalizeConfig(resp?.config ?? resp);
    appearance.set(cfg);
    applyAppearance(cfg);
    writeAppearanceCookie(cfg);
    authedLoaded = true;
    return cfg;
  } catch {
    // Not signed in, no instance or offline: keep defaults (cookie already applied pre-paint).
    return get(appearance);
  }
}

// Public SITE branding for logged-out visitors: fetch the host owner's branding
// and merge ONLY the branding into the store, leaving the visitor's own
// settings untouched. Authed branding always wins, so this no-ops
// once the signed-in config has loaded (and never fetches for signed-in users).
export async function loadPublicBranding() {
  if (authedLoaded) return get(appearance);
  try {
    const resp = await api.pubBranding();
    if (authedLoaded) return get(appearance); // signed-in config arrived first
    const b = resp?.branding;
    if (b && typeof b === 'object' && Object.keys(b).length) {
      const cur = get(appearance);
      const cfg = normalizeConfig({ ...cur, branding: { ...cur.branding, ...b } });
      appearance.set(cfg);
      applyAppearance(cfg); // refreshes the favicon if branded
    }
  } catch { /* offline or no owner: keep the built-in name */ }
  return get(appearance);
}

// Apply a config locally (live preview) without persisting.
export function applyLocal(cfg) {
  const norm = normalizeConfig(cfg);
  appearance.set(norm);
  applyAppearance(norm);
  writeAppearanceCookie(norm);
  return norm;
}

// Persist to the server (and apply locally).
export async function saveAppearance(cfg) {
  const norm = applyLocal(cfg);
  await api.saveAppearance(norm);
  return norm;
}
