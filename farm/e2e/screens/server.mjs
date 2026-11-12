// A stand-in backend for the screen checks: serves the built web app and
// answers its /api calls from fixtures.mjs, as an owner who is signed in.
// Nothing here talks to a real engine or printer.
//
//   node e2e/screens/server.mjs <web build dir> [port]
//
// SPDX-License-Identifier: AGPL-3.0-or-later
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import * as F from './fixtures.mjs';

const buildDir = path.resolve(process.argv[2] || '../web/build');
const port = Number(process.argv[3] || process.env.PORT || 4173);
const BASE = process.env.BASE_PATH || '';
const { handler } = await import(pathToFileURL(path.join(buildDir, 'handler.js')).href);
// With HUB=1 the hub answers at the site root (nav and counts), as in an
// ArtisanHub install; without it the hub is unreachable and the farm falls back
// to its built-in sidebar.
const HUB = process.env.HUB === '1';
const hubNav = JSON.parse(fs.readFileSync(new URL('../../web/src/lib/hub-nav.json', import.meta.url), 'utf8'));
function hubAnswer(p) {
  if (p === '/api/nav/v1') {
    const farm = { kind: 'openprinthq', label: 'ArtisanHub Farm', app: 'http://127.0.0.1:' + port + BASE };
    const groups = hubNav.groups.map((g) => ({ ...g, items: g.items.map((i) => (i.farm_path
      ? { ...i, href: farm.app + i.farm_path, app: 'farm' } : { ...i, app: 'hub' })) }));
    return { version: 1, product: 'ArtisanHub', installation: 'Artisan Prints', farm, groups };
  }
  if (p === '/api/stats') return F.hubStats;
  return undefined;
}

// A camera frame: a print bed seen from the front, drawn as SVG so the checks
// need no image files. Each printer gets a slightly different light.
function frame(id) {
  const tint = ['#4a6275', '#465a6b', '#3f5566', '#4d5f70'][(Number(id) || 0) % 4];
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
<defs><radialGradient id="g" cx="48%" cy="44%" r="75%"><stop offset="0" stop-color="${tint}"/><stop offset="0.5" stop-color="#2c3d4a"/><stop offset="1" stop-color="#18222b"/></radialGradient></defs>
<rect width="1280" height="720" fill="url(#g)"/>
<polygon points="250,600 1030,600 880,330 400,330" fill="rgba(255,255,255,0.06)" stroke="rgba(255,255,255,0.2)"/>
<path d="M320 465H960M360 395H920M290 535H990M520 330L480 600M640 330V600M760 330L800 600" stroke="rgba(255,255,255,0.08)"/>
<rect x="560" y="380" width="160" height="120" rx="8" fill="rgba(143,211,255,0.25)" stroke="rgba(143,211,255,0.6)"/>
<rect x="590" y="250" width="100" height="62" rx="10" fill="rgba(255,255,255,0.14)" stroke="rgba(255,255,255,0.3)"/>
<path d="M640 312V372" stroke="rgba(255,255,255,0.45)" stroke-width="4"/></svg>`;
}

const json = (res, code, body) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)); };

function route(method, p, q) {
  const m = (re) => p.split('?')[0].match(re);
  let r;
  if (method !== 'GET') return { ok: true };
  if (p === '/me') return { id: 1, email: 'james@example.com', displayName: 'James Norman', role: 'owner', isOwner: true, hasAccount: true };
  if (p === '/appearance') return { config: null };
  if (p === '/pub/branding') return { branding: {} };
  if (p === '/pub/config') return { deployment_mode: 'both', signup_enabled: true };
  if (p === '/pub/signup-info') return { enabled: true, inviteRequired: true };
  if (p === '/instance') return { status: 'running', subdomain: 'artisanprints', engineVersion: 'openprinthq-engine:prod', createdAt: '2026-07-25T14:14:00Z', features: {} };
  if (p === '/instance/stats') return { printersOnline: 4, printersTotal: 4, activeJobs: 3, queued: F.queue.length, successRate: 96 };
  if (p === '/connectors') return F.connectors;
  if (p === '/cameras') return F.cameras;
  if (p === '/slicer/info') return { version: '2.3.1', desktop_url: null, print_host: 'http://127.0.0.1:' + port + BASE + '/octo', desktop_print_host: null, print_host_keys: 0 };
  if ((r = m(/^\/pub\/tag\/([^/]+)$/))) return { spool: { id: 203, brand: 'Prusament', material: 'PLA', subtype: 'Galaxy', color_name: 'Orange', rgba: 'E8742AFF', remaining_weight: 190, label_weight: 1000, location: 'Print room, AMS A4', tag_uid: '04:A2:19:7C:31:5E:80' }, filament: { product_name: 'Prusament PLA Galaxy Orange', specs: { nozzle_temp_range_low: 205, nozzle_temp_range_high: 225, nozzle_temp_initial_layer: 215, bed_temp: 60, density: 1.24, flow_ratio: 0.98 } } };
  if (p === '/library-status') return { available: true };
  if (p === '/library/models' || p.startsWith('/library/models?')) return { models: F.models, totalItems: F.models.length, totalPages: 1 };
  if ((r = m(/^\/library\/models\/(\d+)$/))) return F.models.find((x) => x.id === Number(r[1])) || null;
  if (p === '/library/categories') return F.categories;
  if (p === '/library/tags') return [{ id: 1, name: 'PLA' }, { id: 2, name: 'PETG' }];
  if (p.startsWith('/library/')) return [];
  if (p === '/filament-backend') return { mode: 'builtin' };
  if (p === '/batch/active') return [];
  if (p === '/hms/descriptions') return {};
  if (p === '/admin/summary') return { users: 3, instances: 3, invites: 2 };
  if (p === '/admin/settings') return {};
  if (p === '/admin/users') return [{ id: 1, email: 'james@example.com', display_name: 'James Norman', role: 'owner', created_at: '2026-07-25T14:14:00Z' }];
  if (p === '/admin/instances') return [{ id: 1, subdomain: 'artisanprints', status: 'running', owner_email: 'james@example.com', features: {}, created_at: '2026-07-25T14:14:00Z' }];
  if (p === '/settings/logging') return { log_url: '' };
  if (p === '/slicer/engines') return { engines: ['orca'] };
  if ((r = m(/^\/camera\/capability\/(\d+)$/))) return { mode: 'snapshot', webrtc: false };

  const e = p.replace(/^\/engine\/api\/v1/, '');
  if (e === p) return null;
  if (e === '/printers/') return F.printers;
  if ((r = e.match(/^\/printers\/(\d+)$/))) return F.printers.find((x) => x.id === Number(r[1])) || null;
  if ((r = e.match(/^\/printers\/(\d+)\/status$/))) return F.status[r[1]] || null;
  if (e === '/queue/') return F.queue;
  if (e === '/library/files' || e.startsWith('/library/files?')) return F.files;
  if (e === '/projects/') return F.projects;
  if ((r = e.match(/^\/projects\/(\d+)\/(bom|queue|archives)$/))) return [];
  if (e === '/archives/stats') return F.stats;
  if (e.startsWith('/archives/analysis/failures')) return F.failures;
  if (e.startsWith('/archives/')) return F.archives;
  if (e.startsWith('/print-log/')) return { items: F.archives, total: F.archives.length };
  if (e === '/inventory/spools' || e === '/spoolman/inventory/spools') return F.spools;
  if (e === '/inventory/locations') return [...new Set(F.spools.map((s) => s.location))].map((name, i) => ({ id: i + 1, name }));
  if (e === '/settings') return { spoolman_enabled: false, currency: 'USD', energy_cost_per_kwh: 0.15 };
  if (e.startsWith('/slicer/presets')) return { printer: ['Bambu Lab H2D 0.4 nozzle'], process: ['0.20mm Standard'], filament: ['Generic PLA'] };
  if (e.startsWith('/slicer/printer-models')) return { 'Bambu Lab H2D': {}, 'Prusa MK4S': {}, 'Prusa MK3S': {}, 'Voron 2.4 350': {} };
  return null;
}

http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  let p = u.pathname;
  if (BASE && !p.startsWith(BASE + '/') && p !== BASE) {
    // Outside the farm: the hub's root.
    const out = HUB ? hubAnswer(p) : undefined;
    if (out !== undefined) return json(res, 200, out);
    res.writeHead(404, { 'content-type': 'text/plain' }); return res.end('not found');
  }
  if (BASE && p.startsWith(BASE + '/api/')) p = p.slice(BASE.length);
  if (p.startsWith('/api/')) {
    const ap = p.slice(4);
    let r;
    if ((r = ap.match(/^\/engine\/api\/v1\/printers\/(\d+)\/camera\/snapshot$/)) || (r = ap.match(/^\/cameras\/frame\.jpeg$/)) ||
        (r = ap.match(/^\/engine\/api\/v1\/archives\/(\d+)\/thumbnail$/))) {
      res.writeHead(200, { 'content-type': 'image/svg+xml', 'cache-control': 'no-store' });
      return res.end(frame(r[1] || u.searchParams.get('src')?.replace(/\D/g, '')));
    }
    const out = route(req.method, ap, u.searchParams);
    if (out === null || out === undefined) {
      // Anything not modelled: an empty list for collections, an empty object otherwise.
      return json(res, 200, /\/$|s$/.test(ap.split('?')[0]) ? [] : {});
    }
    return json(res, 200, out);
  }
  handler(req, res, () => { res.writeHead(404); res.end('not found'); });
}).listen(port, '127.0.0.1', () => console.log(`screens server on http://127.0.0.1:${port}${BASE}`));
