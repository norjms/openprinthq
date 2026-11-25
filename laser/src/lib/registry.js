import * as lac from '../formats/lac.js';
import * as svg from '../formats/svg.js';
import * as fds from '../formats/fds.js';
import { xcs, xs } from '../formats/xtool.js';
import { obj, stl } from '../formats/mesh.js';

export const READERS = [lac, xcs, xs, fds, svg, obj, stl];

// kind: '2d' needs plates, '3d' needs models
export const TARGETS = [
  { id: 'xs-p2s', label: 'xTool Studio .xs', sub: 'P2S laser, 2D (Studio 1.7+)', kind: '2d', format: xs, template: 'xtool-p2s' },
  { id: 'xcs-p2s', label: 'xTool Studio .xcs', sub: 'P2S laser, 2D (all Studio versions)', kind: '2d', format: xcs, template: 'xtool-p2s' },
  { id: 'lac-r1', label: 'Bambu Suite .lac', sub: 'R1 55 W laser', kind: '2d', format: lac, template: 'bambu-r1-55w' },
  { id: 'lac-h2d', label: 'Bambu Suite .lac', sub: 'H2D 10 W laser', kind: '2d', format: lac, template: 'bambu-h2d-10w' },
  { id: 'svg', label: 'SVG', sub: 'LightBurn, Glowforge, Inkscape, any laser software', kind: '2d', format: svg },
  { id: 'xs-f2uuv', label: 'xTool Studio .xs', sub: 'F2 Ultra UV inner engraving, 3D', kind: '3d', format: xs, template: 'xtool-f2uuv-inner' },
  { id: 'xcs-f2uuv', label: 'xTool Studio .xcs', sub: 'F2 Ultra UV inner engraving, 3D', kind: '3d', format: xcs, template: 'xtool-f2uuv-inner' },
  { id: 'fds-t1', label: 'Falcon Design Space .fds', sub: 'Crystal engraving, 3D', kind: '3d', format: fds, template: 'falcon-t1' },
  { id: 'obj', label: 'OBJ', sub: '3D model with materials and textures', kind: '3d', format: obj },
  { id: 'stl', label: 'STL', sub: '3D model, millimetres', kind: '3d', format: stl },
];

export async function detect(bytes, name) {
  for (const r of READERS) if (await r.sniff(bytes, name)) return r;
  return null;
}

export async function readFile(bytes, name) {
  const r = await detect(bytes, name);
  if (!r) throw new Error(`Unrecognised file type: ${name}. Supported: ${READERS.flatMap((x) => x.extensions).join(', ')}`);
  const doc = await r.read(bytes, name);
  doc.meta.sourceLabel = r.label;
  return doc;
}

// loadTemplate: async (name) => template object
export async function convert(doc, targetId, loadTemplate, extra = {}) {
  const t = TARGETS.find((x) => x.id === targetId);
  if (!t) throw new Error('unknown target ' + targetId);
  const template = t.template ? await loadTemplate(t.template) : null;
  return t.format.write(doc, { template, ...extra });
}
