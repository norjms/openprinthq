// The OpenPrintHQ sidebar, drawn from the hub's /api/nav/v1 (the hub's docs/nav-api.md) so this app shows the same
// groups and items as the hub and the farm. The hub serves this app at /apps/laser, on the same address, so the
// request carries the person's hub session. Without the hub (running this app on its own) the sidebar holds
// only this page.
import { ICONS } from './vendor/openprinthq-ui/icons.js';

const SVG = 'http://www.w3.org/2000/svg';
const HERE = '/apps/laser/';

function icon(name, size) {
  // ICONS holds the package's own path markup (trusted, vendored); nothing from the network goes in here.
  const svg = document.createElementNS(SVG, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor',
    'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(k, v);
  svg.innerHTML = ICONS[name] || '';
  return svg;
}

function isHere(item) {
  if (item.app !== 'laser') return false;
  try { return new URL(item.href, location.href).pathname === location.pathname.replace(/index\.html$/, ''); } catch { return false; }
}

function safeHref(href) {
  // Only same-site paths and http(s) addresses become links.
  try { const u = new URL(href, location.href); return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null; } catch { return null; }
}

function draw(groups) {
  const nav = document.getElementById('nav');
  nav.replaceChildren();
  for (const g of groups) {
    const label = document.createElement('div');
    label.className = 'ah-group-label';
    label.textContent = g.label;
    nav.append(label);
    for (const it of g.items || []) {
      const href = safeHref(it.href);
      if (!href) continue;
      const a = document.createElement('a');
      a.className = 'ah-nav';
      a.href = href;
      if (isHere(it)) a.setAttribute('aria-current', 'page');
      a.append(icon(it.icon, 17), document.createTextNode(it.label));
      nav.append(a);
    }
  }
}

const ALONE = [{ label: 'Laser Crafting', items: [{ label: 'Convert files', href: HERE, icon: 'swap', app: 'laser' }] }];

document.getElementById('brand-icon').append(icon('mark', 20));
draw(ALONE);
fetch('/api/nav/v1', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
  .then((d) => {
    if (!Array.isArray(d.groups) || !d.groups.length) return;
    draw(d.groups);
    if (d.installation) document.getElementById('brand-name').textContent = d.installation;
  })
  .catch(() => {});
