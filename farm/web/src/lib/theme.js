// OpenPrintHQ theme engine
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// A "theme" is a full set of --ophq-* CSS variables. The three built-in presets
// (dark / light / accessible) also exist as [data-theme] blocks in app.css so the
// document renders correctly before JS runs. This module holds the SAME values as
// JS objects so the Look & Feel editor can render live mockups, compute WCAG
// contrast, and seed the Custom mode. Per-mode user edits are stored as sparse
// `overrides` and applied over the preset (inline on <html>, winning over CSS).

// The editable colour tokens, in display order, grouped for the editor UI.
export const TOKEN_GROUPS = [
  {
    label: 'Surfaces',
    tokens: [
      ['--ophq-bg', 'Page background'],
      ['--ophq-bg-2', 'Inset / sidebar'],
      ['--ophq-surface', 'Card (top)'],
      ['--ophq-surface-2', 'Card (bottom)'],
      ['--ophq-surface-3', 'Raised surface'],
      ['--ophq-border', 'Border'],
      ['--ophq-border-soft', 'Soft border / grid']
    ]
  },
  {
    label: 'Text',
    tokens: [
      ['--ophq-text', 'Primary text'],
      ['--ophq-text-2', 'Secondary text'],
      ['--ophq-muted', 'Muted text'],
      ['--ophq-faint', 'Faint text']
    ]
  },
  {
    label: 'Brand & status',
    tokens: [
      ['--ophq-primary', 'Primary (buttons)'],
      ['--ophq-primary-2', 'Primary text / links'],
      ['--ophq-accent', 'Accent'],
      ['--ophq-success', 'Success'],
      ['--ophq-warn', 'Warning'],
      ['--ophq-danger', 'Danger'],
      ['--ophq-info', 'Info']
    ]
  }
];

// Flat list of every editable token key.
export const TOKEN_KEYS = TOKEN_GROUPS.flatMap((g) => g.tokens.map((t) => t[0]));

// Human labels keyed by token.
export const TOKEN_LABELS = Object.fromEntries(TOKEN_GROUPS.flatMap((g) => g.tokens.map((t) => [t[0], t[1]])));

// ---- built-in presets (must mirror app.css [data-theme] blocks) -----------
export const PRESETS = {
  dark: {
    '--ophq-bg': '#0d1117', '--ophq-bg-2': '#0a0e14',
    '--ophq-surface': '#151b24', '--ophq-surface-2': '#1b2330', '--ophq-surface-3': '#222c3c',
    '--ophq-border': '#26303f', '--ophq-border-soft': '#1e2733',
    '--ophq-primary': '#7c6cff', '--ophq-primary-2': '#9a8cff', '--ophq-accent': '#ffb020',
    '--ophq-success': '#35c46b', '--ophq-warn': '#f5a623', '--ophq-danger': '#ff5c6c', '--ophq-info': '#38bdf8',
    '--ophq-text': '#e6ebf2', '--ophq-text-2': '#b4bdcc', '--ophq-muted': '#7f8ca1', '--ophq-faint': '#56617a'
  },
  light: {
    '--ophq-bg': '#999999', '--ophq-bg-2': '#e9ebef',
    '--ophq-surface': '#f5f6f9', '--ophq-surface-2': '#e9ecf1', '--ophq-surface-3': '#dde1e8',
    '--ophq-border': '#b7bcc6', '--ophq-border-soft': '#8c8c8c',
    '--ophq-primary': '#6d5cf0', '--ophq-primary-2': '#5646d6', '--ophq-accent': '#b06f00',
    '--ophq-success': '#1f9d57', '--ophq-warn': '#b5730a', '--ophq-danger': '#d63a4a', '--ophq-info': '#1f7fb8',
    '--ophq-text': '#14171b', '--ophq-text-2': '#2f353d', '--ophq-muted': '#383d45', '--ophq-faint': '#565d68'
  },
  accessible: {
    '--ophq-bg': '#ffffff', '--ophq-bg-2': '#f2f2f2',
    '--ophq-surface': '#ffffff', '--ophq-surface-2': '#f6f6f6', '--ophq-surface-3': '#e8e8e8',
    '--ophq-border': '#595959', '--ophq-border-soft': '#767676',
    '--ophq-primary': '#0b3d91', '--ophq-primary-2': '#0b3d91', '--ophq-accent': '#8a5000',
    '--ophq-success': '#0a6b30', '--ophq-warn': '#8a5000', '--ophq-danger': '#b10000', '--ophq-info': '#0b3d91',
    '--ophq-text': '#000000', '--ophq-text-2': '#1a1a1a', '--ophq-muted': '#333333', '--ophq-faint': '#4a4a4a'
  }
};
// Custom starts from the light preset; the user takes it from there.
PRESETS.custom = { ...PRESETS.light };

export const MODES = [
  { id: 'dark', name: 'Dark', blurb: 'The original graphite control-room look.' },
  { id: 'light', name: 'Light', blurb: 'A bright grey theme for well-lit rooms.' },
  { id: 'accessible', name: 'Accessible', blurb: 'WCAG-AAA high contrast + a11y aids.' },
  { id: 'custom', name: 'Custom', blurb: 'Pick every colour yourself.' }
];

// Accessibility toggles (Accessible mode turns the first three on by default).
export const A11Y_OPTIONS = [
  ['underline', 'Underline all links'],
  ['focus', 'Strong focus outline'],
  ['targets', 'Larger click targets (44px)'],
  ['font', 'Higher-legibility font'],
  ['motion', 'Reduce motion / animation']
];

export const DEFAULT_A11Y = { underline: false, focus: false, targets: false, font: false, motion: false };
export const ACCESSIBLE_DEFAULT_A11Y = { underline: true, focus: true, targets: true, font: false, motion: false };

export const DEFAULT_BRANDING = {
  siteName: 'OpenPrintHQ',
  tagline: 'One command center for every 3D printer.',
  // Three per-mode logo variants (data-URIs; empty = built-in wordmark). The app
  // shows the one matching the active theme mode so the mark stays legible on any
  // background: `light` for Light mode, `dark` for Dark mode, `accessible` for
  // Accessible/high-contrast mode. Custom mode falls back to the light variant.
  logos: { light: '', dark: '', accessible: '' },
  logo: '',       // legacy single logo — kept for back-compat; resolveLogo falls back to it
  favicon: '',    // data-URI; empty = default favicon.svg
  wordmark: '',   // optional text override for the wordmark (defaults to siteName)
  trademark: '',  // footer trademark / legal line (e.g. "AcmePrint™ is a trademark of Acme Inc.")
  contact: ''     // footer host contact info (email, URL, or address)
};

// The three uploadable logo slots, in editor display order.
export const LOGO_SLOTS = [
  ['light', 'Light mode', 'Shown on light backgrounds.'],
  ['dark', 'Dark mode', 'Shown on the dark graphite theme.'],
  ['accessible', 'Accessible', 'High-contrast / accessible mode.']
];

// Resolve which logo data-URI to display for a given theme mode, with graceful
// fallbacks (chosen slot → light → dark → accessible → legacy single logo → '').
export function resolveLogo(branding, mode) {
  const b = branding || {};
  const logos = b.logos || {};
  const order = {
    dark: ['dark', 'light', 'accessible'],
    light: ['light', 'dark', 'accessible'],
    accessible: ['accessible', 'light', 'dark'],
    custom: ['light', 'dark', 'accessible']
  }[mode] || ['light', 'dark', 'accessible'];
  for (const k of order) if (logos[k]) return logos[k];
  return b.logo || '';
}

// A complete appearance config. `overrides` is per-mode sparse maps of token->value.
export const DEFAULT_CONFIG = {
  mode: 'dark',
  overrides: { dark: {}, light: {}, accessible: {}, custom: {} },
  textScale: 1,
  a11y: { ...DEFAULT_A11Y },
  branding: { ...DEFAULT_BRANDING }
};

// Merge a stored (possibly partial) config onto the defaults, defensively.
export function normalizeConfig(raw) {
  const c = raw && typeof raw === 'object' ? raw : {};
  const ov = c.overrides && typeof c.overrides === 'object' ? c.overrides : {};
  return {
    mode: MODES.some((m) => m.id === c.mode) ? c.mode : 'dark',
    overrides: {
      dark: { ...(ov.dark || {}) },
      light: { ...(ov.light || {}) },
      accessible: { ...(ov.accessible || {}) },
      custom: { ...(ov.custom || {}) }
    },
    textScale: clampScale(c.textScale),
    a11y: { ...DEFAULT_A11Y, ...(c.a11y || {}) },
    branding: {
      ...DEFAULT_BRANDING,
      ...(c.branding || {}),
      // Always keep the three logo slots present, even for older saved configs.
      logos: { ...DEFAULT_BRANDING.logos, ...((c.branding && c.branding.logos) || {}) }
    }
  };
}

export function clampScale(v) {
  const n = Number(v);
  if (!isFinite(n)) return 1;
  return Math.min(1.5, Math.max(0.85, n));
}

// Accessible mode makes base text 1.5x larger by default (a11y aid) WITHOUT the
// user having to touch the text-size slider. An explicit user scale (any value
// other than the 1.0 default) always wins, and the bump only applies while the
// mode is 'accessible' — so it never sticks after switching away.
export const ACCESSIBLE_DEFAULT_SCALE = 1.5;
export function effectiveTextScale(mode, textScale) {
  const s = clampScale(textScale);
  if (mode === 'accessible' && s === 1) return ACCESSIBLE_DEFAULT_SCALE;
  return s;
}

// The effective variable map for a mode = preset merged with the user's overrides.
export function effectiveVars(config, mode = config.mode) {
  return { ...PRESETS[mode], ...(config.overrides?.[mode] || {}) };
}

// Apply an entire appearance config to a target element (default <html>).
export function applyAppearance(config, el) {
  if (typeof document === 'undefined') return;
  el = el || document.documentElement;
  const cfg = normalizeConfig(config);
  el.setAttribute('data-theme', cfg.mode);
  // Inline the effective vars so custom edits win over the CSS preset block.
  const vars = effectiveVars(cfg);
  for (const k of TOKEN_KEYS) {
    const v = cfg.overrides[cfg.mode]?.[k];
    if (v) el.style.setProperty(k, v);
    else el.style.removeProperty(k); // fall back to the CSS preset value
  }
  el.style.setProperty('--ophq-text-scale', String(effectiveTextScale(cfg.mode, cfg.textScale)));
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
  // If cleared, leave whatever is there (a full reset would need the asset path).
}

// ---- pre-paint cookie cache (mirrors the app.html bootstrap contract) ------
const COOKIE = 'ophq_appearance';
export function writeAppearanceCookie(config) {
  if (typeof document === 'undefined') return;
  const cfg = normalizeConfig(config);
  // Keep the cookie small: colours + scale + a11y + favicon only (NOT the logo).
  const slim = {
    mode: cfg.mode,
    overrides: cfg.overrides[cfg.mode] || {},
    textScale: effectiveTextScale(cfg.mode, cfg.textScale),
    a11y: cfg.a11y,
    favicon: cfg.branding.favicon || ''
  };
  try {
    const val = encodeURIComponent(JSON.stringify(slim));
    // 1 year, site-wide, lax.
    document.cookie = `${COOKIE}=${val}; path=/; max-age=31536000; samesite=lax`;
  } catch { /* cookie too big (favicon) — drop favicon and retry */
    try {
      const slim2 = { ...slim, favicon: '' };
      document.cookie = `${COOKIE}=${encodeURIComponent(JSON.stringify(slim2))}; path=/; max-age=31536000; samesite=lax`;
    } catch { /* give up; server will re-apply on load */ }
  }
}

// ---- WCAG contrast helpers (for the editor's readouts) ---------------------
export function parseColor(str) {
  if (!str) return null;
  str = String(str).trim();
  let m = /^#([0-9a-f]{3})$/i.exec(str);
  if (m) { const h = m[1]; return [h[0], h[1], h[2]].map((c) => parseInt(c + c, 16)); }
  m = /^#([0-9a-f]{6})$/i.exec(str);
  if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
  m = /^rgba?\(([^)]+)\)$/i.exec(str);
  if (m) { const p = m[1].split(',').map((x) => parseFloat(x)); return [p[0], p[1], p[2]]; }
  return null;
}

function relLuminance(rgb) {
  const a = rgb.map((v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
}

// Contrast ratio between two colours (1..21). Returns null on unparseable input.
export function contrastRatio(fg, bg) {
  const a = parseColor(fg), b = parseColor(bg);
  if (!a || !b) return null;
  const l1 = relLuminance(a), l2 = relLuminance(b);
  const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
  return (hi + 0.05) / (lo + 0.05);
}

// WCAG rating for normal-size text: 'AAA' >=7, 'AA' >=4.5, 'AA Large' >=3, else 'Fail'.
export function wcagRating(ratio) {
  if (ratio == null) return '';
  if (ratio >= 7) return 'AAA';
  if (ratio >= 4.5) return 'AA';
  if (ratio >= 3) return 'AA Large';
  return 'Fail';
}
