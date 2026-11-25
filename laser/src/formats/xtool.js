// xTool Studio projects: .xcs (v1, one JSON document) and .xs (v2, zip "xcs-workspace-v2").
// Both are converted through the same in-memory v1 project object.
import { loadZip, utf8, sha256Hex, uuid, rid, r6, bytesToB64, dataUrlToBytes, safeName } from '../lib/util.js';
import { parseSvgPath, transformPath, bbox, toD, pathFromEllipse, IDENTITY } from '../lib/path.js';
import { newDoc, objStats } from '../lib/model.js';

const OP_FROM = { VECTOR_CUTTING: 'cut', VECTOR_ENGRAVING: 'score', FILL_VECTOR_ENGRAVING: 'fill' };
const OP_TO = { cut: 'VECTOR_CUTTING', score: 'VECTOR_ENGRAVING', fill: 'FILL_VECTOR_ENGRAVING' };
const LAYER = { cut: '#fe0002', score: '#00befe', fill: '#ff7f56' };
const LAYER_ORDER = { '#ff7f56': 1, '#00befe': 2, '#fe0002': 3 };
const VECTOR_MIN = 4096; // .xs: dPath longer than this goes to the vector store
const CHUNK = 1000; // .xs: displays per chunk file
const MARGIN = 10;

// ---------------- reading ----------------

function displayToSubs(d) {
  const T = Array.isArray(d.transform) && d.transform.length === 6 ? d.transform : IDENTITY;
  switch (d.type) {
    case 'PATH':
      return d.dPath ? transformPath(parseSvgPath(d.dPath), T) : null;
    case 'LINE': {
      const e = d.endPoint || { x: 0, y: 0 };
      return transformPath([{ s: [0, 0], closed: false, segs: [{ t: 'L', p: [e.x, e.y] }] }], T);
    }
    case 'CIRCLE': case 'ELLIPSE': {
      const sx = d.scale?.x || 1, sy = d.scale?.y || 1;
      const w = d.width / sx, h = d.height / sy;
      return transformPath(pathFromEllipse(w / 2, h / 2, w / 2, h / 2), T);
    }
    default:
      return null;
  }
}

function modelFromDisplay(d) {
  const md = d.modelData;
  if (!md?.modelSerializedData?.data) return null;
  const ms = md.modelSerializedData;
  const obj = utf8(dataUrlToBytes(ms.data));
  let mtl = null;
  const textures = [];
  for (const r of md.resources || []) {
    const rd = r.data || r;
    if (!rd?.data) continue;
    const fname = r.filename || rd.filename || 'resource';
    if (/\.mtl$/i.test(fname)) mtl = { filename: fname, text: utf8(dataUrlToBytes(rd.data)) };
    else textures.push({ filename: fname, mime: rd.mimeType || '', bytes: dataUrlToBytes(rd.data) });
  }
  return {
    name: (ms.filename || 'model').replace(/\.[^.]+$/, ''), objFilename: ms.filename || 'model.obj', obj, mtl, textures,
    size: [d.sizeX, d.sizeY, d.sizeZ], center: [d.x, d.y, d.z], world: d.matrix || null,
  };
}

export function projectToDoc(proj, sourceFormat, name) {
  const doc = newDoc(sourceFormat);
  doc.meta = { sourceFormat, application: 'xTool Studio ' + (proj.version || ''), title: proj.name || name, machine: proj.extName || '' };
  const devMaps = new Map((proj.device?.data?.value || []).map(([cid, m]) => [cid, m]));
  proj.canvas.forEach((canvas, ci) => {
    const dmap = new Map(devMaps.get(canvas.id)?.displays?.value || []);
    const items = [], skipped = {};
    for (const d of canvas.displays) {
      const ptype = d.processingType || dmap.get(d.id)?.processingType;
      if (d.type === 'MODEL_3D') {
        const m = modelFromDisplay(d);
        if (m) doc.models.push(m); else skipped['3D model without embedded data'] = (skipped['3D model without embedded data'] || 0) + 1;
        continue;
      }
      const op = OP_FROM[ptype];
      const subs = op ? displayToSubs(d) : null;
      if (!subs) {
        const k = `${d.type}${ptype ? ' / ' + ptype : ''}`;
        skipped[k] = (skipped[k] || 0) + 1;
        continue;
      }
      if (d.visible === false) continue;
      items.push({ op, name: d.name || '', subs });
    }
    if (items.length) doc.plates.push({ name: canvas.title && canvas.title !== '{panel}1' ? canvas.title : `Canvas ${ci + 1}`, material: '', items });
    for (const [k, v] of Object.entries(skipped)) doc.warnings.push(`Skipped ${v} xTool object(s) of type ${k}.`);
  });
  return doc;
}

export async function xsToProject(zip) {
  const J = async (p) => { const f = zip.file(p); if (!f) throw new Error(`.xs is missing ${p}`); return JSON.parse(await f.async('string')); };
  const fmt = zip.file('.format') ? (await zip.file('.format').async('string')).trim() : '';
  if (fmt !== 'v2') throw new Error(`unsupported .xs format "${fmt}"`);
  const project = await J('project.json');
  const profiles = (await J('profiles.json')).profiles;
  const vectors = {};
  for (const f of Object.keys(zip.files).filter((n) => /^vectors\/svg\/data-\d+\.json$/.test(n))) Object.assign(vectors, (await J(f)).entries);
  const proj = { version: project.versionInfo?.appVersion, name: project.projectName, canvasId: project.activeCanvasId, canvas: [], device: null };
  const devs = [];
  for (const dev of project.modules.devices) devs.push(await J(`devices/device-${dev}.json`));
  const dev = devs[0];
  proj.extName = dev?.extName; proj.extId = dev?.extId;
  const devValue = [];
  for (const cid of project.modules.canvases) {
    const meta = await J(`canvases/${cid}.json`);
    const displays = [];
    for (const i of meta.chunkLayout?.chunkIndexes || [0]) displays.push(...(await J(`canvases/${cid}/displays-${i}.json`)).displays);
    for (const d of displays) {
      if (d.vectorRef) { d.dPath = vectors[d.vectorRef.vectorHash]; delete d.vectorRef; }
      if (d.modelDataRef && !d.modelData && zip.file(d.modelDataRef)) d.modelData = await J(d.modelDataRef);
    }
    const pr = dev?.processing?.[cid];
    const entries = [];
    if (pr) {
      const mode = pr.modes[pr.activeMode];
      for (const b of mode.bindings) {
        if (b.shadowOf) continue;
        const ptype = profiles[b.baseProfileId]?.processingType;
        for (const id of b.displayIds) entries.push([id, { processingType: ptype }]);
      }
    }
    devValue.push([cid, { mode: pr?.activeMode, displays: { dataType: 'Map', value: entries } }]);
    proj.canvas.push({ ...meta, displays });
  }
  proj.device = { ...(dev || {}), data: { dataType: 'Map', value: devValue } };
  return proj;
}

// ---------------- writing (v1 project objects) ----------------

const clone = (o) => JSON.parse(JSON.stringify(o));

function pathDisplay(subs, op, z) {
  const [x0, y0, x1, y1] = bbox(subs);
  const isFill = op === 'fill';
  const d = toD(subs, 'svg', 4);
  return {
    id: uuid(), name: null, type: 'PATH', x: x0, y: y0, angle: 0,
    scale: { x: 1, y: 1 }, skew: { x: 0, y: 0 }, pivot: { x: 0, y: 0 }, localSkew: { x: 0, y: 0 },
    offsetX: 0, offsetY: 0, lockRatio: true, isClosePath: subs.every((s) => s.closed), zOrder: z,
    groupTags: [], groupTag: uuid(), layerTag: LAYER[op], layerColor: LAYER[op], visible: true,
    originColor: '#000000', enableTransform: true, visibleState: true, lockState: false, resourceOrigin: '',
    customData: { tabBreaks: {}, startPoint: {} }, rootComponentId: '', minCanvasVersion: '0.0.0', alpha: 1,
    fill: { paintType: 'color', visible: isFill, color: isFill ? 5526616 : 0, alpha: 1 },
    stroke: { paintType: 'color', visible: !isFill, color: isFill ? 0 : 5526616, alpha: 1, width: 1, cap: 'butt', join: 'miter', miterLimit: 4, alignment: 0.5, dashOffset: 0 },
    effects: [], transform: [1, 0, 0, 1, 0, 0], width: x1 - x0, height: y1 - y0, isFill,
    lineColor: 16421416, fillColor: '#f9932b', points: [], fillRule: isFill ? 'evenodd' : 'nonzero',
    graphicX: 0, graphicY: 0, isCompoundPath: subs.length > 1, pathCornerData: null, dPath: d,
  };
}

function shell(t, name, canvasId, displays, devEntries, cover) {
  const now = Date.now();
  return {
    version: t.version, name, canvasId,
    canvas: [{ id: canvasId, title: '{panel}1', hidden: false, displays, extendInfo: clone(t.extendInfo), layerData: {}, groupData: {}, batchData: { entryType: 'BATCH_TEMPLATE', bindings: [] } }],
    projectTraceID: uuid(), projectID: uuid(), created: now, modify: now, ua: t.ua, cover: cover || '',
    minRequiredVersion: t.minRequiredVersion, appMinRequiredVersion: '', webMinRequiredVersion: '',
    extId: t.extId, extName: t.extName,
    device: { ...clone(t.device), data: { dataType: 'Map', value: [[canvasId, { mode: t.mode, displays: { dataType: 'Map', value: devEntries }, data: { [t.mode]: clone(t.modeData) } }]] } },
    meta: [{ date: now, version: t.version, ua: t.ua }],
  };
}

async function plateProject(doc, plate, t, cover) {
  const all = plate.items.flatMap((i) => i.subs);
  const [gx0, gy0] = bbox(all);
  const shift = [1, 0, 0, 1, MARGIN - gx0, MARGIN - gy0];
  const items = [...plate.items].sort((a, b) => LAYER_ORDER[LAYER[a.op]] - LAYER_ORDER[LAYER[b.op]]);
  const displays = [], dev = [], layers = {};
  items.forEach((it, i) => {
    const disp = pathDisplay(transformPath(it.subs, shift), it.op, i + 1);
    const ptype = OP_TO[it.op];
    disp.data = { [ptype]: clone(t.params[ptype]) };
    disp.processingType = ptype;
    displays.push(disp);
    const devData = { [ptype]: clone(t.params[ptype]) };
    if (!devData.VECTOR_ENGRAVING) devData.VECTOR_ENGRAVING = clone(t.params.VECTOR_ENGRAVING);
    dev.push([disp.id, { type: 'PATH', isFill: disp.isFill, processingType: ptype, processIgnore: false, data: devData }]);
    layers[disp.layerTag] = { name: disp.layerTag.toUpperCase(), order: LAYER_ORDER[disp.layerTag], visible: true };
  });
  const cid = uuid();
  const proj = shell(t, plate.name ? `${doc.meta.title || 'Converted'} - ${plate.name}` : doc.meta.title || 'Converted', cid, displays, dev, cover);
  proj.canvas[0].layerData = layers;
  return proj;
}

async function modelProject(doc, model, t, cover) {
  const st = objStats(model.obj);
  let world = model.world, size = model.size, center = model.center;
  if (!world || !size || !center) {
    const dims = [0, 1, 2].map((k) => st.max[k] - st.min[k]);
    const target = Math.max(...(model.size || [45, 45, 45]));
    const s = target / Math.max(...dims);
    const cons = t.extendInfo.consumable || {};
    const C = [(t.extendInfo.gridSize || 70) / 2, (cons.sizeZ || 80) / 2, (t.extendInfo.gridSize || 70) / 2]; // world x, height, bed y
    const lc = [0, 1, 2].map((k) => (st.max[k] + st.min[k]) / 2);
    world = [s, 0, 0, 0, 0, s, 0, 0, 0, 0, s, 0, C[0] - s * lc[0], C[1] - s * lc[1], C[2] - s * lc[2], 1];
    size = [s * dims[0], s * dims[2], s * dims[1]];
    center = [C[0], C[2], C[1]];
  }
  const toUrl = (bytes, mime) => `data:${mime || 'application/octet-stream'};base64,` + bytesToB64(bytes);
  const enc = new TextEncoder();
  const modelData = {
    modelSerializedData: { data: toUrl(enc.encode(model.obj)), filename: model.objFilename || `${safeName(model.name)}.obj`, mimeType: '' },
    resources: [
      ...model.textures.map((tx) => ({ data: { data: toUrl(tx.bytes, tx.mime || 'image/jpeg'), filename: tx.filename, mimeType: tx.mime || 'image/jpeg' }, filename: tx.filename })),
      ...(model.mtl ? [{ data: { data: toUrl(enc.encode(model.mtl.text)), filename: model.mtl.filename, mimeType: '' }, filename: model.mtl.filename }] : []),
    ],
  };
  const ref = 'resources/' + (await sha256Hex(JSON.stringify(modelData))) + '.json';
  const id = uuid();
  const disp = {
    ...clone(t.display), id, matrix: world, x: center[0], z: center[2], y: center[1], sizeX: size[0], sizeZ: size[2], sizeY: size[1],
    groupTag: uuid(), zOrder: 1, name: model.name || '', customData: {}, userData: { id },
    modelData, resourceId: uuid(), modelDataRef: ref, displayMode: 'simplified', hasSimplifiedModel: false,
  };
  // keep the template's key order, then settings
  const ordered = {};
  for (const k of Object.keys(t.display)) ordered[k] = disp[k];
  for (const k of ['customData', 'userData', 'modelData']) if (!(k in ordered)) ordered[k] = disp[k];
  ordered.data = clone(t.params);
  ordered.processingType = t.activeType;
  const keyOrder = ['id', 'type', 'matrix', 'x', 'z', 'y', 'sizeX', 'sizeZ', 'sizeY', 'processId', 'groupTag', 'visible', 'visibleState', 'lockState', 'enableTransform', 'zOrder', 'sourceId', 'lockRatio', 'name', 'customData', 'userData', 'alpha', 'modelData', 'resourceId', 'modelDataRef', 'isWhiteModel', 'isPointCloudModel', 'displayMode', 'hasSimplifiedModel', 'data', 'processingType'];
  const final = {};
  for (const k of keyOrder) if (k in ordered) final[k] = ordered[k];
  for (const k of Object.keys(ordered)) if (!(k in final)) final[k] = ordered[k];
  const cid = uuid();
  return shell(t, model.name || doc.meta.title || 'Model', cid, [final],
    [[id, { type: 'MODEL_3D', sourceId: '', processingType: t.activeType, processIgnore: false, data: clone(t.params) }]], cover);
}

// Returns [{ name, proj }] : one project per 2D plate (2D template) or per 3D model (3D template).
export async function docToProjects(doc, t, covers = {}) {
  const out = [];
  const base = safeName(doc.meta.title || 'converted');
  if (t.kind === '3d') {
    if (!doc.models.length) throw new Error(`${t.label} needs a 3D model; this file has none.`);
    for (let i = 0; i < doc.models.length; i++) {
      const m = doc.models[i];
      out.push({ name: doc.models.length > 1 ? `${base}_${safeName(m.name)}` : base, proj: await modelProject(doc, m, t, covers.model?.(i)) });
    }
  } else {
    if (!doc.plates.length) throw new Error(`${t.label} needs 2D paths; this file has none.`);
    for (let i = 0; i < doc.plates.length; i++) {
      const p = doc.plates[i];
      const nm = doc.plates.length > 1 ? `${base}_${safeName(p.name || 'plate_' + (i + 1))}${p.material ? '_' + safeName(p.material) : ''}` : base;
      out.push({ name: nm, proj: await plateProject(doc, p, t, covers.plate ? await covers.plate(p) : '') });
    }
  }
  return out;
}

// ---------------- .xs (v2) from a v1 project ----------------

export async function projectToXs(xcs, t) {
  const Z = globalThis.JSZip;
  const base = t.xs.baseProfiles;
  const files = {}, vectors = {}, resources = {}, profiles = {};
  const canvases = [], processing = {};
  const dev = xcs.device;
  const devMaps = new Map(dev.data.value);
  for (const canvas of xcs.canvas) {
    const cid = canvas.id;
    canvases.push(cid);
    const dmap = new Map(devMaps.get(cid)?.displays?.value || []);
    const outDisp = [];
    for (const d of canvas.displays) {
      const x = {};
      for (const [k, v] of Object.entries(d)) if (k !== 'data' && k !== 'processingType') x[k] = v;
      if (x.groupTags?.length) delete x.groupTag;
      if (typeof x.dPath === 'string' && x.dPath.length > VECTOR_MIN) {
        const h = await sha256Hex(x.dPath);
        vectors[h] = x.dPath;
        // keep key order: vectorRef replaces dPath in place
        const y = {};
        for (const [k, v] of Object.entries(x)) if (k === 'dPath') y.vectorRef = { vectorHash: h, bucketType: 'svg', originalField: 'dPath' }; else y[k] = v;
        Object.keys(x).forEach((k) => delete x[k]); Object.assign(x, y);
      }
      if (x.type === 'MODEL_3D' && x.modelData) {
        const txt = JSON.stringify(x.modelData);
        const ref = 'resources/' + (await sha256Hex(txt)) + '.json';
        resources[ref] = txt;
        x.modelDataRef = ref; x.modelData = null;
      }
      outDisp.push(r6(x));
    }
    const chunks = [];
    for (let i = 0; i < Math.max(1, outDisp.length); i += CHUNK) chunks.push(outDisp.slice(i, i + CHUNK));
    chunks.forEach((ch, i) => { files[`canvases/${cid}/displays-${i}.json`] = { canvasId: cid, chunkIndex: i, displays: ch }; });
    const meta = {};
    for (const k of ['id', 'title', 'hidden', 'layerData', 'groupData', 'batchData', 'extendInfo']) meta[k] = clone(canvas[k]);
    meta.chunkLayout = { displayCount: outDisp.length, chunkCount: chunks.length, chunkIndexes: chunks.map((_, i) => i) };
    files[`canvases/${cid}.json`] = meta;

    const modeName = devMaps.get(cid)?.mode || t.mode;
    const groups = new Map();
    for (const d of canvas.displays) {
      const entry = dmap.get(d.id) || { processingType: d.processingType, data: d.data || {} };
      const ptype = entry.processingType || d.processingType;
      if (!ptype) continue;
      const key = JSON.stringify([ptype, entry.data]);
      if (!groups.has(key)) groups.set(key, { ptype, data: entry.data, ids: [] });
      groups.get(key).ids.push(d.id);
    }
    const profFor = {}, patches = {}, bindings = [];
    const profile = (ptype) => {
      if (!profFor[ptype]) {
        const pid = rid('profile');
        profiles[pid] = { id: pid, processingType: ptype, values: clone(base[ptype] || {}) };
        profFor[ptype] = pid;
      }
      return profFor[ptype];
    };
    const patch = (ptype, blk) => {
      const pid = profile(ptype);
      const params = blk.parameter[Object.keys(blk.parameter)[0]];
      const ov = { ...params }; delete ov.materialId;
      const material = { materialType: blk.materialType, materialId: blk.materialId, paramSource: blk.paramSource };
      if ('paramSchemeId' in blk) material.paramSchemeId = blk.paramSchemeId;
      material.planType = blk.planType;
      const p = { id: rid('patch'), profileId: pid, source: 'material', material, overrides: ov };
      patches[p.id] = p;
      return [pid, p.id];
    };
    for (const { ptype, data, ids } of groups.values()) {
      const [bp, pa] = patch(ptype, data[ptype]);
      const b = { bindingId: rid('binding'), canvasId: cid, mode: modeName, baseProfileId: bp, patchIds: [pa], displayIds: ids };
      bindings.push(b);
      for (const [other, blk] of Object.entries(data)) {
        if (other === ptype || !base[other]) continue;
        const [op, oa] = patch(other, blk);
        bindings.push({ bindingId: rid('binding'), canvasId: cid, mode: modeName, baseProfileId: op, patchIds: [oa], displayIds: [], shadowOf: b.bindingId });
      }
    }
    const plane = devMaps.get(cid)?.data?.[modeName] || {};
    const refs = [...new Set(Object.values(patches).map((p) => p.profileId))];
    processing[cid] = { id: cid, activeMode: modeName, modes: { [modeName]: { ignoredDisplayIds: [], data: clone(plane), profileRefs: refs, patches, bindings } } };
  }
  files[`devices/device-${dev.id}.json`] = { id: dev.id, deviceCode: dev.deviceCode, extId: dev.extId, extName: dev.extName, power: dev.power, processing };
  files['profiles.json'] = { profiles };
  if (Object.keys(vectors).length) {
    files['vectors/svg/index.json'] = { bucketType: 'svg', version: '1.0', entryCount: Object.keys(vectors).length, entries: Object.fromEntries(Object.entries(vectors).map(([h, v]) => [h, { hash: h, size: v.length }])) };
    files['vectors/svg/data-0.json'] = { bucketType: 'svg', chunkIndex: 0, entries: vectors };
  }
  let coverBytes = null;
  if (typeof xcs.cover === 'string' && xcs.cover.startsWith('data:image/png;base64,')) {
    coverBytes = dataUrlToBytes(xcs.cover);
    files['resources/project-cover.png.meta.json'] = { ref: 'resources/project-cover.png', metadata: { kind: 'image', source: { type: 'workspace', value: 'project-cover.png' }, mimeType: 'image/png' } };
  }
  for (const ref of Object.keys(resources)) files[ref + '.meta.json'] = { ref, metadata: { kind: 'template', source: { type: 'workspace', value: ref.split('/').pop() } } };
  const now = Date.now();
  const pid = xcs.projectID || uuid();
  files['project.json'] = {
    __v2__: true, version: '2.0.0', schemaMeta: { schemaVersion: '2', format: 'directory' },
    projectId: pid, projectTraceID: pid, projectName: xcs.name || 'Untitled', activeCanvasId: xcs.canvasId, activeDeviceId: dev.id,
    versionInfo: { ...clone(t.xs.versionInfo), savedAt: now }, created: xcs.created || now, modify: now,
    modules: { canvases, devices: [dev.id] }, ...(coverBytes ? { cover: 'resources/project-cover.png' } : {}),
    customProjectData: { projectTraceID: pid },
  };
  files['meta/persistence-meta.json'] = clone(t.xs.persistenceMeta);

  const zip = new Z();
  const order = ['meta/persistence-meta.json', 'project.json', 'profiles.json',
    ...Object.keys(files).filter((k) => k.startsWith('canvases/') && k.split('/').length === 2).sort(),
    ...Object.keys(files).filter((k) => k.startsWith('canvases/') && k.split('/').length === 3).sort(),
    ...Object.keys(files).filter((k) => k.startsWith('devices/')).sort(),
    ...Object.keys(files).filter((k) => k.startsWith('resources/')).sort(),
    ...Object.keys(files).filter((k) => k.startsWith('vectors/')).sort()];
  const dirs = new Set();
  const addDirs = (name) => {
    const parts = name.split('/').slice(0, -1);
    for (let i = 1; i <= parts.length; i++) {
      const dn = parts.slice(0, i).join('/') + '/';
      if (!dirs.has(dn) && dn !== 'meta/') { dirs.add(dn); zip.folder(dn.slice(0, -1)); }
    }
  };
  for (const name of order) {
    addDirs(name);
    zip.file(name, JSON.stringify(files[name], null, 2));
    if (name === 'resources/project-cover.png.meta.json' && coverBytes) zip.file('resources/project-cover.png', coverBytes);
  }
  for (const [ref, txt] of Object.entries(resources)) { addDirs(ref); zip.file(ref, txt); }
  zip.file('.format', t.xs.format);
  return zip.generateAsync({ type: 'uint8array', compression: 'DEFLATE' });
}

// ---------------- format objects ----------------

export const xcs = {
  id: 'xcs', label: 'xTool Studio (.xcs)', extensions: ['.xcs'],
  async sniff(bytes, name) { return /\.xcs$/i.test(name) && bytes[0] === 0x7b; },
  async read(bytes, name) {
    const proj = JSON.parse(utf8(bytes));
    if (!Array.isArray(proj.canvas)) throw new Error('not an xTool .xcs project');
    return projectToDoc(proj, 'xcs', name.replace(/\.xcs$/i, ''));
  },
  async write(doc, opts) {
    const projs = await docToProjects(doc, opts.template, opts.covers);
    return projs.map(({ name, proj }) => ({ name: name + '.xcs', bytes: new TextEncoder().encode(JSON.stringify(proj)) }));
  },
};

export const xs = {
  id: 'xs', label: 'xTool Studio (.xs)', extensions: ['.xs'],
  async sniff(bytes, name) { return /\.xs$/i.test(name) && bytes[0] === 0x50 && bytes[1] === 0x4b; },
  async read(bytes, name) {
    const proj = await xsToProject(await loadZip(bytes));
    return projectToDoc(proj, 'xs', name.replace(/\.xs$/i, ''));
  },
  async write(doc, opts) {
    const projs = await docToProjects(doc, opts.template, opts.covers);
    const out = [];
    for (const { name, proj } of projs) out.push({ name: name + '.xs', bytes: await projectToXs(proj, opts.template) });
    return out;
  },
};
