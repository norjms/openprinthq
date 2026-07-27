// Per-user appearance store
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Holds the live appearance config (theme mode, colour overrides, text scale,
// a11y, branding). `load()` pulls the saved config from the control-plane and
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
// The logo that matches the active theme mode (light / dark / accessible / custom).
export const activeLogo = derived(appearance, ($a) => resolveLogo($a.branding, $a.mode));

let loaded = false;

// Fetch + apply the saved config. Safe to call on any page; no-ops if unauthed.
export async function loadAppearance() {
  if (loaded) return get(appearance);
  loaded = true;
  try {
    // GET /api/appearance returns { config: <saved-or-null> }. Unwrap it — passing
    // the wrapper straight to normalizeConfig would find no `.mode` and silently
    // fall back to light, reverting the user's saved theme on every refresh.
    const resp = await api.appearance();
    const cfg = normalizeConfig(resp?.config ?? resp);
    appearance.set(cfg);
    applyAppearance(cfg);
    writeAppearanceCookie(cfg);
    return cfg;
  } catch {
    // Not logged in / no instance / offline — keep defaults (cookie already applied pre-paint).
    return get(appearance);
  }
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
