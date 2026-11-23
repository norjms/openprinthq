// SVG: universal interchange. Writer emits one SVG per plate in millimetres with operations by
// colour (red cut, blue score, black fill). Reader maps colours back the same way.
import { parseSvgPath, transformPath, mul, IDENTITY, bbox, toD, pathFromRect, pathFromEllipse, fmt } from '../lib/path.js';
import { newDoc, OPS } from '../lib/model.js';
import { utf8, safeName } from '../lib/util.js';

export const id = 'svg';
export const label = 'SVG (any laser software)';
export const extensions = ['.svg'];

export async function sniff(bytes, name) {
  if (/\.svg$/i.test(name)) return true;
  const head = utf8(bytes.subarray(0, 512));
  return /<svg[\s>]/i.test(head);
}

const UNIT = { mm: 1, cm: 10, in: 25.4, pt: 25.4 / 72, pc: 25.4 / 6, px: 25.4 / 96, '': 25.4 / 96 };
function lengthMM(v) {
  if (v == null || v === '') return null;
  const m = String(v).trim().match(/^([-+]?[\d.]+(?:e[-+]?\d+)?)\s*([a-z%]*)$/i);
  if (!m || m[2] === '%') return null;
  return parseFloat(m[1]) * (UNIT[m[2].toLowerCase()] ?? 25.4 / 96);
}

function parseTransform(s) {
  let m = IDENTITY;
  if (!s) return m;
  const re = /(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)/g;
  let t;
  while ((t = re.exec(s))) {
    const a = t[2].split(/[\s,]+/).filter(Boolean).map(Number);
    let n;
    switch (t[1]) {
      case 'matrix': n = a.slice(0, 6); break;
      case 'translate': n = [1, 0, 0, 1, a[0] || 0, a[1] || 0]; break;
      case 'scale': n = [a[0], 0, 0, a.length > 1 ? a[1] : a[0], 0, 0]; break;
      case 'rotate': {
        const r = ((a[0] || 0) * Math.PI) / 180, c = Math.cos(r), si = Math.sin(r);
        n = [c, si, -si, c, 0, 0];
        if (a.length > 2) n = mul(mul([1, 0, 0, 1, a[1], a[2]], n), [1, 0, 0, 1, -a[1], -a[2]]);
        break;
      }
      case 'skewX': n = [1, 0, Math.tan((a[0] * Math.PI) / 180), 1, 0, 0]; break;
      case 'skewY': n = [1, Math.tan((a[0] * Math.PI) / 180), 0, 1, 0, 0]; break;
    }
    m = mul(m, n);
  }
  return m;
}

function styleOf(el, inherited) {
  const st = { ...inherited };
  for (const k of ['fill', 'stroke', 'display', 'visibility', 'fill-rule']) {
    const v = el.getAttribute(k);
    if (v != null && v !== '') st[k] = v.trim();
  }
  const css = el.getAttribute('style');
  if (css) for (const decl of css.split(';')) {
    const [k, v] = decl.split(':').map((x) => x && x.trim());
    if (k && v && ['fill', 'stroke', 'display', 'visibility', 'fill-rule'].includes(k)) st[k] = v;
  }
  return st;
}

function rgb(c) {
  if (!c) return null;
  c = c.trim().toLowerCase();
  const named = { red: [255, 0, 0], blue: [0, 0, 255], black: [0, 0, 0], green: [0, 128, 0], white: [255, 255, 255] };
  if (named[c]) return named[c];
  let m = c.match(/^#([0-9a-f]{3})$/);
  if (m) return m[1].split('').map((h) => parseInt(h + h, 16));
  m = c.match(/^#([0-9a-f]{6})/);
  if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
  m = c.match(/^rgb\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/);
  if (m) return m.slice(1, 4).map(Number);
  return null;
}

// Operation from colour: filled shapes engrave; red strokes cut; blue strokes score; others cut.
function opFor(st, mapping) {
  const fill = st.fill && st.fill !== 'none' && st.fill !== 'transparent' ? rgb(st.fill) || [0, 0, 0] : null;
  if (fill && mapping.fillMeansEngrave) return 'fill';
  const s = rgb(st.stroke);
  if (!s) return fill ? 'fill' : 'cut';
  const [r, g, b] = s;
  if (b > 150 && r < 120) return 'score';
  if (r > 150 && g < 120 && b < 120) return 'cut';
  return mapping.defaultStroke || 'cut';
}

function pts(attr) {
  const n = (attr || '').trim().split(/[\s,]+/).filter(Boolean).map(Number);
  const out = [];
  for (let i = 0; i + 1 < n.length; i += 2) out.push([n[i], n[i + 1]]);
  return out;
}

function shapeSubs(el) {
  const g = (k) => parseFloat(el.getAttribute(k) || '0');
  switch (el.localName || el.nodeName) {
    case 'path': return el.getAttribute('d') ? parseSvgPath(el.getAttribute('d')) : null;
    case 'rect': {
      const w = g('width'), h = g('height');
      if (!(w > 0 && h > 0)) return null;
      const rxA = el.getAttribute('rx'), ryA = el.getAttribute('ry');
      const rx = rxA != null ? parseFloat(rxA) : ryA != null ? parseFloat(ryA) : 0;
      const ry = ryA != null ? parseFloat(ryA) : rx;
      return pathFromRect(g('x'), g('y'), w, h, rx, ry);
    }
    case 'circle': return g('r') > 0 ? pathFromEllipse(g('cx'), g('cy'), g('r'), g('r')) : null;
    case 'ellipse': return g('rx') > 0 && g('ry') > 0 ? pathFromEllipse(g('cx'), g('cy'), g('rx'), g('ry')) : null;
    case 'line': return [{ s: [g('x1'), g('y1')], closed: false, segs: [{ t: 'L', p: [g('x2'), g('y2')] }] }];
    case 'polyline': case 'polygon': {
      const p = pts(el.getAttribute('points'));
      if (p.length < 2) return null;
      return [{ s: p[0], closed: (el.localName || el.nodeName) === 'polygon', segs: p.slice(1).map((q) => ({ t: 'L', p: q })) }];
    }
    default: return null;
  }
}

export async function read(bytes, name, opts = {}) {
  const DP = globalThis.DOMParser;
  const xml = new DP().parseFromString(utf8(bytes), 'image/svg+xml');
  const root = xml.documentElement;
  if (!root || (root.localName || root.nodeName) !== 'svg') throw new Error('not an SVG file');
  const doc = newDoc('svg');
  doc.meta = { sourceFormat: 'svg', title: name.replace(/\.svg$/i, '') };
  const titleEl = root.getElementsByTagName('title')[0];
  if (titleEl?.textContent?.trim()) doc.meta.title = titleEl.textContent.trim();
  // user units -> mm
  const vb = (root.getAttribute('viewBox') || '').split(/[\s,]+/).filter(Boolean).map(Number);
  const wmm = lengthMM(root.getAttribute('width')), hmm = lengthMM(root.getAttribute('height'));
  let base = IDENTITY;
  if (vb.length === 4 && vb[2] > 0 && vb[3] > 0) {
    const sx = wmm ? wmm / vb[2] : 25.4 / 96, sy = hmm ? hmm / vb[3] : sx;
    base = [sx, 0, 0, sy, -vb[0] * sx, -vb[1] * sy];
  } else base = [25.4 / 96, 0, 0, 25.4 / 96, 0, 0];
  const mapping = { fillMeansEngrave: true, defaultStroke: 'cut', ...(opts.mapping || {}) };
  const items = [];
  let skipped = 0;
  const walk = (el, m, st) => {
    for (const ch of Array.from(el.childNodes || [])) {
      if (ch.nodeType !== 1) continue;
      const tag = ch.localName || ch.nodeName;
      if (['defs', 'clipPath', 'mask', 'metadata', 'title', 'desc', 'style', 'symbol', 'pattern', 'marker', 'linearGradient', 'radialGradient'].includes(tag)) continue;
      const s2 = styleOf(ch, st);
      if (s2.display === 'none' || s2.visibility === 'hidden') continue;
      const m2 = mul(m, parseTransform(ch.getAttribute('transform')));
      if (tag === 'g' || tag === 'a' || tag === 'svg') { walk(ch, m2, s2); continue; }
      const subs = shapeSubs(ch);
      if (!subs) { if (!['path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon'].includes(tag)) skipped++; continue; }
      const op = opFor(s2, mapping);
      items.push({ op, name: ch.getAttribute('id') || '', subs: transformPath(subs, m2) });
    }
  };
  walk(root, base, { fill: 'black' });
  if (skipped) doc.warnings.push(`Skipped ${skipped} SVG element(s) that are not shapes (text, images, <use>). Convert text to paths before importing.`);
  if (items.length) doc.plates.push({ name: 'Plate 1', material: '', items });
  return doc;
}

export function plateToSvg(plate, meta = {}, pad = 2) {
  const all = plate.items.flatMap((i) => i.subs);
  const [x0, y0, x1, y1] = bbox(all);
  const w = x1 - x0 + 2 * pad, h = y1 - y0 + 2 * pad;
  const groups = { fill: [], score: [], cut: [] };
  for (const it of plate.items) groups[it.op].push(it);
  const esc = (s) => String(s || '').replace(/[<>&"]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));
  const lines = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" width="${fmt(w, 3)}mm" height="${fmt(h, 3)}mm" viewBox="${fmt(x0 - pad, 3)} ${fmt(y0 - pad, 3)} ${fmt(w, 3)} ${fmt(h, 3)}">`,
    `<title>${esc(meta.title || 'Converted')}${plate.name ? ' - ' + esc(plate.name) : ''}</title>`,
    `<desc>${esc([meta.designer && 'Designer: ' + meta.designer, meta.license && 'License: ' + meta.license, meta.source && 'Source: ' + meta.source, plate.material && 'Material: ' + plate.material, 'Red = cut, blue = score, black fill = engrave. Converted with OpenPrintHQ Laser.'].filter(Boolean).join(' | '))}</desc>`,
  ];
  if (groups.fill.length) {
    lines.push(`<g id="fill-engrave" fill="${OPS.fill.color === '#212121' ? '#000000' : OPS.fill.color}" fill-rule="evenodd" stroke="none">`);
    for (const it of groups.fill) lines.push(`<path d="${toD(it.subs)}"/>`);
    lines.push('</g>');
  }
  if (groups.score.length) {
    lines.push('<g id="score" fill="none" stroke="#0000ff" stroke-width="0.1">');
    for (const it of groups.score) lines.push(`<path d="${toD(it.subs)}"/>`);
    lines.push('</g>');
  }
  if (groups.cut.length) {
    lines.push('<g id="cut" fill="none" stroke="#ff0000" stroke-width="0.1">');
    for (const it of groups.cut) lines.push(`<path d="${toD(it.subs)}"/>`);
    lines.push('</g>');
  }
  lines.push('</svg>');
  return lines.join('\n');
}

export async function write(doc) {
  if (!doc.plates.length) throw new Error('SVG needs 2D paths; this file has none.');
  const base = safeName(doc.meta.title);
  return doc.plates.map((p, i) => ({
    name: doc.plates.length > 1 ? `${base}_${safeName(p.name || 'plate_' + (i + 1))}${p.material ? '_' + safeName(p.material) : ''}.svg` : `${base}.svg`,
    bytes: new TextEncoder().encode(plateToSvg(p, doc.meta)),
  }));
}
