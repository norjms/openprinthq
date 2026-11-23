// Round trips that need no vendor files: a small drawing and a cube, made here, through every format the app
// writes and back. test/run.js does the same with real projects saved by each vendor's software (npm run
// test:samples), which cannot be published.
import { loadTemplate } from './setup.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, convert, TARGETS } from '../src/lib/registry.js';
import { bbox } from '../src/lib/path.js';

const enc = (s) => new TextEncoder().encode(s);

// 120 x 80 mm: a cut rectangle, a scored circle, an engraved shape with curves and an arc, and a cut slot.
const DRAWING = `<svg xmlns="http://www.w3.org/2000/svg" width="120mm" height="80mm" viewBox="0 0 120 80">
  <rect x="5" y="5" width="110" height="70" rx="4" fill="none" stroke="#ff0000"/>
  <circle cx="30" cy="40" r="15" fill="none" stroke="#0000ff"/>
  <path d="M60 20 C75 10 95 15 100 30 A12 12 0 0 1 88 50 Q70 60 62 45 Z" fill="#000000"/>
  <path d="M20 65 h30 a3 3 0 0 0 0 -6 h-30 a3 3 0 0 0 0 6 z" fill="none" stroke="#ff0000"/>
</svg>`;

// A 20 mm cube with quad faces.
const CUBE = ['o cube',
  'v 0 0 0', 'v 20 0 0', 'v 20 20 0', 'v 0 20 0', 'v 0 0 20', 'v 20 0 20', 'v 20 20 20', 'v 0 20 20',
  'f 1 4 3 2', 'f 5 6 7 8', 'f 1 2 6 5', 'f 2 3 7 6', 'f 3 4 8 7', 'f 4 1 5 8', ''].join('\n');

// Per plate: each item's operation and bounding box relative to the plate's, sorted.
function fingerprint(doc) {
  return doc.plates.map((p) => {
    const all = bbox(p.items.flatMap((i) => i.subs));
    return p.items.map((i) => [i.op, ...bbox(i.subs).map((v, k) => v - all[k % 2])]).sort((a, b) => String(a).localeCompare(String(b)));
  });
}

function sameGeometry(a, b, label) {
  const fa = fingerprint(a), fb = fingerprint(b);
  assert.equal(fb.length, fa.length, `${label}: plate count`);
  fa.forEach((items, p) => {
    assert.equal(fb[p].length, items.length, `${label}: object count`);
    items.forEach((it, k) => {
      assert.equal(fb[p][k][0], it[0], `${label}: operation`);
      it.slice(1).forEach((v, j) => assert.ok(Math.abs(v - fb[p][k][j + 1]) < 0.02, `${label}: ${it[0]} moved: ${it} vs ${fb[p][k]}`));
    });
  });
}

const drawing = await readFile(enc(DRAWING), 'drawing.svg');
const cube = await readFile(enc(CUBE), 'cube.obj');

test('the drawing reads with its operations and size', () => {
  assert.equal(drawing.plates.length, 1);
  assert.deepEqual(drawing.plates[0].items.map((i) => i.op).sort(), ['cut', 'cut', 'fill', 'score']);
  const b = bbox(drawing.plates[0].items.flatMap((i) => i.subs));
  assert.ok(Math.abs(b[2] - b[0] - 110) < 0.01 && Math.abs(b[3] - b[1] - 70) < 0.01, `size ${b}`);
});

for (const t of TARGETS.filter((x) => x.kind === '2d')) {
  test(`2D round trip through ${t.id}`, async () => {
    const files = await convert(drawing, t.id, loadTemplate);
    assert.ok(files.length >= 1);
    const back = [];
    for (const f of files) back.push(await readFile(f.bytes, f.name));
    sameGeometry(drawing, { plates: back.flatMap((d) => d.plates) }, t.id);
  });
}

for (const t of TARGETS.filter((x) => x.kind === '3d')) {
  test(`3D round trip through ${t.id}`, async () => {
    const files = await convert(cube, t.id, loadTemplate);
    const back = await readFile(files[0].bytes, files[0].name);
    assert.equal(back.models.length, 1);
    if (t.id === 'stl') {
      assert.equal(back.models[0].obj.split('\n').filter((l) => l.startsWith('f ')).length, 12, 'six quads make 12 triangles');
      return;
    }
    assert.equal(back.models[0].obj, cube.models[0].obj, 'the mesh changed');
  });
}

test('2D and 3D conversions refuse the other kind with a clear message', async () => {
  await assert.rejects(convert(drawing, 'fds-t1', loadTemplate), /needs a 3D model/);
  await assert.rejects(convert(cube, 'svg', loadTemplate), /needs 2D paths/);
});
