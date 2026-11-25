// Creality Falcon Design Space (.fds): a sequence of blocks, each
//   uint32 little-endian length N, then N bytes of Qt qCompress data
//   (uint32 big-endian uncompressed size + zlib stream).
// Block 1: device + material library, block 2: 2D project (layers + shapes),
// block 3: crystal (3D inner) engraving with the model embedded as a base64 OBJ asset.
import { zlibInflate, zlibDeflate, utf8, enc, bytesToB64, b64ToBytes, safeName } from '../lib/util.js';
import { newDoc, objStats } from '../lib/model.js';

export const id = 'fds';
export const label = 'Creality Falcon Design Space (.fds)';
export const extensions = ['.fds'];

export async function sniff(bytes, name) {
  if (!/\.fds$/i.test(name) || bytes.length < 10) return false;
  return bytes[8] === 0x78; // zlib header after the two length fields
}

export async function readBlocks(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const blocks = [];
  let i = 0;
  while (i + 8 <= bytes.length) {
    const n = dv.getUint32(i, true);
    if (n < 4 || i + 4 + n > bytes.length) throw new Error('corrupt .fds block table');
    const expect = dv.getUint32(i + 4, false);
    const raw = await zlibInflate(bytes.subarray(i + 8, i + 4 + n));
    if (raw.length !== expect) throw new Error('corrupt .fds block (size mismatch)');
    blocks.push(JSON.parse(utf8(raw)));
    i += 4 + n;
  }
  return blocks;
}

export async function writeBlocks(objs) {
  const parts = [];
  for (const o of objs) {
    const raw = enc(JSON.stringify(o));
    const z = await zlibDeflate(raw);
    const head = new Uint8Array(8);
    const dv = new DataView(head.buffer);
    dv.setUint32(0, z.length + 4, true);
    dv.setUint32(4, raw.length, false);
    parts.push(head, z);
  }
  const total = parts.reduce((a, p) => a + p.length, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

export async function read(bytes, name) {
  const blocks = await readBlocks(bytes);
  const doc = newDoc('fds');
  doc.meta = { sourceFormat: 'fds', application: 'Creality Falcon Design Space', title: name.replace(/\.fds$/i, ''), machine: blocks[0]?.deviceName || '' };
  const proj2d = blocks[1];
  const shapes = proj2d?.shapes || [];
  if (shapes.length) doc.warnings.push(`This Falcon file has ${shapes.length} 2D shape(s). Reading Falcon 2D shapes is not supported yet (no sample of that layout); send one to add it.`);
  const crystal = blocks.find((b) => b && b.crtystalEngrave)?.crtystalEngrave;
  if (crystal) {
    const assets = new Map((crystal.Assets || []).map((a) => [a.id, a]));
    const cube = crystal.EngraveParm?.crystalCubeSize || [50, 50, 50];
    for (const md of crystal.ModelData || []) {
      const a = assets.get(md.sourceAssetId);
      if (!a?.data) continue;
      const obj = utf8(b64ToBytes(a.data));
      const s = md.scale?.[0] || 1;
      // Falcon fits the model to the crystal and applies scale; use that as the target size.
      const st = objStats(obj);
      const dims = [0, 1, 2].map((k) => st.max[k] - st.min[k]);
      const maxDim = Math.max(...dims);
      const target = Math.min(cube[0], cube[1], cube[2]) * s;
      const k = target / maxDim;
      doc.models.push({
        name: (a.fileName || 'model').replace(/\.[^.]+$/, ''), objFilename: a.fileName || 'model.obj', obj, mtl: null, textures: [],
        size: [dims[0] * k, dims[2] * k, dims[1] * k], center: null, world: null,
        crystal: { type: crystal.EngraveParm?.crystalMaterialName || 'crystal cube', size: cube },
      });
    }
    if (crystal.EngraveParm) doc.meta.notes = `Falcon crystal: ${cube.join(' x ')} mm ${crystal.EngraveParm.crystalMaterialName || ''}`.trim();
  }
  return doc;
}

export async function write(doc, opts) {
  const t = opts.template;
  if (!doc.models.length) throw new Error('Falcon crystal export needs a 3D model; this file has none.');
  const out = [];
  for (const m of doc.models) {
    const st = objStats(m.obj);
    const dims = [0, 1, 2].map((k) => st.max[k] - st.min[k]);
    const target = Math.max(...(m.size || [45, 45, 45]));
    let cube = [...(t.engraveParm.crystalCubeSize || [50, 50, 50])];
    if (target > cube[0] * 0.95) cube = [Math.ceil(target / 0.9), Math.ceil(target / 0.9), Math.ceil(target / 0.9)];
    const scale = target / cube[0];
    const assetId = 'asset_' + crypto.randomUUID().replace(/-/g, '');
    const fileName = m.objFilename || `${safeName(m.name)}.obj`;
    const modelData = {
      ...JSON.parse(JSON.stringify(t.modelData)), name: m.name || 'model', position: [0, cube[1] / 2, 0], scale: [scale, scale, scale],
      source: 'file:///' + fileName, sourceAssetId: assetId, visible: true,
    };
    const crystal = {
      Assets: [{ data: bytesToB64(enc(m.obj)), fileName, id: assetId, originalPath: fileName, relativePath: fileName }],
      EngraveParm: { ...JSON.parse(JSON.stringify(t.engraveParm)), crystalCubeSize: cube, crystalType: 0, crystalMaterialName: 'crystal cube' },
      ModelData: [modelData],
    };
    const bytes = await writeBlocks([t.device, t.project, { crtystalEngrave: crystal }]);
    out.push({ name: (doc.models.length > 1 ? safeName(m.name) : safeName(doc.meta.title || m.name)) + '.fds', bytes, note: dims ? undefined : undefined });
  }
  return out;
}
