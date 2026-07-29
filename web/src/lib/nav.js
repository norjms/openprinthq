// Default left-navigation definition + per-user nav-customization helpers.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// NAV_ITEMS is the single source of truth for the built-in nav order and the set
// of hideable items. Both AppShell (which renders the nav) and NavPrefs (the
// Look & Feel editor for it) import from here so they can never drift apart.
// Per-user customization lives in the appearance config under `nav`:
//   nav = { order: string[] (hrefs), hidden: string[] (hrefs), links: [{label,url}] }

export const NAV_ITEMS = [
  { href: '/app', label: 'Overview', icon: '▚' },
  { href: '/app/printers', label: 'Printers', icon: '🖨' },
  { href: '/app/cameras', label: 'Cameras', icon: '📷' },
  { href: '/app/queue', label: 'Print queue', icon: '≣' },
  { href: '/app/files', label: 'Files', icon: '🗀' },
  { href: '/app/slicer', label: 'Slicer', icon: '◈' },
  { href: '/app/filament', label: 'Filament', icon: '🧵' },
  { href: '/app/projects', label: 'Projects', icon: '📁' },
  { href: '/app/statistics', label: 'Statistics', icon: '📈' },
  { href: '/app/reports', label: 'Reports', icon: '🧾' },
  { href: '/app/settings', label: 'Settings', icon: '⚙' }
];

// Feature-gated built-in (only offered when the instance enables it).
export const GENFILAMENT_ITEM = { href: '/app/genfilament', label: 'GenFilament', icon: '🧪' };

// Settings must always stay reachable so a user can undo their customization.
export const NAV_SETTINGS_HREF = '/app/settings';

// Generic glyph for user-added custom links.
export const NAV_LINK_ICON = '🔗';

// Only http(s) links are allowed for custom nav rows.
export function isHttpUrl(u) {
  return typeof u === 'string' && /^https?:\/\//i.test(u.trim());
}

// Merge the built-in items (already filtered for enabled features) with the
// user's saved `nav` prefs. Robust to missing / extra / duplicate hrefs.
// Returns the ordered, visibility-filtered built-ins followed by custom links.
// Settings is always kept visible regardless of `hidden`.
export function mergeNav(builtins, prefs) {
  const p = prefs && typeof prefs === 'object' ? prefs : {};
  const byHref = new Map(builtins.map((i) => [i.href, i]));
  const order = Array.isArray(p.order) ? p.order : [];
  const hidden = new Set(Array.isArray(p.hidden) ? p.hidden : []);

  const seen = new Set();
  const ordered = [];
  for (const href of order) {
    if (byHref.has(href) && !seen.has(href)) { ordered.push(byHref.get(href)); seen.add(href); }
  }
  for (const item of builtins) {
    if (!seen.has(item.href)) { ordered.push(item); seen.add(item.href); }
  }

  const visible = ordered.filter((i) => i.href === NAV_SETTINGS_HREF || !hidden.has(i.href));

  const links = Array.isArray(p.links) ? p.links : [];
  const custom = links
    .filter((l) => l && isHttpUrl(l.url))
    .map((l) => ({ href: String(l.url).trim(), label: (l.label && String(l.label).trim()) || String(l.url).trim(), icon: NAV_LINK_ICON, external: true }));

  return [...visible, ...custom];
}
