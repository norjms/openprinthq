// Bambu Suite .lac (zip). Geometry lives in 2D/2dmodel.json as SVG-like path_data with two
// Suite quirks: cubic curves are written end point first ("C ex ey c1x c1y c2x c2y") and arcs use
// "AC ex ey cx cy" (clockwise on screen) / "AA ..." (counterclockwise) with the arc centre.
// Operations per object are in Metadata2D/project_settings.json.
import { loadZip, utf8, enc, b64ToBytes, safeName } from '../lib/util.js';
import { centerArcToCubics, transformPath, mul, IDENTITY, bbox, fmt } from '../lib/path.js';
import { newDoc } from '../lib/model.js';

const OP_FROM = { LaserLineCut: 'cut', LaserLineEngrave: 'score', LaserFillEngrave: 'fill' };
const OP_TO = { cut: 'LaserLineCut', score: 'LaserLineEngrave', fill: 'LaserFillEngrave' };
const COLOR = { cut: '244 67 54 255', score: '33 150 243 255', fill: '33 33 33 255' };

export const id = 'lac';
export const label = 'Bambu Suite (.lac)';
export const extensions = ['.lac'];

export async function sniff(bytes, name) {
  if (!/\.lac$/i.test(name)) return false;
  return bytes[0] === 0x50 && bytes[1] === 0x4b;
}

export function parseSuitePath(d, closed) {
  const tok = d.match(/[A-Za-z]+|-?\d*\.?\d+(?:[eE][-+]?\d+)?/g) || [];
  const subs = [];
  let cur = null, pos = null, start = null, i = 0;
  const n = () => parseFloat(tok[i++]);
  while (i < tok.length) {
    const t = tok[i++];
    if (t === 'M') {
      pos = start = [n(), n()];
      cur = { s: pos.slice(), segs: [], closed: false };
      subs.push(cur);
    } else if (t === 'L') {
      const p = [n(), n()];
      cur.segs.push({ t: 'L', p }); pos = p;
    } else if (t === 'C') {
      const p = [n(), n()], c1 = [n(), n()], c2 = [n(), n()];
      cur.segs.push({ t: 'C', c1, c2, p }); pos = p;
    } else if (t === 'Q') {
      const p = [n(), n()], q = [n(), n()];
      cur.segs.push({ t: 'C', c1: [pos[0] + (2 / 3) * (q[0] - pos[0]), pos[1] + (2 / 3) * (q[1] - pos[1])], c2: [p[0] + (2 / 3) * (q[0] - p[0]), p[1] + (2 / 3) * (q[1] - p[1])], p });
      pos = p;
    } else if (t === 'AC' || t === 'AA') {
      const e = [n(), n()], c = [n(), n()];
      for (const s of centerArcToCubics(pos[0], pos[1], e[0], e[1], c[0], c[1], t === 'AC')) cur.segs.push(s);
      pos = e;
    } else if (t === 'Z' || t === 'z') {
      if (cur) cur.closed = true;
      pos = start;
    } else {
      throw new Error(`unsupported Bambu path command "${t}"`);
    }
  }
  if (closed) for (const s of subs) s.closed = true;
  return subs;
}

const matStr = (s) => { const v = s.trim().split(/\s+/).map(Number); return [v[0], v[1], v[2], v[3], v[4], v[5]]; };

export async function read(bytes, name) {
  const zip = await loadZip(bytes);
  const json = async (p) => { const f = zip.file(p); if (!f) throw new Error(`not a Bambu Suite project (missing ${p})`); return JSON.parse(await f.async('string')); };
  const model = await json('2D/2dmodel.json');
  const settings = await json('Metadata2D/project_settings.json');
  const entry = zip.file('2D/entry.json') ? JSON.parse(await zip.file('2D/entry.json').async('string')) : {};
  const doc = newDoc('lac');
  doc.meta = {
    sourceFormat: 'lac', application: model.Application,
    title: entry.Title || name.replace(/\.lac$/i, ''), designer: entry.Designer || entry.ProfileUserName || '',
    license: entry.LicenseTerms || '', source: entry.MakerWorldDesignId ? `https://makerworld.com/models/${entry.MakerWorldDesignId}` : '',
    machine: settings.project_settings?.machine_settings_name || '',
  };
  for (let ci = 0; ci < model.canvas_list.length; ci++) {
    const canvas = model.canvas_list[ci];
    const objs = new Map(canvas.obj_list.map((o) => [o.obj_id, o]));
    const cs = settings.canvas_settings[ci] || settings.canvas_settings[0];
    const proc = new Map(cs.object_settings.map((o) => [o.obj_id, o.process_type]));
    const skipped = {};
    const leaves = function* (oid, m) {
      const o = objs.get(oid);
      if (!o) return;
      if (o.type === 'PathObject') yield [o, m];
      else if (o.components) for (const c of o.components) yield* leaves(c.obj_id, mul(m, matStr(c.transform)));
      else skipped[o.type] = (skipped[o.type] || 0) + 1;
    };
    for (const batch of cs.making_batch_list) {
      batch.making_plate_list.forEach((plate, pi) => {
        const items = [];
        for (const comp of plate.components) {
          for (const [o, m] of leaves(comp.obj_id, matStr(comp.transform))) {
            const op = OP_FROM[proc.get(o.obj_id)];
            if (!op) { const k = proc.get(o.obj_id) || 'no operation'; skipped[k] = (skipped[k] || 0) + 1; continue; }
            items.push({ op, name: o.name || '', subs: transformPath(parseSuitePath(o.path_data, o.is_closed), m) });
          }
        }
        if (items.length) doc.plates.push({ name: `Plate ${doc.plates.length + 1}`, material: batch.material_name || '', items });
      });
    }
    for (const [k, v] of Object.entries(skipped)) doc.warnings.push(`Skipped ${v} Bambu object(s) of type "${k}".`);
  }
  return doc;
}

// ---------- writer ----------

function suiteD(subs) {
  const P = (p) => fmt(p[0], 6) + ' ' + fmt(p[1], 6);
  const out = [];
  for (const s of subs) {
    out.push('M ' + P(s.s));
    for (const g of s.segs) out.push(g.t === 'L' ? 'L ' + P(g.p) : 'C ' + P(g.p) + ' ' + P(g.c1) + ' ' + P(g.c2));
    if (s.closed) out.push('Z');
  }
  return out.join(' ');
}

// opts: { template, thumbnails?: async (plate | null for the cover) => Uint8Array(png) | null }
export async function write(doc, opts) {
  const t = opts.template;
  const Z = globalThis.JSZip;
  const zip = new Z();
  let nextId = 16;
  const newId = () => (nextId += 2);
  const objList = [], canvasComps = [], objectSettings = [];
  const batches = new Map(); // material -> batch
  let canvasX = 0;
  const MARGIN = 10;
  let groupCount = 0;

  for (const plate of doc.plates) {
    const box = bbox(plate.items.flatMap((i) => i.subs));
    const shift = [1, 0, 0, 1, -box[0], -box[1]];
    const children = [];
    for (const it of plate.items) {
      const oid = newId();
      const subs = transformPath(it.subs, shift);
      objList.push({
        color: COLOR[it.op], flags: ['FreeAspectRatio'], is_closed: subs.every((s) => s.closed),
        name: it.name || `${it.op} ${oid}`, obj_id: oid, path_data: suiteD(subs), type: 'PathObject',
      });
      children.push({ obj_id: oid, transform: '1 0 0 1 0 0' });
      objectSettings.push(it.op === 'cut' ? { LaserLineCut: { cut_offset: 0.1 }, obj_id: oid, process_type: OP_TO.cut } : { obj_id: oid, process_type: OP_TO[it.op] });
    }
    const gid = newId();
    groupCount++;
    objList.push({ components: children, flags: ['FreeAspectRatio'], name: `Attach ${groupCount}`, obj_id: gid, type: 'AttachedGroup' });
    canvasComps.push({ obj_id: gid, transform: `1 0 0 1 ${fmt(canvasX, 4)} 0` });
    canvasX += box[2] - box[0] + 20;
    const matName = t.material.name;
    if (!batches.has(matName)) {
      batches.set(matName, {
        auto_arranged: false, batch_settings: t.batchSettings, material_id: t.material.id, material_name: matName,
        material_settings_name: t.material.settingsName, name: '', obj_id: 0, process_category: 1, making_plate_list: [],
      });
    }
    batches.get(matName)._docIdx = (batches.get(matName)._docIdx || []).concat(doc.plates.indexOf(plate));
    batches.get(matName).making_plate_list.push({
      components: [{ obj_id: gid, transform: `1 0 0 1 ${MARGIN} ${MARGIN}` }], name: plate.name || '', obj_id: 0, plate_mirror: false, plate_settings: { classVersion: 3 },
    });
  }
  const docIdx = new Map();
  for (const b of batches.values()) { b.obj_id = newId(); for (const p of b.making_plate_list) p.obj_id = newId(); docIdx.set(b, b._docIdx); delete b._docIdx; }

  const model2d = {
    Application: t.application, FileVersion: t.fileVersion,
    canvas_list: [{ components: canvasComps, index: 1, name: '', obj_list: objList, type_count: { AttachedGroup: groupCount } }],
  };
  const projectSettings = {
    canvas_settings: [{ index: 1, making_batch_list: [...batches.values()], object_settings: objectSettings }],
    project_settings: t.projectSettings,
  };
  const today = new Date().toISOString().slice(0, 10);
  const entry = {
    Application: t.application, CreationDate: today, Description: doc.meta.title ? `Converted from ${doc.meta.sourceFormat} with OpenPrintHQ Laser` : '',
    Designer: doc.meta.designer || '', FileVersion: t.fileVersion, LicenseTerms: doc.meta.license || '', ModificationDate: today, Title: doc.meta.title || 'Converted project',
  };
  zip.file('2D/2dmodel.json', JSON.stringify(model2d, null, 4));
  zip.file('2D/entry.json', JSON.stringify(entry, null, 4));
  zip.file('Metadata2D/project_settings.json', JSON.stringify(projectSettings, null, 4));
  for (const [fname, f] of Object.entries(t.files)) zip.file('Metadata2D/' + fname, f.b64 ? b64ToBytes(f.b64) : f.text);
  const rels = ['<?xml version="1.0" encoding="UTF-8"?>', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'];
  if (opts.thumbnails) {
    let bi = 0;
    for (const b of batches.values()) {
      bi++;
      for (let pi = 0; pi < b.making_plate_list.length; pi++) {
        const png = await opts.thumbnails(doc.plates[docIdx.get(b)[pi]]);
        if (png) zip.file(`Metadata2D/full_c1_b${bi}_p${pi + 1}.png`, png);
      }
    }
    const cover = await opts.thumbnails(null);
    if (cover) { zip.file('2D/design_thumbnail.png', cover); rels.push('  <Relationship Target="/2D/design_thumbnail.png" Id="rel-1" Type=""></Relationship>'); }
  }
  rels.push('</Relationships>');
  zip.file('[Content_Types].xml', t.contentTypes);
  zip.file('_rels/.rels', rels.join('\n') + '\n');
  const out = await zip.generateAsync({ type: 'uint8array', compression: 'DEFLATE' });
  return [{ name: safeName(doc.meta.title) + '.lac', bytes: out }];
}
