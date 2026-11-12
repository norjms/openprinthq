// OpenPrintHQ appearance settings
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The look itself comes from artisanhub-ui: one named theme, glass-dark, set as
// data-theme on <html>. What a user can still choose is stored here, per user:
// transparency (follow the device, always glass, or solid panels), text size,
// accessibility aids, branding and the navigation. Older saved configs carried
// a theme "mode" (dark, light, accessible, custom) and colour overrides; those
// values resolve to glass-dark and the overrides are ignored.

import { resolveTheme, applyTheme, DEFAULT_THEME } from 'artisanhub-ui/themes';
import { normalizePrinterSections } from '$lib/printerSections';

export { THEMES, DEFAULT_THEME } from 'artisanhub-ui/themes';

// Transparency: 'auto' follows the device's "reduce transparency" setting.
export const TRANSPARENCY = [
  { id: 'auto', label: 'Match device' },
  { id: 'on', label: 'Glass' },
  { id: 'off', label: 'Solid' }
];

// Accessibility toggles.
export const A11Y_OPTIONS = [
  ['underline', 'Underline all links'],
  ['focus', 'Strong focus outline'],
  ['targets', 'Larger click targets (44px)'],
  ['font', 'Higher-legibility font'],
  ['motion', 'Reduce motion and animation']
];

export const DEFAULT_A11Y = { underline: false, focus: false, targets: false, font: false, motion: false };

export const DEFAULT_BRANDING = {
  siteName: 'ArtisanHub Farm',
  tagline: 'The print farm part of ArtisanHub.',
  // Logo data-URIs (empty = the built-in mark). The glass theme is dark, so the
  // `dark` slot is the one shown; the other two are kept so older saved configs
  // round-trip and are used as fallbacks.
  logos: { light: '', dark: '', accessible: '' },
  logo: '',       // legacy single logo, kept for older configs; resolveLogo falls back to it
  favicon: '',    // data-URI; empty = default favicon.svg
  wordmark: '',   // optional text override for the product name (defaults to siteName)
  trademark: '',  // footer trademark or legal line
  contact: ''     // footer host contact info (email, URL, or address)
};

// The logo to show: the dark slot first, then any other uploaded one.
export function resolveLogo(branding) {
  const b = branding || {};
  const logos = b.logos || {};
  for (const k of ['dark', 'light', 'accessible']) if (logos[k]) return logos[k];
  return b.logo || '';
}

// Per-user left-nav customization: reorder or hide built-ins, add custom links.
// `order`/`hidden` are arrays of hrefs; `links` are user-added external rows.
export const DEFAULT_NAV = { order: [], hidden: [], links: [] };

export const DEFAULT_CONFIG = {
  mode: DEFAULT_THEME,
  transparency: 'auto',
  textScale: 1,
  a11y: { ...DEFAULT_A11Y },
  branding: { ...DEFAULT_BRANDING },
  nav: { ...DEFAULT_NAV }
};

export function normalizeNav(raw) {
  const n = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const strArr = (a) => (Array.isArray(a) ? a.filter((x) => typeof x === 'string') : []);
  const links = Array.isArray(n.links) ? n.links : [];
  return {
    order: strArr(n.order),
    hidden: strArr(n.hidden),
    links: links
      .filter((l) => l && typeof l === 'object' && typeof l.url === 'string' && /^https?:\/\//i.test(l.url.trim()))
      .map((l) => ({ label: typeof l.label === 'string' ? l.label : '', url: l.url.trim() }))
  };
}

// Merge a stored (possibly partial or older) config onto the defaults.
export function normalizeConfig(raw) {
  const c = raw && typeof raw === 'object' ? raw : {};
  return {
    // Any older mode (dark, light, accessible, custom) becomes the one theme.
    mode: resolveTheme(c.mode),
    transparency: TRANSPARENCY.some((t) => t.id === c.transparency) ? c.transparency : 'auto',
    textScale: clampScale(c.textScale),
    a11y: { ...DEFAULT_A11Y, ...(c.a11y || {}) },
    branding: {
      ...DEFAULT_BRANDING,
      ...(c.branding || {}),
      logos: { ...DEFAULT_BRANDING.logos, ...((c.branding && c.branding.logos) || {}) }
    },
    nav: normalizeNav(c.nav),
    printerSections: normalizePrinterSections(c.printerSections)
  };
}

export function clampScale(v) {
  const n = Number(v);
  if (!isFinite(n)) return 1;
  return Math.min(1.5, Math.max(0.85, n));
}

// Apply an appearance config to <html> (or another element).
export function applyAppearance(config, el) {
  if (typeof document === 'undefined') return;
  el = el || document.documentElement;
  const cfg = normalizeConfig(config);
  applyTheme(el, cfg.mode, cfg.transparency);
  el.style.setProperty('--ophq-text-scale', String(cfg.textScale));
  setA11yAttrs(el, cfg.a11y);
  applyFavicon(cfg.branding.favicon);
  return cfg;
}

function setA11yAttrs(el, a11y) {
  const map = {
    underline: ['data-a11y-underline', 'on'],
    focus: ['data-a11y-focus', 'strong'],
    targets: ['data-a11y-targets', 'large'],
    font: ['data-a11y-font', 'legible'],
    motion: ['data-a11y-motion', 'reduce']
  };
  for (const key in map) {
    const [attr, val] = map[key];
    if (a11y[key]) el.setAttribute(attr, val);
    else el.removeAttribute(attr);
  }
}

function applyFavicon(dataUri) {
  if (typeof document === 'undefined') return;
  const f = document.getElementById('ophq-favicon');
  if (!f) return;
  if (dataUri) { f.setAttribute('href', dataUri); f.removeAttribute('type'); }
}

// ---- pre-paint cookie cache (mirrors the app.html bootstrap contract) ------
const COOKIE = 'ophq_appearance';
export function writeAppearanceCookie(config) {
  if (typeof document === 'undefined') return;
  const cfg = normalizeConfig(config);
  const slim = {
    mode: cfg.mode,
    transparency: cfg.transparency,
    textScale: cfg.textScale,
    a11y: cfg.a11y,
    favicon: cfg.branding.favicon || ''
  };
  try {
    document.cookie = `${COOKIE}=${encodeURIComponent(JSON.stringify(slim))}; path=/; max-age=31536000; samesite=lax`;
  } catch {
    try {
      const slim2 = { ...slim, favicon: '' };
      document.cookie = `${COOKIE}=${encodeURIComponent(JSON.stringify(slim2))}; path=/; max-age=31536000; samesite=lax`;
    } catch { /* the server re-applies on load */ }
  }
}
