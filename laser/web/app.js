import { readFile, convert, TARGETS } from './lib/registry.js';
import { docSummary, objStats, matApply, OPS } from './lib/model.js';
import { bbox } from './lib/path.js';

const VERSION = '1.0.0';
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[<>&"]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

const STATUS = {
  'xs-p2s': ['v', 'Checked', 'Writer output was compared with files xTool Studio 1.10 saved itself: identical shapes, operations and settings.'],
  'xcs-p2s': ['v', 'Checked', 'Same object structure as xTool Studio 1.10 saves; every plate re-read and compared shape by shape.'],
  'lac-r1': ['b', 'Beta', 'Same file structure as Bambu Suite 1.4 projects; not yet opened in Bambu Suite.'],
  'lac-h2d': ['b', 'Beta', 'Same file structure as Bambu Suite 1.4 projects; not yet opened in Bambu Suite.'],
  svg: ['v', 'Checked', 'Pixel-compared with Bambu Suite’s own plate previews.'],
  'xs-f2uuv': ['v', 'Checked', 'Writer output was compared with a 3D project xTool Studio 1.10 saved: the model file is byte-for-byte identical.'],
  'xcs-f2uuv': ['v', 'Checked', 'Same structure as xTool Studio 1.10 3D projects; the model, textures and size survive a round trip.'],
  'fds-t1': ['b', 'Beta', 'Same block layout as Falcon Design Space crystal projects; placement in the crystal may need adjusting.'],
  obj: ['v', 'Standard', 'Plain OBJ with its MTL and textures.'],
  stl: ['v', 'Standard', 'Binary STL in millimetres, Z up.'],
};

let doc = null, srcName = '', selected = null, plateIdx = 0;
const tplCache = {};
const loadTemplate = async (name) => (tplCache[name] ??= await (await fetch(`templates/${name}.json`)).json());

// ---------- file input ----------
const drop = $('#drop'), input = $('#file');
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) open(f); });
input.addEventListener('change', () => { if (input.files[0]) open(input.files[0]); input.value = ''; });
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => { e.preventDefault(); const f = e.dataTransfer?.files?.[0]; if (f) open(f); });

const tick = () => new Promise((r) => setTimeout(r, 30));

async function open(file) {
  const info = $('#info');
  info.classList.remove('hidden');
  info.innerHTML = `<div class="lc-busy"><i class="lc-spin"></i>Reading ${esc(file.name)} (${(file.size / 1048576).toFixed(1)} MB)…</div>`;
  drop.classList.add('busy');
  $('#results').innerHTML = '';
  await tick();
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    doc = await readFile(bytes, file.name);
    srcName = file.name;
    plateIdx = 0;
    showInfo();
    renderTargets();
    renderTabs();
    draw();
  } catch (e) {
    console.error(e);
    doc = null;
    info.innerHTML = `<div class="ah-banner ah-banner--bad"><div><b>Couldn’t read ${esc(file.name)}.</b><br>${esc(e.message)}</div></div>`;
    renderTargets(); renderTabs(); draw();
  } finally {
    drop.classList.remove('busy');
  }
}

function showInfo() {
  const m = doc.meta, s = docSummary(doc);
  const rows = [
    ['Format', m.sourceLabel], m.application && ['Saved by', m.application], m.machine && ['Machine', m.machine],
    m.designer && ['Designer', m.designer], m.source && ['Source', `<a href="${esc(m.source)}" target="_blank" rel="noopener">${esc(m.source.replace(/^https?:\/\//, ''))}</a>`],
    m.notes && ['Notes', m.notes],
  ].filter(Boolean);
  const chips = [];
  if (s.plates) chips.push(`${s.plates} plate${s.plates > 1 ? 's' : ''}`);
  if (s.counts.cut) chips.push(`${s.counts.cut} cut`);
  if (s.counts.score) chips.push(`${s.counts.score} score`);
  if (s.counts.fill) chips.push(`${s.counts.fill} fill`);
  for (const md of doc.models) {
    const st = objStats(md.obj);
    chips.push(`3D: ${st.vertices.toLocaleString()} vertices`);
    if (md.size) chips.push(`${md.size.map((v) => v.toFixed(1)).join(' × ')} mm`);
    if (md.textures.length) chips.push(`${md.textures.length} textures`);
  }
  const mats = [...new Set(doc.plates.map((p) => p.material).filter(Boolean))];
  if (mats.length) rows.push(['Materials', mats.join(', ')]);
  $('#info').innerHTML = `
    <h3 class="lc-title">${esc(m.title || srcName)}</h3>
    <dl class="lc-kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${k === 'Source' ? v : esc(v)}</dd>`).join('')}</dl>
    <div class="lc-chips">${chips.map((c, i) => `<span class="ah-chip${i === 0 ? ' ah-chip--live' : ''}">${esc(c)}</span>`).join('')}</div>
    ${m.license ? `<div class="ah-banner ah-banner--live"><div><b>Licence:</b> ${esc(m.license)}. Converting for your own machine doesn’t change what the licence allows.</div></div>` : ''}
    ${doc.warnings.map((w) => `<div class="ah-banner"><div>${esc(w)}</div></div>`).join('')}
    ${!s.plates && !doc.models.length ? '<div class="ah-banner ah-banner--bad"><div>Nothing convertible was found in this file.</div></div>' : ''}`;
}

// ---------- targets ----------
function renderTargets() {
  const has2d = !!doc?.plates.length, has3d = !!doc?.models.length;
  const groups = [['2D laser projects', '2d'], ['3D inner engraving', '3d']];
  if (has3d && !has2d) groups.reverse();
  let html = '';
  for (const [title, kind] of groups) {
    html += `<div class="ah-group-label">${title}</div>`;
    for (const t of TARGETS.filter((x) => x.kind === kind)) {
      const ok = kind === '2d' ? has2d : has3d;
      const [cls, badge, why] = STATUS[t.id];
      html += `<label class="lc-t ${ok ? '' : 'off'} ${selected === t.id && ok ? 'sel' : ''}" title="${esc(why)}">
        <input type="radio" name="tgt" value="${t.id}" ${ok ? '' : 'disabled'} ${selected === t.id && ok ? 'checked' : ''}>
        <span><b>${esc(t.label)}<span class="ah-chip ${cls === 'v' ? 'ah-chip--ok' : 'ah-chip--warn'}">${badge}</span></b><small>${esc(t.sub)}</small></span></label>`;
    }
  }
  $('#targets').innerHTML = html;
  if (selected && !TARGETS.find((t) => t.id === selected && (t.kind === '2d' ? has2d : has3d))) selected = null;
  $('#go').disabled = !selected;
  $('#targets').querySelectorAll('input').forEach((r) => r.addEventListener('change', () => { selected = r.value; renderTargets(); }));
}

$('#go').addEventListener('click', async () => {
  if (!doc || !selected) return;
  const t = TARGETS.find((x) => x.id === selected);
  const res = $('#results');
  res.innerHTML = `<div class="lc-busy"><i class="lc-spin"></i>Converting to ${esc(t.label)}…</div>`;
  $('#go').disabled = true;
  await tick();
  try {
    const files = await convert(doc, selected, loadTemplate, {
      covers: { plate: async (p) => plateDataUrl(p, 400) },
      thumbnails: async (p) => (p ? pngBytes(p, 600) : doc.plates[0] ? pngBytes(doc.plates[0], 600) : null),
    });
    showResults(files);
  } catch (e) {
    console.error(e);
    res.innerHTML = `<div class="ah-banner ah-banner--bad"><div><b>Conversion failed.</b><br>${esc(e.message)}</div></div>`;
  } finally {
    $('#go').disabled = !selected;
  }
});

const urls = [];
function showResults(files) {
  urls.splice(0).forEach((u) => URL.revokeObjectURL(u));
  const link = (name, bytes) => { const u = URL.createObjectURL(new Blob([bytes])); urls.push(u); return `<a class="ah-btn ah-btn--primary ah-btn--sm" href="${u}" download="${esc(name)}">Download</a>`; };
  const size = (b) => (b.length > 1048576 ? (b.length / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b.length / 1024)) + ' KB');
  let html = files.map((f) => `<div class="lc-dl"><span>${esc(f.name)} · ${size(f.bytes)}</span>${link(f.name, f.bytes)}</div>`).join('');
  $('#results').innerHTML = html;
  if (files.length > 1) {
    const zip = new JSZip();
    for (const f of files) zip.file(f.name, f.bytes);
    zip.generateAsync({ type: 'uint8array', compression: 'STORE' }).then((z) => {
      const base = (doc.meta.title || srcName).replace(/[^\p{L}\p{N}._ -]+/gu, '_').trim() || 'converted';
      $('#results').insertAdjacentHTML('afterbegin', `<div class="lc-dl"><span><b>All ${files.length} files</b> · ${size(z)}</span>${link(base + '.zip', z)}</div>`);
    });
  } else {
    // single file: start the download straight away
    $('#results').querySelector('a')?.click();
  }
}

// ---------- preview ----------
function renderTabs() {
  const tabs = $('#tabs');
  if (!doc) { tabs.innerHTML = ''; tabs.classList.add('hidden'); return; }
  const list = [...doc.plates.map((p, i) => [`p${i}`, `${p.name || 'Plate ' + (i + 1)}${p.material ? ' · ' + p.material : ''}`]), ...doc.models.map((m, i) => [`m${i}`, `3D · ${m.name}`])];
  if (list.length < 2) { tabs.innerHTML = ''; tabs.classList.add('hidden'); return; }
  tabs.classList.remove('hidden');
  tabs.innerHTML = list.map(([k, l], i) => `<button role="tab" aria-selected="${i === plateIdx}" data-i="${i}">${esc(l)}</button>`).join('');
  tabs.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { plateIdx = +b.dataset.i; renderTabs(); draw(); }));
}

const cssVar = (n, d) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || d;

function drawPlate(ctx, plate, W, H, pad = 18, colors = null) {
  const C = colors || { cut: cssVar('--lc-cut', '#ff9a7a'), score: cssVar('--ah-accent', '#8fd3ff'), fill: cssVar('--lc-fill', 'rgba(236,243,248,0.78)') };
  const all = plate.items.flatMap((i) => i.subs);
  const [x0, y0, x1, y1] = bbox(all);
  const s = Math.min((W - 2 * pad) / (x1 - x0 || 1), (H - 2 * pad) / (y1 - y0 || 1));
  const ox = (W - (x1 - x0) * s) / 2 - x0 * s, oy = (H - (y1 - y0) * s) / 2 - y0 * s;
  const tracePath = (subs) => {
    ctx.beginPath();
    for (const sp of subs) {
      ctx.moveTo(ox + sp.s[0] * s, oy + sp.s[1] * s);
      for (const g of sp.segs) {
        if (g.t === 'L') ctx.lineTo(ox + g.p[0] * s, oy + g.p[1] * s);
        else ctx.bezierCurveTo(ox + g.c1[0] * s, oy + g.c1[1] * s, ox + g.c2[0] * s, oy + g.c2[1] * s, ox + g.p[0] * s, oy + g.p[1] * s);
      }
      if (sp.closed) ctx.closePath();
    }
  };
  for (const op of ['fill', 'score', 'cut']) {
    for (const it of plate.items) {
      if (it.op !== op) continue;
      tracePath(it.subs);
      if (op === 'fill') { ctx.fillStyle = C.fill; ctx.fill('evenodd'); }
      else { ctx.strokeStyle = op === 'cut' ? C.cut : C.score; ctx.lineWidth = 1.1; ctx.stroke(); }
    }
  }
}

function drawModel(ctx, m, W, H) {
  const lines = m.obj.split('\n');
  const pts = [];
  const step = Math.max(1, Math.floor(lines.length / 400000));
  for (let i = 0; i < lines.length; i += step) {
    const l = lines[i];
    if (l.charCodeAt(0) === 118 && l.charCodeAt(1) === 32) {
      const p = l.slice(2).trim().split(/\s+/).map(Number);
      pts.push(m.world ? matApply(m.world, p) : p);
    }
  }
  if (!pts.length) return;
  // view from the front: x right, y (height) up, z (depth) shading
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, z0 = Infinity, z1 = -Infinity;
  for (const p of pts) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); z0 = Math.min(z0, p[2]); z1 = Math.max(z1, p[2]); }
  const pad = 24, s = Math.min((W - 2 * pad) / (x1 - x0 || 1), (H - 2 * pad) / (y1 - y0 || 1));
  const ox = (W - (x1 - x0) * s) / 2, oy = (H - (y1 - y0) * s) / 2;
  pts.sort((a, b) => a[2] - b[2]);
  const r = Math.max(0.6, Math.min(1.6, 600000 / pts.length / 4));
  for (const p of pts) {
    const t = (p[2] - z0) / (z1 - z0 || 1);
    ctx.fillStyle = `rgba(${Math.round(120 + 135 * t)},${Math.round(200 + 55 * t)},255,${0.25 + 0.5 * t})`;
    ctx.fillRect(ox + (p[0] - x0) * s, oy + (y1 - p[1]) * s, r, r);
  }
}

function draw() {
  const cv = $('#canvas'), stage = $('#stage');
  const dpr = window.devicePixelRatio || 1;
  const W = stage.clientWidth, H = stage.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const empty = !doc || (!doc.plates.length && !doc.models.length);
  $('#empty').classList.toggle('hidden', !empty);
  if (empty) return;
  if (plateIdx < doc.plates.length) { $('#legend').classList.remove('hidden'); drawPlate(ctx, doc.plates[plateIdx], W, H); }
  else { $('#legend').classList.add('hidden'); drawModel(ctx, doc.models[plateIdx - doc.plates.length], W, H); }
}
window.addEventListener('resize', () => draw());

function plateCanvas(plate, size) {
  const all = plate.items.flatMap((i) => i.subs);
  const [x0, y0, x1, y1] = bbox(all);
  const W = size, H = Math.max(40, Math.round((size * (y1 - y0)) / (x1 - x0 || 1)));
  const c = document.createElement('canvas');
  c.width = W; c.height = H;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, W, H);
  drawPlate(ctx, plate, W, H, 6, { cut: '#e53935', score: '#1e88e5', fill: 'rgba(38,50,56,0.9)' });
  return c;
}
const plateDataUrl = (p, size) => plateCanvas(p, size).toDataURL('image/png');
const pngBytes = (p, size) => new Promise((res) => plateCanvas(p, size).toBlob(async (b) => res(new Uint8Array(await b.arrayBuffer())), 'image/png'));

// ---------- static bits ----------
$('#verify').innerHTML = TARGETS.map((t) => `<li><b>${esc(t.label)}</b> (${esc(t.sub)}): ${esc(STATUS[t.id][2])}</li>`).join('') +
  '<li><b>Reading</b> .lac, .xcs, .xs and .fds: tested on real projects saved by Bambu Suite 1.4, xTool Studio 1.10 and Falcon Design Space, including Bambu’s non-standard curve and arc encoding.</li>';
$('#ver').textContent = 'v' + VERSION;
renderTargets();
draw();
