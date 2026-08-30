<script>
  // OpenPrintHQ — in-app G-code toolpath preview (#17).
  // Fetches a library file's raw g-code (engine `/library/files/{id}/gcode`,
  // which unwraps .gcode.3mf), parses G0/G1 moves into layers, and renders a
  // top-down canvas with a layer slider. Self-contained (no WebGL / deps).
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';

  // Source is either an engine file id, or a URL that returns raw g-code text.
  // The library needs the second: its files are not engine library rows, and
  // teaching this component a second API client would duplicate the parser,
  // which is the only part that is hard to get right.
  let { fileId = null, url = null, name = '' } = $props();

  // Guards — real prints can be tens of MB / millions of moves. Keep the UI
  // responsive and never hang the tab.
  const MAX_BYTES = 26 * 1024 * 1024;    // ~26 MB of g-code text
  const MAX_SEGMENTS = 900_000;

  let loading = $state(true);
  let error = $state(null);
  let truncated = $state(false);
  let layers = $state([]);               // [{ z, print:[x0,y0,x1,y1,...], travel:[...] }]
  let bbox = $state(null);               // { minX,maxX,minY,maxY }
  let cur = $state(0);                   // selected layer index
  let showTravel = $state(false);
  let canvasEl;

  function parse(text) {
    // Modal state
    let absPos = true, absE = true;
    let x = 0, y = 0, z = 0, e = 0;
    let curZ = null;
    let segCount = 0;
    const byZ = new Map();          // z(rounded) -> { z, print:[], travel:[] }
    const xs = [], ys = [];         // print-move endpoints, for a robust frame
    let cutoff = false;

    function layerFor(zv) {
      const key = Math.round(zv * 1000) / 1000;
      let l = byZ.get(key);
      if (!l) { l = { z: key, print: [], travel: [] }; byZ.set(key, l); }
      return l;
    }

    const lines = text.split('\n');
    for (let li = 0; li < lines.length; li++) {
      let line = lines[li];
      const semi = line.indexOf(';');
      if (semi !== -1) line = line.slice(0, semi);
      line = line.trim();
      if (!line) continue;
      // Only care about a small instruction set.
      const sp = line.indexOf(' ');
      const cmd = (sp === -1 ? line : line.slice(0, sp)).toUpperCase();

      if (cmd === 'G90') { absPos = true; continue; }
      if (cmd === 'G91') { absPos = false; continue; }
      if (cmd === 'M82') { absE = true; continue; }
      if (cmd === 'M83') { absE = false; continue; }
      if (cmd === 'G92') {
        // Reset coordinate origins (commonly E0).
        const m = /E(-?\d*\.?\d+)/i.exec(line); if (m) e = parseFloat(m[1]);
        const mz = /Z(-?\d*\.?\d+)/i.exec(line); if (mz) z = parseFloat(mz[1]);
        continue;
      }
      if (cmd !== 'G0' && cmd !== 'G1' && cmd !== 'G00' && cmd !== 'G01' &&
          cmd !== 'G2' && cmd !== 'G3' && cmd !== 'G02' && cmd !== 'G03') continue;

      const px = x, py = y;
      let nx = x, ny = y, nz = z, de = 0, hasMove = false;

      // Cheap token scan for X/Y/Z/E.
      const parts = line.split(' ');
      for (let pi = 1; pi < parts.length; pi++) {
        const t = parts[pi]; if (!t) continue;
        const axis = t[0].toUpperCase();
        const val = parseFloat(t.slice(1));
        if (Number.isNaN(val)) continue;
        if (axis === 'X') { nx = absPos ? val : x + val; hasMove = true; }
        else if (axis === 'Y') { ny = absPos ? val : y + val; hasMove = true; }
        else if (axis === 'Z') { nz = absPos ? val : z + val; }
        else if (axis === 'E') { de = absE ? (val - e) : val; e = absE ? val : e + val; }
      }

      if (nz !== z) { z = nz; curZ = z; }
      if (!hasMove) { x = nx; y = ny; continue; }

      const extruding = de > 0.0000001;
      const l = layerFor(curZ == null ? z : curZ);
      if (extruding) { l.print.push(px, py, nx, ny); xs.push(nx); ys.push(ny); }
      else l.travel.push(px, py, nx, ny);
      x = nx; y = ny;

      if (++segCount >= MAX_SEGMENTS) { cutoff = true; break; }
    }

    // Keep only layers that actually extrude — travel-only Z levels (probe at
    // Z-1, end-of-print park at Z100+) would otherwise pad the slider with
    // empty "layers".
    let out = [...byZ.values()].sort((a, b) => a.z - b.z).filter((l) => l.print.length > 0);
    if (out.length === 0) out = [...byZ.values()].sort((a, b) => a.z - b.z);

    // Frame to the object, not the whole bed: a Bambu prime line at Y≈0 and the
    // odd wipe move at the bed edge would blow out a raw min/max bbox and shrink
    // the part to a dot. Use a padded 0.5–99.5 percentile of print endpoints
    // (all segments are still DRAWN; this only sets the view transform).
    function pctBounds(arr) {
      if (arr.length === 0) return null;
      const a = [...arr].sort((m, n) => m - n);
      const at = (p) => a[Math.min(a.length - 1, Math.max(0, Math.round(p * (a.length - 1))))];
      return [at(0.005), at(0.995)];
    }
    const bx = pctBounds(xs), by = pctBounds(ys);
    let bb;
    if (bx && by) {
      let [minX, maxX] = bx, [minY, maxY] = by;
      const padX = Math.max(1, (maxX - minX) * 0.06), padY = Math.max(1, (maxY - minY) * 0.06);
      bb = { minX: minX - padX, maxX: maxX + padX, minY: minY - padY, maxY: maxY + padY };
    } else {
      bb = { minX: 0, minY: 0, maxX: 256, maxY: 256 };
    }
    return { layers: out, bbox: bb, truncated: cutoff };
  }

  async function load() {
    loading = true; error = null; truncated = false;
    try {
      let text;
      if (url) {
        const res = await fetch(url, { credentials: 'include' });
        if (!res.ok) throw Object.assign(new Error('could not load g-code'), { status: res.status });
        text = await res.text();
      } else {
        text = await api.fileGcode(fileId);
      }
      if (typeof text !== 'string') throw new Error('no g-code returned');
      if (text.length > MAX_BYTES) {
        // Parse a leading slice so the preview still renders something.
        truncated = true;
        const r = parse(text.slice(0, MAX_BYTES));
        layers = r.layers; bbox = r.bbox;
      } else {
        const r = parse(text);
        layers = r.layers; bbox = r.bbox; truncated = r.truncated;
      }
      if (layers.length === 0) throw new Error('no printable moves found');
      cur = Math.min(layers.length - 1, Math.floor(layers.length * 0.5));
      queueDraw();
    } catch (e) {
      error = e?.status === 404 ? 'This file has no sliced g-code to preview.' : (e.message || 'could not load g-code');
    } finally {
      loading = false;
    }
  }

  function draw() {
    const cv = canvasEl; if (!cv || !bbox || layers.length === 0) return;
    const dpr = Math.min(2, (typeof window !== 'undefined' && window.devicePixelRatio) || 1);
    const cssW = cv.clientWidth || 480, cssH = cv.clientHeight || 360;
    cv.width = Math.round(cssW * dpr); cv.height = Math.round(cssH * dpr);
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const pad = 16;
    const w = Math.max(1, bbox.maxX - bbox.minX), h = Math.max(1, bbox.maxY - bbox.minY);
    const s = Math.min((cssW - pad * 2) / w, (cssH - pad * 2) / h);
    const ox = (cssW - w * s) / 2, oy = (cssH - h * s) / 2;
    // Model Y up -> canvas Y down (flip).
    const tx = (vx) => ox + (vx - bbox.minX) * s;
    const ty = (vy) => cssH - (oy + (vy - bbox.minY) * s);

    function stroke(arr, style, width) {
      if (!arr.length) return;
      ctx.strokeStyle = style; ctx.lineWidth = width; ctx.beginPath();
      for (let i = 0; i < arr.length; i += 4) {
        ctx.moveTo(tx(arr[i]), ty(arr[i + 1]));
        ctx.lineTo(tx(arr[i + 2]), ty(arr[i + 3]));
      }
      ctx.stroke();
    }

    // Ghost of layers below the current one.
    for (let i = 0; i < cur; i++) stroke(layers[i].print, 'rgba(120,140,170,0.14)', 1);
    // Current layer.
    if (showTravel) stroke(layers[cur].travel, 'rgba(255,255,255,0.16)', 0.6);
    stroke(layers[cur].print, '#4ea1ff', 1.4);
  }

  let rafPending = false;
  function queueDraw() {
    if (rafPending || typeof requestAnimationFrame === 'undefined') { if (typeof requestAnimationFrame === 'undefined') draw(); return; }
    rafPending = true;
    requestAnimationFrame(() => { rafPending = false; draw(); });
  }

  // Redraw when the selected layer or travel toggle changes.
  $effect(() => { cur; showTravel; queueDraw(); });
  $effect(() => { fileId; url; load(); });

  const curZ = $derived(layers.length ? layers[Math.min(cur, layers.length - 1)].z : 0);
</script>

<div class="gv">
  {#if loading}
    <div class="gv-state muted">Parsing g-code…</div>
  {:else if error}
    <div class="gv-state"><p class="muted">{error}</p></div>
  {:else}
    <div class="canvas-wrap"><canvas bind:this={canvasEl}></canvas></div>
    <div class="ctrls">
      <input class="slider" type="range" min="0" max={layers.length - 1} bind:value={cur} aria-label="Layer" />
      <div class="row">
        <span class="mono lyr">Layer {cur + 1}/{layers.length} · Z {curZ.toFixed(2)} mm</span>
        <label class="tv"><input type="checkbox" bind:checked={showTravel} /><span>travel</span></label>
      </div>
      {#if truncated}<p class="trunc muted">Large file — preview truncated for performance.</p>{/if}
    </div>
  {/if}
</div>

<style>
  .gv { display: flex; flex-direction: column; gap: 0.6rem; }
  .gv-state { display: grid; place-items: center; min-height: 260px; text-align: center; }
  .canvas-wrap { width: 100%; aspect-ratio: 4/3; background: radial-gradient(circle at 50% 40%, #0f1622, #070b11); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); overflow: hidden; }
  canvas { width: 100%; height: 100%; display: block; }
  .slider { width: 100%; accent-color: var(--ophq-primary); }
  .row { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
  .lyr { font-size: 0.8rem; color: var(--ophq-text-2); }
  .tv { display: flex; align-items: center; gap: 0.35rem; font-size: 0.8rem; color: var(--ophq-text-2); }
  .tv input { width: auto; accent-color: var(--ophq-primary); }
  .trunc { font-size: 0.76rem; margin: 0; }
</style>
