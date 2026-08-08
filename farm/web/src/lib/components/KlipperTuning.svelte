<script>
  // OpenPrintHQ — advanced Klipper tuning panel (#24).
  // Surfaces common Klipper/Voron tuning routines as one-click actions that
  // send the right g-code via the engine's raw-gcode route (POST /printers/
  // {id}/gcode) or the dedicated level/emergency-stop routes. Every action that
  // moves, heats, or writes config is confirm-gated. Shown only for Klipper
  // printers, and only while connected.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';

  let { printerId, connected = false, printing = false } = $props();

  let nozzleTarget = $state(240);
  let bedTarget = $state(60);
  let paValue = $state(0.040);
  let speedFactor = $state(100);
  let flowFactor = $state(100);

  let pending = $state(null);   // { label, run, danger }
  let busy = $state(false);
  let msg = $state(null);       // { kind:'ok'|'err', text }

  function ask(label, run, danger = false) { pending = { label, run, danger }; msg = null; }
  function cancel() { pending = null; }
  async function confirm() {
    if (!pending) return;
    busy = true;
    try { await pending.run(); msg = { kind: 'ok', text: pending.label + ' — sent.' }; }
    catch (e) { msg = { kind: 'err', text: e.message || 'command failed' }; }
    finally { busy = false; pending = null; }
  }

  const g = (cmd) => () => api.sendGcode(printerId, cmd);

  // Babystep Z is instantaneous & tiny — no confirm, fire directly.
  let zBusy = $state(false);
  async function babystep(delta) {
    zBusy = true; msg = null;
    try { await api.sendGcode(printerId, `SET_GCODE_OFFSET Z_ADJUST=${delta} MOVE=1`); msg = { kind: 'ok', text: `Z offset ${delta > 0 ? '+' : ''}${delta} mm` }; }
    catch (e) { msg = { kind: 'err', text: e.message || 'failed' }; }
    finally { zBusy = false; }
  }
</script>

<div class="card card-pad ktune">
  <div class="kh">
    <span class="eyebrow">Klipper tuning</span>
    {#if printing}<span class="chip warn">printing — tuning disabled</span>{:else if !connected}<span class="chip">offline</span>{/if}
  </div>
  <p class="muted intro">Common Klipper calibration routines. Each runs on the printer — watch it while it moves. Persist results with <b>Save config</b> (this restarts firmware).</p>

  <fieldset class="grp" disabled={!connected || printing || busy}>
    <span class="gl">Homing &amp; leveling</span>
    <div class="btns">
      <button class="btn btn-ghost btn-sm" onclick={() => ask('Home all axes (G28)', g('G28'))}>⌂ Home all</button>
      <button class="btn btn-ghost btn-sm" onclick={() => ask('Quad gantry level', () => api.klipperLevel(printerId))}>▱ QGL / level</button>
      <button class="btn btn-ghost btn-sm" onclick={() => ask('Bed mesh calibrate', g('BED_MESH_CALIBRATE'))}>▦ Bed mesh</button>
    </div>
  </fieldset>

  <fieldset class="grp" disabled={!connected || printing || busy}>
    <span class="gl">PID calibration</span>
    <div class="row">
      <label>Nozzle °C <input class="input sm" type="number" min="150" max="300" bind:value={nozzleTarget} /></label>
      <button class="btn btn-ghost btn-sm" onclick={() => ask(`PID calibrate hotend @ ${nozzleTarget}°C`, g(`PID_CALIBRATE HEATER=extruder TARGET=${nozzleTarget}`), true)}>Run</button>
    </div>
    <div class="row">
      <label>Bed °C <input class="input sm" type="number" min="40" max="120" bind:value={bedTarget} /></label>
      <button class="btn btn-ghost btn-sm" onclick={() => ask(`PID calibrate bed @ ${bedTarget}°C`, g(`PID_CALIBRATE HEATER=heater_bed TARGET=${bedTarget}`), true)}>Run</button>
    </div>
    <p class="muted tiny">Heats to target and cycles the heater. Follow with <b>Save config</b> to keep the result.</p>
  </fieldset>

  <!-- Speed and flow used to live on the old jog panel, which the new control
       cluster replaced. They are the two knobs worth reaching for mid-print, so
       they are NOT disabled while printing the way the calibration groups are. -->
  <fieldset class="grp" disabled={!connected || busy}>
    <span class="gl">Live factors</span>
    <div class="row">
      <label>Speed <input class="input sm" type="number" min="10" max="300" step="5" bind:value={speedFactor} /> %</label>
      <button class="btn btn-ghost btn-sm" onclick={() => api.sendGcode(printerId, `M220 S${speedFactor}`)}>Set</button>
      <label>Flow <input class="input sm" type="number" min="50" max="200" step="1" bind:value={flowFactor} /> %</label>
      <button class="btn btn-ghost btn-sm" onclick={() => api.sendGcode(printerId, `M221 S${flowFactor}`)}>Set</button>
    </div>
    <p class="muted tiny">Safe to change while a print is running — both take effect immediately.</p>
  </fieldset>

  <fieldset class="grp" disabled={!connected || printing || busy}>
    <span class="gl">Pressure advance</span>
    <div class="row">
      <label>Advance <input class="input sm" type="number" min="0" max="1" step="0.001" bind:value={paValue} /></label>
      <button class="btn btn-ghost btn-sm" onclick={() => ask(`Set pressure advance = ${paValue}`, g(`SET_PRESSURE_ADVANCE ADVANCE=${paValue}`))}>Apply</button>
    </div>
    <p class="muted tiny">Applies for this session. Put the winning value in your printer config to make it permanent.</p>
  </fieldset>

  <fieldset class="grp" disabled={!connected || printing || busy}>
    <span class="gl">Resonance / input shaper</span>
    <div class="btns">
      <button class="btn btn-ghost btn-sm" onclick={() => ask('Input shaper calibrate (needs accelerometer)', g('SHAPER_CALIBRATE'), true)}>Calibrate shaper</button>
    </div>
    <p class="muted tiny">Requires an ADXL345 accelerometer configured on the printer.</p>
  </fieldset>

  <fieldset class="grp" disabled={!connected || printing || busy}>
    <span class="gl">Live Z offset (babystep)</span>
    <div class="row zrow">
      <button class="btn btn-ghost btn-sm" onclick={() => babystep(-0.05)} disabled={zBusy}>−0.05</button>
      <button class="btn btn-ghost btn-sm" onclick={() => babystep(-0.01)} disabled={zBusy}>−0.01</button>
      <button class="btn btn-ghost btn-sm" onclick={() => babystep(0.01)} disabled={zBusy}>+0.01</button>
      <button class="btn btn-ghost btn-sm" onclick={() => babystep(0.05)} disabled={zBusy}>+0.05</button>
    </div>
    <div class="btns">
      <button class="btn btn-ghost btn-sm" onclick={() => ask('Persist Z offset to endstop + save', () => api.sendGcode(printerId, 'Z_OFFSET_APPLY_ENDSTOP'))}>Apply Z to endstop</button>
    </div>
  </fieldset>

  <fieldset class="grp" disabled={!connected || busy}>
    <span class="gl">Firmware</span>
    <div class="btns">
      <button class="btn btn-ghost btn-sm" onclick={() => ask('Firmware restart', g('FIRMWARE_RESTART'))}>↻ Firmware restart</button>
      <button class="btn btn-ghost btn-sm" onclick={() => ask('Save config (restarts firmware)', g('SAVE_CONFIG'), true)}>💾 Save config</button>
      <button class="btn btn-danger btn-sm" onclick={() => ask('EMERGENCY STOP (M112)', () => api.klipperEmergencyStop(printerId), true)}>⨯ Emergency stop</button>
    </div>
  </fieldset>

  {#if pending}
    <div class="confirm {pending.danger ? 'danger' : ''}">
      <span>Send <b>{pending.label}</b> to the printer?</span>
      <div class="cbtns">
        <button class="btn {pending.danger ? 'btn-danger' : 'btn-primary'} btn-sm" onclick={confirm} disabled={busy}>{busy ? 'Sending…' : 'Send'}</button>
        <button class="btn btn-ghost btn-sm" onclick={cancel} disabled={busy}>Cancel</button>
      </div>
    </div>
  {/if}
  {#if msg}<p class={msg.kind === 'ok' ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}
</div>

<style>
  .ktune { margin-top: 1.2rem; }
  .kh { display: flex; align-items: center; justify-content: space-between; }
  .intro { font-size: 0.86rem; margin: 0.3rem 0 1rem; max-width: 70ch; }
  .grp { border: none; border-top: 1px solid var(--ophq-border); padding: 0.8rem 0 0.2rem; margin: 0; display: block; }
  .grp[disabled] { opacity: 0.5; }
  .gl { display: block; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); margin-bottom: 0.5rem; }
  .btns { display: flex; flex-wrap: wrap; gap: 0.4rem; }
  .row { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.4rem; }
  .row label { display: flex; align-items: center; gap: 0.4rem; font-size: 0.84rem; color: var(--ophq-text-2); }
  .input.sm { max-width: 90px; padding: 0.3rem 0.4rem; }
  .zrow { gap: 0.4rem; }
  .tiny { font-size: 0.76rem; margin: 0.3rem 0 0; }
  .chip.warn { color: var(--ophq-warning, #e0a533); border-color: rgba(224,165,51,0.3); background: rgba(224,165,51,0.08); }
  .confirm { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-top: 1rem; padding: 0.7rem 0.9rem; border: 1px solid var(--ophq-primary); border-radius: var(--radius-sm); background: var(--ophq-primary-dim); font-size: 0.88rem; flex-wrap: wrap; }
  .confirm.danger { border-color: var(--ophq-danger); background: rgba(255,92,108,0.08); }
  .cbtns { display: flex; gap: 0.4rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.86rem; margin: 0.7rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.86rem; margin: 0.7rem 0 0; }
</style>
