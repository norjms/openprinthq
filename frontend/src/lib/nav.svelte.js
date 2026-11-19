import NAV from '../../static/nav.json';
import { api } from './api.js';

// The sidebar: one list (static/nav.json) that the hub draws and serves at /api/nav/v1 for the farm.
// The server's copy adds what only it knows: the installed farm's own links and the installation's name, and with
// them the Farm group (pages only the farm has, app "farm" in the list), which the built-in copy leaves out.
const hubOnly = NAV.groups
  .map((g) => ({ ...g, items: g.items.filter((i) => i.app !== 'farm').map((i) => ({ ...i, app: 'hub' })) }))
  .filter((g) => g.items.length);
export const nav = $state({ groups: hubOnly, farm: null, installation: null });

export async function loadNav() {
  try {
    const d = await api.get('/api/nav/v1');
    nav.groups = d.groups;
    nav.farm = d.farm;
    nav.installation = d.installation;
  } catch {}
}

/** Whether a nav item is the page at this path and query. */
export function isCurrent(item, url) {
  const p = url.pathname;
  const [path, query] = item.href.split('?');
  if (item.app === 'farm') return false;
  if (query) {
    const want = new URLSearchParams(query);
    return p === path && [...want].every(([k, v]) => url.searchParams.get(k) === v);
  }
  if (path === '/') return p === '/' || (item.match || []).some((m) => p.startsWith(m));
  if (path === '/farm') return p === '/farm' && !url.searchParams.get('view');
  return p === path || p.startsWith(path + '/') || (item.match || []).some((m) => p === m || p.startsWith(m + '/'));
}

/** The group and item for this page, for the header's breadcrumb. */
export function where(url) {
  for (const g of nav.groups) for (const it of g.items) if (isCurrent(it, url)) return { group: g.label, item: it };
  return null;
}

/** How the screens name the print farm (the farm) in a sentence. */
export const farmName = () => 'the farm';
export const FarmName = () => 'The farm';
