// Left navigation: the built-in groups and the per-user customization helpers.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// NAV_GROUPS is the single source of truth for the built-in nav: its groups,
// their order, and the set of hideable items. AppShell renders it and NavPrefs
// (the editor for it) reads it, so the two never drift apart. Icons are names
// from artisanhub-ui's stroke icon set. Per-user customization lives in the
// appearance config under `nav`:
//   nav = { order: string[] (hrefs), hidden: string[] (hrefs), links: [{label,url}] }
// `order` reorders items within their group; groups keep their place.

export const NAV_GROUPS = [
  {
    label: 'Farm',
    items: [
      { href: '/app', label: 'Overview', icon: 'grid', exact: true },
      { href: '/app/printers', label: 'Printers', icon: 'printer' },
      { href: '/app/cameras', label: 'Cameras', icon: 'camera' },
      { href: '/app/queue', label: 'Print queue', icon: 'list-queue' }
    ]
  },
  {
    label: 'Make',
    items: [
      { href: '/app/files', label: 'Files', icon: 'cube' },
      { href: '/app/slicer', label: 'Slicer', icon: 'layers' },
      { href: '/app/filament', label: 'Filament', icon: 'spool' },
      { href: '/app/projects', label: 'Projects', icon: 'box' }
    ]
  },
  {
    label: 'Insights',
    items: [
      { href: '/app/statistics', label: 'Statistics', icon: 'chart' },
      { href: '/app/reports', label: 'Reports', icon: 'file-text' }
    ]
  },
  {
    label: 'Instance',
    items: [
      { href: '/app/settings', label: 'Settings', icon: 'cog' }
    ]
  }
];

// Flat list of the built-in items, each tagged with its group.
export const NAV_ITEMS = NAV_GROUPS.flatMap((g) => g.items.map((i) => ({ ...i, group: g.label })));

// Feature-gated built-in (only offered when the instance enables it).
export const GENFILAMENT_ITEM = { href: '/app/genfilament', label: 'GenFilament', icon: 'puzzle', group: 'Make' };

// Settings must always stay reachable so a user can undo their customization.
export const NAV_SETTINGS_HREF = '/app/settings';

// Group for user-added custom links.
export const NAV_LINKS_GROUP = 'Links';
export const NAV_LINK_ICON = 'puzzle';

// Only http(s) links are allowed for custom nav rows.
export function isHttpUrl(u) {
  return typeof u === 'string' && /^https?:\/\//i.test(u.trim());
}

// Merge the built-in items (already filtered for enabled features) with the
// user's saved `nav` prefs. Robust to missing, extra or duplicate hrefs.
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
    .map((l) => ({
      href: String(l.url).trim(),
      label: (l.label && String(l.label).trim()) || String(l.url).trim(),
      icon: NAV_LINK_ICON, group: NAV_LINKS_GROUP, external: true
    }));

  return [...visible, ...custom];
}

// Group a merged item list for the sidebar: the built-in groups in their fixed
// order (items in the user's order inside each), then custom links.
export function groupNav(items) {
  const labels = [...NAV_GROUPS.map((g) => g.label), NAV_LINKS_GROUP];
  return labels
    .map((label) => ({ label, items: items.filter((i) => (i.group || NAV_LINKS_GROUP) === label) }))
    .filter((g) => g.items.length);
}

// Which item a path belongs to (for the current-page highlight and breadcrumb).
export function currentItem(items, path) {
  let best = null;
  for (const i of items) {
    if (i.external) continue;
    const hit = i.exact ? path === i.href : path === i.href || path.startsWith(i.href + '/');
    if (hit && (!best || i.href.length > best.href.length)) best = i;
  }
  return best;
}
