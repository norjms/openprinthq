// Vendor-neutral project model shared by every reader and writer.
//
// Doc = {
//   meta:   { title, designer, license, source, sourceFormat, application },
//   plates: [ { name, material, items: [ { subs, op, name } ] } ],   // 2D, millimetres, y down
//   models: [ Model3D ],                                              // 3D inner engraving
//   warnings: [ string ],
// }
// op is one of OPS. subs is a path from lib/path.js.
//
// Model3D = {
//   name, obj: string (OBJ text), mtl: {filename, text} | null,
//   textures: [ { filename, mime, bytes } ],
//   size: [x, y, z]  target bounding box in mm (x, y on the bed, z = height),
//   center: [x, y, z] in mm when known,
//   world: number[16] | null   xTool column-major matrix (OBJ coords -> mm, Y up), when known
// }

export const OPS = {
  cut: { label: 'Cut', color: '#e53935' },
  score: { label: 'Score / line engrave', color: '#1e88e5' },
  fill: { label: 'Fill engrave', color: '#212121' },
};

export function newDoc(sourceFormat) {
  return { meta: { sourceFormat }, plates: [], models: [], warnings: [] };
}

export function docSummary(doc) {
  const counts = { cut: 0, score: 0, fill: 0 };
  for (const p of doc.plates) for (const it of p.items) counts[it.op] = (counts[it.op] || 0) + 1;
  return { plates: doc.plates.length, models: doc.models.length, counts };
}

// OBJ helpers (3D)
export function objStats(objText) {
  let v = 0, f = 0;
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  let i = 0;
  const n = objText.length;
  while (i < n) {
    let j = objText.indexOf('\n', i);
    if (j < 0) j = n;
    const c0 = objText.charCodeAt(i), c1 = objText.charCodeAt(i + 1);
    if (c0 === 118 && (c1 === 32 || c1 === 9)) { // "v "
      const parts = objText.slice(i + 2, j).trim().split(/\s+/);
      for (let k = 0; k < 3; k++) {
        const val = parseFloat(parts[k]);
        if (val < min[k]) min[k] = val;
        if (val > max[k]) max[k] = val;
      }
      v++;
    } else if (c0 === 102 && (c1 === 32 || c1 === 9)) f++;
    i = j + 1;
  }
  return { vertices: v, faces: f, min, max };
}

// Column-major 4x4 helpers
export function matApply(m, p) {
  return [
    m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
    m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
    m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14],
  ];
}

// Uniform scale s plus translation t, column-major.
export const scaleTranslate = (s, t) => [s, 0, 0, 0, 0, s, 0, 0, 0, 0, s, 0, t[0], t[1], t[2], 1];
