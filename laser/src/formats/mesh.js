// Plain 3D model export: OBJ (zipped with its MTL and textures when it has them) and binary STL.
// Also reads .obj / .stl as 3D input.
import { utf8, safeName } from '../lib/util.js';
import { newDoc } from '../lib/model.js';

export const obj = {
  id: 'obj', label: '3D model (.obj)', extensions: ['.obj'],
  async sniff(bytes, name) { return /\.obj$/i.test(name); },
  async read(bytes, name) {
    const doc = newDoc('obj');
    const nm = name.replace(/\.obj$/i, '');
    doc.meta = { sourceFormat: 'obj', title: nm };
    doc.models.push({ name: nm, objFilename: name, obj: utf8(bytes), mtl: null, textures: [], size: null, center: null, world: null });
    return doc;
  },
  async write(doc) {
    if (!doc.models.length) throw new Error('OBJ export needs a 3D model; this file has none.');
    const out = [];
    for (const m of doc.models) {
      const base = safeName(m.name || doc.meta.title);
      if (!m.mtl && !m.textures.length) { out.push({ name: base + '.obj', bytes: new TextEncoder().encode(m.obj) }); continue; }
      const zip = new globalThis.JSZip();
      zip.file(m.objFilename || base + '.obj', m.obj);
      if (m.mtl) zip.file(m.mtl.filename, m.mtl.text);
      for (const t of m.textures) zip.file(t.filename, t.bytes);
      out.push({ name: base + '_obj.zip', bytes: await zip.generateAsync({ type: 'uint8array', compression: 'DEFLATE' }) });
    }
    return out;
  },
};

function objTriangles(text) {
  const v = [];
  const tris = [];
  for (const line of text.split('\n')) {
    if (line.startsWith('v ') || line.startsWith('v\t')) {
      const p = line.trim().split(/\s+/);
      v.push([+p[1], +p[2], +p[3]]);
    } else if (line.startsWith('f ') || line.startsWith('f\t')) {
      const idx = line.trim().split(/\s+/).slice(1).map((t) => { const k = parseInt(t.split('/')[0], 10); return k < 0 ? v.length + k : k - 1; });
      for (let i = 1; i + 1 < idx.length; i++) tris.push(idx[0], idx[i], idx[i + 1]);
    }
  }
  return { v, tris };
}

// Mesh to STL in mm, Z up, base at z = 0, scaled to the model's target size when known.
export function objToStl(m) {
  const { v, tris } = objTriangles(m.obj);
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (const p of v) for (let k = 0; k < 3; k++) { if (p[k] < min[k]) min[k] = p[k]; if (p[k] > max[k]) max[k] = p[k]; }
  let s = 1;
  if (m.world) s = Math.hypot(m.world[0], m.world[1], m.world[2]);
  else if (m.size) s = Math.max(...m.size) / Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2]);
  const n = tris.length / 3;
  const buf = new ArrayBuffer(84 + 50 * n);
  const dv = new DataView(buf);
  const head = 'Converted with OpenPrintHQ Laser';
  for (let i = 0; i < head.length; i++) dv.setUint8(i, head.charCodeAt(i));
  dv.setUint32(80, n, true);
  // OBJ Y-up -> STL Z-up: (x, y, z) -> (x, -z, y)
  const P = (i) => { const p = v[i]; return [(p[0] - min[0]) * s, (max[2] - p[2]) * s, (p[1] - min[1]) * s]; };
  let o = 84;
  for (let t = 0; t < n; t++) {
    const a = P(tris[3 * t]), b = P(tris[3 * t + 1]), c = P(tris[3 * t + 2]);
    const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], w = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    let nx = u[1] * w[2] - u[2] * w[1], ny = u[2] * w[0] - u[0] * w[2], nz = u[0] * w[1] - u[1] * w[0];
    const l = Math.hypot(nx, ny, nz) || 1; nx /= l; ny /= l; nz /= l;
    for (const val of [nx, ny, nz, ...a, ...b, ...c]) { dv.setFloat32(o, val, true); o += 4; }
    dv.setUint16(o, 0, true); o += 2;
  }
  return new Uint8Array(buf);
}

export const stl = {
  id: 'stl', label: '3D model (.stl)', extensions: ['.stl'],
  async sniff(bytes, name) { return /\.stl$/i.test(name); },
  async read(bytes, name) {
    // binary or ASCII STL -> OBJ text (Z-up STL -> Y-up OBJ)
    const doc = newDoc('stl');
    const nm = name.replace(/\.stl$/i, '');
    doc.meta = { sourceFormat: 'stl', title: nm };
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const lines = [];
    let faces = 0;
    const isAscii = utf8(bytes.subarray(0, 5)).toLowerCase() === 'solid' && bytes.length !== 84 + 50 * dv.getUint32(80, true);
    if (isAscii) {
      const verts = utf8(bytes).match(/vertex\s+[-+\d.eE]+\s+[-+\d.eE]+\s+[-+\d.eE]+/g) || [];
      for (const vx of verts) { const p = vx.trim().split(/\s+/); lines.push(`v ${p[1]} ${p[3]} ${-p[2]}`); }
      faces = verts.length / 3;
    } else {
      const n = dv.getUint32(80, true);
      for (let t = 0; t < n; t++) {
        const o = 84 + 50 * t + 12;
        for (let k = 0; k < 3; k++) {
          const x = dv.getFloat32(o + 12 * k, true), y = dv.getFloat32(o + 12 * k + 4, true), z = dv.getFloat32(o + 12 * k + 8, true);
          lines.push(`v ${x} ${z} ${-y}`);
        }
      }
      faces = n;
    }
    for (let f = 0; f < faces; f++) lines.push(`f ${3 * f + 1} ${3 * f + 2} ${3 * f + 3}`);
    doc.models.push({ name: nm, objFilename: nm + '.obj', obj: lines.join('\n') + '\n', mtl: null, textures: [], size: null, center: null, world: null });
    return doc;
  },
  async write(doc) {
    if (!doc.models.length) throw new Error('STL export needs a 3D model; this file has none.');
    return doc.models.map((m) => ({ name: safeName(m.name || doc.meta.title) + '.stl', bytes: objToStl(m) }));
  },
};
