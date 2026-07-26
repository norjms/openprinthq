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
  applyAppearance, writeAppearanceCookie
} from '$lib/theme';

export const appearance = writable(normalizeConfig(DEFAULT_CONFIG));
export const branding = derived(appearance, ($a) => $a.branding || { ...DEFAULT_BRANDING });

let loaded = false;

// Fetch + apply the saved config. Safe to call on any page; no-ops if unauthed.
export async function loadAppearance() {
  if (loaded) return get(appearance);
  loaded = true;
  try {
    const saved = await api.appearance();
    const cfg = normalizeConfig(saved);
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
