// The sidebar is the hub's: the farm draws the hub's navigation list, read from
// the hub (GET /api/nav/v1, see docs/nav-api.md in artisan-hub) when it answers,
// and from the copy built in here (hub-nav.json) when it does not.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// An item with `farm_path` is a farm page: it links inside the farm, under the
// base path (/apps/farm). Every other item is a hub page: its `href` is a path
// at the hub's root (or a full address), so it is opened with a full page load.
import { base } from '$app/paths';
import FALLBACK from './hub-nav.json';

// The farm's own item in the hub's Print group (the farm canvas): after Filament.
export const TIMELAPSES = { id: 'timelapses', label: 'Timelapses', icon: 'film', farm_path: '/app/timelapses', href: '/farm' };

// Farm pages the hub's list does not name. They are reached from the farm's
// overview, the jump-to palette and the account menu, and get a breadcrumb.
export const FARM_PAGES = [
  { path: '/app', label: 'Farm', icon: 'grid', exact: true },
  { path: '/app/files', label: 'Files', icon: 'cube' },
  { path: '/app/slicer', label: 'Slicer', icon: 'layers' },
  { path: '/app/projects', label: 'Projects', icon: 'box' },
  { path: '/app/statistics', label: 'Statistics', icon: 'chart' },
  { path: '/app/reports', label: 'Print reports', icon: 'file-text' },
  { path: '/app/settings', label: 'Farm settings', icon: 'cog' },
  { path: '/app/instances', label: 'Farm settings', icon: 'cog', hidden: true }
];
export const GENFILAMENT_PAGE = { path: '/app/genfilament', label: 'GenFilament', icon: 'puzzle' };

const isUrl = (h) => /^https?:\/\//i.test(h || '');

/** The fallback list, as the hub would answer it to someone it cannot see. */
export function fallbackNav() {
  return { ...FALLBACK, installation: null, farm: null, fromHub: false };
}

/** Whether an answer looks like the hub's sidebar (version 1). */
export function validNav(d) {
  return !!d && d.version === 1 && Array.isArray(d.groups) && d.groups.length > 0 &&
    d.groups.every((g) => g && typeof g.label === 'string' && Array.isArray(g.items));
}

/** Read the hub's sidebar. Falls back to the built-in copy on any failure. */
export async function loadHubNav(fetchFn = fetch) {
  try {
    const r = await fetchFn('/api/nav/v1', { credentials: 'include', headers: { accept: 'application/json' } });
    if (!r.ok) return fallbackNav();
    const d = await r.json();
    return validNav(d) ? { ...d, fromHub: true } : fallbackNav();
  } catch {
    return fallbackNav();
  }
}

/**
 * Sidebar groups for the farm: each item gets `href` (where it goes from here),
 * `farm` (a page of this farm, routed in the app) and `reload` (a hub page).
 * Timelapses joins the Print group after Filament when the hub's list lacks it.
 * Items needing a permission the hub did not grant are already left out by it.
 */
export function farmGroups(doc) {
  const groups = (doc?.groups || []).map((g) => ({ label: g.label, items: [...(g.items || [])] }));
  const all = groups.flatMap((g) => g.items);
  if (!all.some((i) => i.id === TIMELAPSES.id || i.farm_path === TIMELAPSES.farm_path)) {
    const print = groups.find((g) => g.items.some((i) => i.farm_path)) || groups[0];
    if (print) {
      const at = print.items.findIndex((i) => i.id === 'filament' || i.farm_path === '/app/filament');
      print.items.splice(at >= 0 ? at + 1 : print.items.length, 0, TIMELAPSES);
    }
  }
  return groups.map((g) => ({
    label: g.label,
    items: g.items.filter((i) => i && i.label && (i.href || i.farm_path)).map((i) => {
      if (i.farm_path) return { ...i, href: base + i.farm_path, path: i.farm_path, farm: true, reload: false };
      return { ...i, href: i.href, farm: false, reload: true, external: false, abs: isUrl(i.href) };
    })
  }));
}

/** The farm page a path (without the base) belongs to: a sidebar item or one of FARM_PAGES. */
export function farmCurrent(groups, path, extra = []) {
  let best = null;
  for (const g of groups) {
    for (const i of g.items) {
      if (!i.farm) continue;
      if (path === i.path || path.startsWith(i.path + '/')) {
        if (!best || i.path.length > best.item.path.length) best = { group: g.label, item: i };
      }
    }
  }
  if (best) return best;
  let page = null;
  for (const p of [...FARM_PAGES, ...extra]) {
    const hit = p.exact ? path === p.path : path === p.path || path.startsWith(p.path + '/');
    if (hit && (!page || p.path.length > page.path.length)) page = p;
  }
  return page ? { group: null, item: { ...page, href: base + page.path, farm: true } } : null;
}
