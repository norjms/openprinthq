<script>
  // Circular XY jog pad: four directions × two step rings, home in the middle.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // Drawn as SVG wedges rather than CSS clip-paths so the hit areas are exactly
  // the shapes you can see — a clipped <div> still swallows clicks in the
  // corners it only *appears* to have removed.
  //
  // The engine's xy-jog takes a free-form millimetre delta, so the two rings are
  // simply two distances; nothing here is pinned to a fixed step enum.

  let {
    steps = [1, 10],        // inner ring, outer ring — in mm
    disabled = false,
    busy = null,            // label of the move in flight, or null
    onjog = () => {},       // (dx, dy, mm)
    onhome = () => {},
    homeLabel = 'Home all axes'
  } = $props();

  const CX = 100, CY = 100;
  const R_HOME = 30, R_MID = 62, R_OUT = 98;

  // SVG y grows downward, so "up the bed" (Y+) is the wedge centred on -90°.
  const DIRS = [
    { key: 'Y',  label: 'Y',  axis: 'y', sign:  1, mid: -90, tx: 0,  ty: -1 },
    { key: 'X',  label: 'X',  axis: 'x', sign:  1, mid:   0, tx: 1,  ty:  0 },
    { key: '-Y', label: '-Y', axis: 'y', sign: -1, mid:  90, tx: 0,  ty:  1 },
    { key: '-X', label: '-X', axis: 'x', sign: -1, mid: 180, tx: -1, ty:  0 }
  ];

  const rad = (deg) => (deg * Math.PI) / 180;
  const pt = (r, deg) => [CX + r * Math.cos(rad(deg)), CY + r * Math.sin(rad(deg))];

  // One wedge of an annulus, from a1 to a2 degrees, between radii ra and rb.
  function wedge(a1, a2, ra, rb) {
    const [x1, y1] = pt(ra, a1), [x2, y2] = pt(ra, a2);
    const [x3, y3] = pt(rb, a2), [x4, y4] = pt(rb, a1);
    return `M${x1} ${y1} A${ra} ${ra} 0 0 1 ${x2} ${y2} L${x3} ${y3} A${rb} ${rb} 0 0 0 ${x4} ${y4} Z`;
  }

  // [{ d, dx, dy, mm, label, cx, cy }] — outer ring first so the inner ring
  // paints over its edge rather than the other way round.
  const cells = $derived.by(() => {
    const out = [];
    for (const [ring, radii] of [[1, [R_MID, R_OUT]], [0, [R_HOME, R_MID]]]) {
      const mm = steps[ring] ?? steps[steps.length - 1];
      for (const d of DIRS) {
        const [ra, rb] = radii;
        // Every cell states both which way it goes and how far, so the wheel
        // needs no legend. The outer ring carries the axis letter as well,
        // since that's where the eye lands first.
        out.push({
          key: `${d.key}-${mm}`,
          d: wedge(d.mid - 45, d.mid + 45, ra, rb),
          dx: d.axis === 'x' ? d.sign * mm : 0,
          dy: d.axis === 'y' ? d.sign * mm : 0,
          mm,
          dir: d.label,
          axisText: ring === 1 ? d.label : '',
          stepText: `${d.sign > 0 ? '+' : '−'}${mm}`,
          // Left/right labels stack vertically at one radius; up/down stack
          // radially. Putting both on the same radial line horizontally ran
          // "-X" straight into "−10".
          ...(d.tx !== 0
            ? { ax: CX + d.tx * 80, ay: CY - 9, sx: CX + d.tx * (ring === 1 ? 80 : 46), sy: CY + (ring === 1 ? 9 : 0) }
            : { ax: CX, ay: CY + d.ty * 74, sx: CX, sy: CY + d.ty * (ring === 1 ? 91 : 46) })
        });
      }
    }
    return out;
  });

  function fire(c) {
    if (disabled || busy) return;
    onjog(c.dx, c.dy, c.mm);
  }
  function keyFire(e, c) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fire(c); }
  }
</script>

<div class="wheel" class:off={disabled}>
  <svg viewBox="0 0 200 200" role="group" aria-label="Move the print head">
    {#each cells as c (c.key)}
      <g class="cell" class:busy={busy === `${c.dir}${c.mm}`}
         role="button" tabindex={disabled ? -1 : 0}
         aria-label={`Move ${c.dir} by ${c.mm} millimetres`}
         aria-disabled={disabled}
         onclick={() => fire(c)} onkeydown={(e) => keyFire(e, c)}>
        <path class="seg" d={c.d} />
        {#if c.axisText}<text x={c.ax} y={c.ay} class="lbl axis">{c.axisText}</text>{/if}
        <text x={c.sx} y={c.sy} class="lbl step">{c.stepText}</text>
      </g>
    {/each}
    <circle class="hub" cx={CX} cy={CY} r={R_HOME - 3} />
  </svg>

  <!-- Home is a real <button>: it is the one control here you might reach for
       in a hurry, and it homes every axis, not just XY. -->
  <button class="home" type="button" onclick={onhome} disabled={disabled || !!busy}
          data-tip={homeLabel} aria-label={homeLabel}>
    {#if busy === 'home'}
      <span class="spin" aria-hidden="true"></span>
    {:else}
      <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 11l9-8 9 8" /><path d="M5 10v10h14V10" /><path d="M10 20v-6h4v6" />
      </svg>
    {/if}
  </button>
</div>

<style>
  .wheel { position: relative; width: 100%; max-width: 260px; aspect-ratio: 1; margin: 0 auto; }
  .wheel svg { width: 100%; height: 100%; display: block; overflow: visible; }

  /* Mixed against the text colour rather than set to a fixed surface token, so
     the wedges stay visible on a light card as well as a dark one. */
  .seg {
    fill: color-mix(in srgb, var(--ophq-text) 8%, var(--ophq-bg-2));
    stroke: var(--ophq-surface);
    stroke-width: 2;
    transition: fill 0.12s ease;
  }
  .cell { cursor: pointer; }
  .cell:hover .seg { fill: color-mix(in srgb, var(--ophq-primary) 18%, var(--ophq-bg-2)); }
  .cell:active .seg { fill: var(--ophq-primary-dim); }
  .cell.busy .seg { fill: var(--ophq-primary-dim); }
  .cell:focus-visible { outline: none; }
  .cell:focus-visible .seg { stroke: var(--ophq-primary); stroke-width: 3; }

  .lbl {
    fill: var(--ophq-text-2);
    font-size: 13px; font-weight: 600;
    text-anchor: middle; dominant-baseline: central;
    pointer-events: none; user-select: none;
  }
  .lbl.axis { font-size: 14px; font-weight: 700; fill: var(--ophq-text); }
  .lbl.step { font-size: 10.5px; font-weight: 600; fill: var(--ophq-muted); }
  .cell:hover .lbl { fill: var(--ophq-text); }
  .cell:hover .lbl.step { fill: var(--ophq-text-2); }

  .hub { fill: var(--ophq-surface); stroke: var(--ophq-border); stroke-width: 2; pointer-events: none; }
  .lbl.step { fill: var(--ophq-text-2); }

  .home {
    position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
    width: 27%; height: 27%; border-radius: 50%;
    display: grid; place-items: center;
    background: transparent; border: 0; cursor: pointer;
    color: var(--ophq-primary-2);
  }
  .home svg { width: 46%; height: 46%; }
  .home:hover:not(:disabled) { color: var(--ophq-text); }
  .home:disabled { opacity: 0.45; cursor: default; }
  .home:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: 2px; }

  .spin {
    width: 40%; height: 40%; border-radius: 50%;
    border: 2px solid color-mix(in srgb, var(--ophq-primary) 35%, transparent);
    border-top-color: var(--ophq-primary);
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .wheel.off { opacity: 0.45; }
  .wheel.off .cell { cursor: default; }

  @media (prefers-reduced-motion: reduce) {
    .spin { animation: none; }
  }
</style>
