// Test harness. Needs the vendor sample files (not in the repo): set LC_SAMPLES to a folder with
//   lantern.lac  dougong.lac  demo.xcs  demo.xs  solar.xcs  solar.xs  angel.fds
// Writes outputs to LC_OUT (default /tmp/lc-out) for the pixel comparison script.
import { loadTemplate } from './setup.js';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { readFile, convert, TARGETS } from '../src/lib/registry.js';
import { projectToXs, xsToProject } from '../src/formats/xtool.js';
import { bbox } from '../src/lib/path.js';
import { objStats } from '../src/lib/model.js';

const S = process.env.LC_SAMPLES || '/tmp/lc-samples';
const OUT = process.env.LC_OUT || '/tmp/lc-out';
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
const load = (f) => new Uint8Array(fs.readFileSync(path.join(S, f)));
let fails = 0, passes = 0;
const test = async (name, fn) => {
  try { await fn(); passes++; console.log('PASS', name); }
  catch (e) { fails++; console.log('FAIL', name, '\n   ', e.stack.split('\n').slice(0, 3).join('\n    ')); }
};
const save = (dir, files) => { fs.mkdirSync(path.join(OUT, dir), { recursive: true }); for (const f of files) fs.writeFileSync(path.join(OUT, dir, f.name), f.bytes); };

// geometry fingerprint: per plate, sorted list of [op, rounded bbox] relative to plate origin
function fingerprint(doc) {
  return doc.plates.map((p) => {
    const all = bbox(p.items.flatMap((i) => i.subs));
    return p.items.map((i) => { const b = bbox(i.subs); return [i.op, ...b.map((v, k) => Math.round((v - all[k % 2]) * 100) / 100)].join(' '); }).sort();
  });
}
function sameGeometry(a, b, label) {
  const fa = fingerprint(a), fb = fingerprint(b);
  assert.equal(fa.length, fb.length, `${label}: plate count`);
  for (let i = 0; i < fa.length; i++) {
    assert.equal(fa[i].length, fb[i].length, `${label}: plate ${i + 1} object count`);
    let bad = 0;
    for (let k = 0; k < fa[i].length; k++) {
      const x = fa[i][k].split(' '), y = fb[i][k].split(' ');
      if (x[0] !== y[0] || x.slice(1).some((v, j) => Math.abs(+v - +y[j + 1]) > 0.02)) bad++;
    }
    assert.equal(bad, 0, `${label}: plate ${i + 1} has ${bad} objects that moved or changed operation`);
  }
}

const docs = {};
await test('read all samples', async () => {
  for (const f of ['lantern.lac', 'dougong.lac', 'demo.xcs', 'demo.xs', 'solar.xcs', 'solar.xs', 'angel.fds']) docs[f] = await readFile(load(f), f);
  assert.deepEqual(docs['dougong.lac'].plates.map((p) => p.items.length), [553, 984, 107, 256]);
  assert.deepEqual(docs['lantern.lac'].plates.map((p) => p.items.length), [62, 28, 2, 6, 48, 16, 78, 18]);
  assert.equal(docs['demo.xcs'].plates[0].items.length, 13);
  assert.equal(docs['solar.xcs'].models.length, 1);
  assert.equal(docs['solar.xs'].models.length, 1);
  assert.equal(docs['angel.fds'].models.length, 1);
});

await test('.xcs and .xs of the same Studio project read identically', async () => {
  sameGeometry(docs['demo.xcs'], docs['demo.xs'], 'demo');
  const a = docs['solar.xcs'].models[0], b = docs['solar.xs'].models[0];
  assert.equal(a.obj, b.obj);
  assert.equal(a.textures.length, 10);
  assert.equal(b.textures.length, 10);
  assert.ok(a.mtl && b.mtl);
});

await test('SVG export (for pixel comparison against the verified Python renders)', async () => {
  save('svg/lantern', await convert(docs['lantern.lac'], 'svg', loadTemplate));
  save('svg/dougong', await convert(docs['dougong.lac'], 'svg', loadTemplate));
});

for (const src of ['lantern.lac', 'dougong.lac', 'demo.xcs']) {
  for (const tgt of ['svg', 'xcs-p2s', 'xs-p2s', 'lac-r1', 'lac-h2d']) {
    await test(`round trip ${src} -> ${tgt} -> read`, async () => {
      const files = await convert(docs[src], tgt, loadTemplate);
      save(`${tgt}/${src}`, files);
      const back = [];
      for (const f of files) back.push(await readFile(f.bytes, f.name));
      // merge plates of all output files
      const merged = { plates: back.flatMap((d) => d.plates) };
      sameGeometry(docs[src], merged, `${src}->${tgt}`);
    });
  }
}

await test('JS .xcs -> .xs matches what xTool Studio wrote (demo)', async () => {
  const proj = JSON.parse(fs.readFileSync(path.join(S, 'demo.xcs'), 'utf8'));
  fs.mkdirSync(path.join(OUT, 'xs-equiv'), { recursive: true });
  fs.writeFileSync(path.join(OUT, 'xs-equiv/demo_from_js.xs'), await projectToXs(proj, await loadTemplate('xtool-p2s')));
});
await test('JS .xcs -> .xs matches what xTool Studio wrote (Solar System, 3D)', async () => {
  const proj = JSON.parse(fs.readFileSync(path.join(S, 'solar.xcs'), 'utf8'));
  fs.writeFileSync(path.join(OUT, 'xs-equiv/solar_from_js.xs'), await projectToXs(proj, await loadTemplate('xtool-f2uuv-inner')));
});

for (const src of ['solar.xcs', 'angel.fds']) {
  for (const tgt of ['xs-f2uuv', 'xcs-f2uuv', 'fds-t1', 'obj', 'stl']) {
    await test(`3D round trip ${src} -> ${tgt} -> read`, async () => {
      const files = await convert(docs[src], tgt, loadTemplate);
      save(`${tgt}/${src}`, files);
      if (tgt === 'obj' && files[0].name.endsWith('.zip')) return; // zipped OBJ with textures
      const back = await readFile(files[0].bytes, files[0].name);
      assert.equal(back.models.length, 1);
      const a = objStats(docs[src].models[0].obj), b = objStats(back.models[0].obj);
      if (tgt === 'stl') {
        const tri = docs[src].models[0].obj.split('\n').filter((l) => l.startsWith('f ')).reduce((n, l) => n + l.trim().split(/\s+/).length - 3, 0);
        assert.equal(b.faces, tri, 'triangle count');
        return;
      }
      assert.equal(back.models[0].obj, docs[src].models[0].obj, 'OBJ text changed');
      if (tgt !== 'fds-t1' && docs[src].models[0].size && back.models[0].size) {
        const sa = docs[src].models[0].size, sb = back.models[0].size;
        for (let k = 0; k < 3; k++) assert.ok(Math.abs(sa[k] - sb[k]) < 0.05, `size changed ${sa} -> ${sb}`);
      }
    });
  }
}

await test('3D conversions refuse 2D-only input with a clear message', async () => {
  await assert.rejects(convert(docs['lantern.lac'], 'fds-t1', loadTemplate), /needs a 3D model/);
  await assert.rejects(convert(docs['angel.fds'], 'svg', loadTemplate), /needs 2D paths/);
});

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
