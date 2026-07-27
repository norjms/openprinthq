<script>
  // OpenPrintHQ — manual motion & hardware control for one printer.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  // All actions move real hardware; the whole panel is disabled while printing.
  import { api } from '$lib/api';

  let { printerId, status, refresh } = $props();

  const printing = $derived(/run|print/i.test(String(status?.state || '')));
  const t = $derived(status?.temperatures || {});
  const dual = $derived(t.nozzle_2 !== undefined && t.nozzle_2 !== null);

  let step = $state(10);          // XY/Z step mm
  const steps = [0.1, 1, 10, 50];
  let extrudeAmt = $state(10);
  let ext = $state(0);            // active extruder 0=right,1=left
  let busy = $state(null);
  let msg = $state(null);

  const light = $derived(!!status?.chamber_light);
  const partFan = $derived(Math.round(((status?.cooling_fan_speed ?? 0) / 255) * 100) || pctFan(status?.cooling_fan_speed));
  function pctFan(v) { if (v == null) return 0; return v <= 100 ? Math.round(v) : Math.round((v / 255) * 100); }

  async function act(key, fn, okMsg) {
    busy = key; msg = null;
    try { await fn(); if (okMsg) msg = { kind: 'ok', text: okMsg }; if (refresh) await refresh(); }
    catch (e) { msg = { kind: 'err', text: e.message || 'command failed' }; }
    finally { busy = null; }
  }
  const jogXY = (x, y) => act('xy', () => api.xyJog(printerId, x * step, y * step));
  const jogZ = (dir) => act('z', () => api.bedJog(printerId, dir * step));
  const home = () => act('home', () => api.homeAxes(printerId, 'XYZ'), 'Homing.');
  const extrude = (dir) => act('e', () => api.extruderJog(printerId, dir * (Number(extrudeAmt) || 10)));
  async function pickExtruder(i) { ext = i; await act('ext', () => api.selectExtruder(printerId, i)); }
  const setFan = (fan, speed) => act('fan-' + fan, () => api.fanSpeed(printerId, fan, speed));
  const toggleLight = () => act('light', () => api.chamberLight(printerId, !light));

  const SPEEDS = [[1, 'Silent'], [2, 'Standard'], [3, 'Sport'], [4, 'Ludicrous']];
  const speedLevel = $derived(Number(status?.speed_level) || 2);
  const setSpeed = (m) => act('speed', () => api.printSpeed(printerId, m), 'Speed set.');
</script>

<div class="card card-pad control">
  <div class="chead">
    <h3>Move &amp; control</h3>
  </div>

  {#if printing}
    <p class="muted lock">Manual controls are disabled while a print is running.</p>
  {/if}

  <!-- Step size drives the XY/Z jog buttons, so it sits with the move controls. -->
  <div class="steprow">
    <span class="blabel">Step</span>
    <div class="steps">
      {#each steps as s}
        <button class="stp" class:on={step === s} onclick={() => (step = s)}>{s}</button>
      {/each}
      <span class="muted mm">mm</span>
    </div>
  </div>

  <div class="grid ctl">
    <!-- XY + home -->
    <div class="blk">
      <span class="blabel">Move XY</span>
      <div class="pad">
        <button class="jb yb" onclick={() => jogXY(0, 1)} disabled={printing || busy === 'xy'}>Y+</button>
        <button class="jb xl" onclick={() => jogXY(-1, 0)} disabled={printing || busy === 'xy'}>X−</button>
        <button class="jb hm" onclick={home} disabled={printing || busy === 'home'} title="Home all axes">⌂</button>
        <button class="jb xr" onclick={() => jogXY(1, 0)} disabled={printing || busy === 'xy'}>X+</button>
        <button class="jb yf" onclick={() => jogXY(0, -1)} disabled={printing || busy === 'xy'}>Y−</button>
      </div>
    </div>

    <!-- Z -->
    <div class="blk">
      <span class="blabel">Z / bed</span>
      <div class="zcol">
        <button class="jb" onclick={() => jogZ(1)} disabled={printing || busy === 'z'}>Z ↑</button>
        <button class="jb" onclick={() => jogZ(-1)} disabled={printing || busy === 'z'}>Z ↓</button>
      </div>
    </div>

    <!-- Extruder -->
    <div class="blk">
      <span class="blabel">Extruder</span>
      {#if dual}
        <div class="segl">
          <button class:on={ext === 1} onclick={() => pickExtruder(1)} disabled={printing || busy === 'ext'}>Left</button>
          <button class:on={ext === 0} onclick={() => pickExtruder(0)} disabled={printing || busy === 'ext'}>Right</button>
        </div>
      {/if}
      <div class="erow">
        <input class="input xs" type="number" min="1" max="100" bind:value={extrudeAmt} /><span class="muted">mm</span>
      </div>
      <div class="ebtns">
        <button class="jb" onclick={() => extrude(1)} disabled={printing || busy === 'e'}>Extrude ↓</button>
        <button class="jb" onclick={() => extrude(-1)} disabled={printing || busy === 'e'}>Retract ↑</button>
      </div>
    </div>

    <!-- Fans — start right of the extruder, each fan stacked vertically. -->
    <div class="blk fans">
      <span class="blabel">Fans</span>
      {#each [['part', 'Part'], ['aux', 'Aux'], ['chamber', 'Chamber']] as [f, label]}
        <div class="fanctl">
          <span class="fl">{label}</span>
          <button class="mini" onclick={() => setFan(f, 0)} disabled={busy === 'fan-' + f}>Off</button>
          <button class="mini" onclick={() => setFan(f, 50)} disabled={busy === 'fan-' + f}>50</button>
          <button class="mini" onclick={() => setFan(f, 100)} disabled={busy === 'fan-' + f}>100</button>
        </div>
      {/each}
    </div>
  </div>

  <div class="speed">
    <span class="blabel">Print speed</span>
    <div class="segl wide">
      {#each SPEEDS as [m, label]}
        <button class:on={speedLevel === m} onclick={() => setSpeed(m)} disabled={busy === 'speed'}>{label}</button>
      {/each}
    </div>
    <button class="btn btn-ghost btn-sm light {light ? 'on' : ''}" onclick={toggleLight} disabled={busy === 'light'}>
      {light ? '💡 Light on' : 'Light off'}
    </button>
  </div>
  {#if msg}<p class={msg.kind === 'ok' ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}
</div>

<style>
  .control { margin-top: 1.2rem; }
  .chead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.9rem; }
  .chead h3 { margin: 0; font-size: 1.05rem; }
  .steps { display: flex; align-items: center; gap: 0.3rem; }
  .stp { background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); color: var(--ophq-text-2); border-radius: var(--radius-sm); padding: 0.25rem 0.55rem; font-size: 0.8rem; font-family: var(--font-mono); cursor: pointer; }
  .stp.on { border-color: var(--ophq-primary); color: var(--ophq-text); background: var(--ophq-primary-dim); }
  .mm { font-size: 0.78rem; margin-left: 0.2rem; }
  .lock { font-size: 0.85rem; margin: 0 0 0.8rem; }
  .steprow { display: flex; align-items: center; gap: 0.7rem; margin-bottom: 1rem; }
  .ctl { grid-template-columns: auto auto auto auto; justify-content: start; gap: 1.6rem; align-items: start; }
  .fans { gap: 0.55rem; }
  .blk { display: flex; flex-direction: column; gap: 0.5rem; }
  .blabel { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); }
  .jb { background: var(--ophq-surface-2); border: 1px solid var(--ophq-border); color: var(--ophq-text); border-radius: var(--radius-sm); padding: 0.5rem 0.7rem; font-size: 0.85rem; cursor: pointer; font-family: var(--font-mono); }
  .jb:hover:not(:disabled) { border-color: var(--ophq-primary); }
  .jb:disabled { opacity: 0.4; cursor: default; }
  .pad { display: grid; grid-template-columns: repeat(3, 44px); grid-template-rows: repeat(3, 40px); gap: 0.35rem; }
  .pad .yb { grid-column: 2; grid-row: 1; }
  .pad .xl { grid-column: 1; grid-row: 2; }
  .pad .hm { grid-column: 2; grid-row: 2; font-size: 1.1rem; }
  .pad .xr { grid-column: 3; grid-row: 2; }
  .pad .yf { grid-column: 2; grid-row: 3; }
  .zcol { display: flex; flex-direction: column; gap: 0.35rem; }
  .segl { display: inline-flex; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); overflow: hidden; width: fit-content; }
  .segl button { background: var(--ophq-bg-2); border: none; color: var(--ophq-text-2); padding: 0.3rem 0.7rem; font-size: 0.8rem; cursor: pointer; }
  .segl button.on { background: var(--ophq-primary-dim); color: var(--ophq-primary-2); }
  .erow { display: flex; align-items: center; gap: 0.35rem; }
  .input.xs { max-width: 70px; padding: 0.35rem 0.5rem; font-size: 0.85rem; }
  .ebtns { display: flex; gap: 0.35rem; flex-wrap: wrap; }
  .fanctl { display: flex; align-items: center; gap: 0.3rem; }
  .fl { font-size: 0.8rem; color: var(--ophq-text-2); margin-right: 0.2rem; }
  .fans .fl { min-width: 4em; }
  .mini { background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); color: var(--ophq-text-2); border-radius: var(--radius-sm); padding: 0.2rem 0.45rem; font-size: 0.74rem; cursor: pointer; }
  .mini:hover:not(:disabled) { border-color: var(--ophq-primary); color: var(--ophq-text); }
  .light.on { color: var(--ophq-accent); border-color: rgba(255,176,32,0.35); }
  .speed { margin-top: 1.2rem; padding-top: 1rem; border-top: 1px solid var(--ophq-border-soft); display: flex; align-items: center; gap: 0.8rem; }
  .speed .light { margin-left: auto; }
  .segl.wide button { padding: 0.35rem 0.9rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.88rem; margin: 0.7rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0.7rem 0 0; }
  @media (max-width: 700px) { .ctl { grid-template-columns: 1fr; } }
</style>
