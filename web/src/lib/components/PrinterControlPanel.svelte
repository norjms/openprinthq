<script>
  // Control cluster: temperatures and machine toggles on the left, the XY jog
  // wheel and Z steps in the middle, extruder on the right.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // One layout for every printer type. Anything the machine can't do simply
  // isn't rendered — no greyed-out chrome, no vendor-specific variants of the
  // whole panel. What survives on a Klipper machine is temps, the wheel, Z,
  // extrude/retract and (if its profile defines a light macro) the lamp.
  import { api } from '$lib/api';
  import PrinterJogWheel from '$lib/components/PrinterJogWheel.svelte';
  import PrintOptionsModal from '$lib/components/PrintOptionsModal.svelte';
  import CalibrationModal from '$lib/components/CalibrationModal.svelte';
  import ModalShell from '$lib/components/ModalShell.svelte';
  import MaintenancePanel from '$lib/components/MaintenancePanel.svelte';

  let {
    printerId, status = null, meta = null, printing = false,
    isBambu = true, isKlipper = false, chamberHeater = false,
    homedAxes = null, online = true, refresh = () => {}
  } = $props();

  const st = $derived(status || {});
  const T = $derived(st.temperatures || {});
  // Reachability comes from the page already smoothed over MQTT blips. Gating on
  // the raw flag made every control on this panel flicker disabled and back.
  // A command sent during a real disconnect still fails cleanly with the
  // engine's 400, which is the guard that actually matters.
  const connected = $derived(!!online);
  const num = (v) => (v != null && v !== '' ? Number(v) : null);

  // ---- capability gates (derived, never probed) ---------------------------
  const dualNozzle = $derived((st.nozzles?.length || 0) > 1 || T.nozzle_2 != null);
  const canChamber = $derived(isBambu && (!!st.supports_chamber_heater || chamberHeater));
  const canFans = $derived(isBambu);
  const canAirduct = $derived(isBambu && st.airduct_mode != null);
  const canSelectExtruder = $derived(isBambu && dualNozzle);
  const canPrintOptions = $derived(isBambu);
  const canSpeedProfile = $derived(isBambu);

  // 1=silent 2=standard 3=sport 4=ludicrous, per the engine.
  const SPEEDS = [[1, 'Silent'], [2, 'Standard'], [3, 'Sport'], [4, 'Ludicrous']];
  async function setSpeed(mode) {
    acting = 'speed'; err = null;
    try { await api.printSpeed(printerId, mode); await refresh(); }
    catch (e) { err = e?.message || 'Could not change the print speed.'; }
    finally { acting = null; }
  }

  // ---- temperatures -------------------------------------------------------
  // Index 0 is the RIGHT nozzle and index 1 the LEFT, verified on real H2C/H2D
  // hardware: the engine sends M104 T{index} and the readback maps extruder 0 to
  // nozzle_2. Getting this backwards means every card drives the other nozzle.
  const tempRows = $derived.by(() => {
    const rows = [];
    if (dualNozzle) {
      rows.push({ key: 'nzL', kind: 'nozzle', nozzle: 1, badge: 'L', label: 'Left nozzle',
                  cur: num(T.nozzle), target: num(T.nozzle_target) || 0, max: 320 });
      rows.push({ key: 'nzR', kind: 'nozzle', nozzle: 0, badge: 'R', label: 'Right nozzle',
                  cur: num(T.nozzle_2), target: num(T.nozzle_2_target) || 0, max: 320 });
    } else {
      rows.push({ key: 'nz', kind: 'nozzle', nozzle: 0, badge: '', label: 'Nozzle',
                  cur: num(T.nozzle), target: num(T.nozzle_target) || 0, max: 320 });
    }
    rows.push({ key: 'bed', kind: 'bed', nozzle: 0, badge: '', label: 'Bed',
                cur: num(T.bed), target: num(T.bed_target) || 0, max: 140 });
    if (canChamber) {
      rows.push({ key: 'ch', kind: 'chamber', nozzle: 0, badge: '', label: 'Chamber',
                  cur: num(T.chamber), target: num(T.chamber_target) || 0, max: 60 });
    } else if (num(T.chamber) != null) {
      // Reports a chamber reading but can't heat it — show the number, no control.
      rows.push({ key: 'ch', kind: 'chamber', nozzle: 0, badge: '', label: 'Chamber',
                  cur: num(T.chamber), target: 0, max: 0, readonly: true });
    }
    return rows;
  });

  let editing = $state(null);      // row key being retargeted
  let draft = $state('');
  let acting = $state(null);
  let err = $state(null);

  function beginEdit(row) {
    if (row.readonly || !connected) return;
    editing = row.key;
    draft = row.target ? String(row.target) : '';
  }
  async function commitTemp(row) {
    const v = draft === '' ? null : Number(draft);
    editing = null;
    if (v == null || Number.isNaN(v)) return;
    await setTemp(row, Math.max(0, Math.min(row.max, v)));
  }
  async function setTemp(row, value) {
    acting = 'temp-' + row.key; err = null;
    try {
      await api.setTemp(printerId, row.kind, value, row.nozzle);
      await refresh();
    } catch (e) { err = e?.message || 'Could not set the temperature.'; }
    finally { acting = null; }
  }

  // ---- fans ---------------------------------------------------------------
  const fans = $derived([
    { key: 'part', label: 'Part', v: st.cooling_fan_speed },
    { key: 'aux', label: 'Aux', v: st.big_fan1_speed },
    { key: 'chamber', label: 'Chamber', v: st.big_fan2_speed }
  ].filter((f) => canFans && f.v != null));

  // Optimistic positions so the slider doesn't snap back between polls.
  let fanUI = $state({});
  const fanVal = (f) => (fanUI[f.key] != null ? fanUI[f.key] : Math.round(Number(f.v) || 0));
  async function commitFan(f, pct) {
    fanUI = { ...fanUI, [f.key]: pct };
    acting = 'fan-' + f.key; err = null;
    try { await api.fanSpeed(printerId, f.key, pct); await refresh(); }
    catch (e) { err = e?.message || 'Could not set the fan.'; }
    finally { acting = null; }
  }

  // ---- lamp / airduct -----------------------------------------------------
  async function toggleLamp() {
    acting = 'lamp'; err = null;
    try { await api.chamberLight(printerId, !st.chamber_light); await refresh(); }
    catch (e) {
      // Klipper only has a light if its machine profile defines the macro.
      err = isKlipper
        ? 'This printer has no light macro in its Klipper profile.'
        : (e?.message || 'Could not toggle the light.');
    } finally { acting = null; }
  }
  async function setAirduct(mode) {
    acting = 'airduct'; err = null;
    try { await api.airductMode(printerId, mode); await refresh(); }
    catch (e) { err = e?.message || 'Could not change the air mode.'; }
    finally { acting = null; }
  }

  // ---- motion -------------------------------------------------------------
  // Klipper refuses jogs until it has homed, and the raw error is cryptic, so
  // the prompt is surfaced before the user hits it.
  const needsHome = $derived(isKlipper && homedAxes != null && !/x/i.test(homedAxes));

  async function jog(dx, dy, mm) {
    acting = `${dx ? (dx > 0 ? 'X' : '-X') : (dy > 0 ? 'Y' : '-Y')}${mm}`; err = null;
    try { await api.xyJog(printerId, dx, dy); await refresh(); }
    catch (e) { err = needsHome ? 'Home the printer first.' : (e?.message || 'Move failed.'); }
    finally { acting = null; }
  }
  async function home() {
    acting = 'home'; err = null;
    try { await api.homeAxes(printerId, 'all'); await refresh(); }
    catch (e) { err = e?.message || 'Homing failed.'; }
    finally { acting = null; }
  }
  async function zJog(distance) {
    acting = 'z' + distance; err = null;
    try { await api.bedJog(printerId, distance); await refresh(); }
    catch (e) { err = e?.message || 'Bed move failed.'; }
    finally { acting = null; }
  }

  // ---- extruder -----------------------------------------------------------
  let side = $state(0);            // 0 = right, 1 = left (engine's indices)
  let extrudeMm = $state(10);
  async function pickSide(v) {
    side = v;
    if (!canSelectExtruder) return;
    acting = 'side'; err = null;
    try { await api.selectExtruder(printerId, v); await refresh(); }
    catch (e) { err = e?.message || 'Could not switch extruder.'; }
    finally { acting = null; }
  }
  async function extrude(sign) {
    const d = sign * Math.abs(Number(extrudeMm) || 0);
    if (!d) return;
    acting = 'e' + sign; err = null;
    try { await api.extruderJog(printerId, d); await refresh(); }
    catch (e) { err = e?.message || 'Extruder move failed.'; }
    finally { acting = null; }
  }

  // ---- header pills -------------------------------------------------------
  let partsOpen = $state(false);
  let optionsOpen = $state(false);
  let calibOpen = $state(false);

  const disabled = $derived(!connected || printing);
</script>

<div class="card ctl">
  <div class="chead">
    <span class="ctitle">Control</span>
    <span class="sp"></span>
    <button class="pill" onclick={() => (partsOpen = true)}
            data-tip="Wear items and service schedule" aria-label="Printer parts">Printer Parts</button>
    {#if canPrintOptions}
      <button class="pill" onclick={() => (optionsOpen = true)} disabled={!connected}
              data-tip="On-board monitoring while a job runs" aria-label="Print options">Print Options</button>
    {/if}
    <button class="pill" onclick={() => (calibOpen = true)} disabled={!connected}
            data-tip="Bed levelling, resonance and friends" aria-label="Calibration">Calibration</button>
  </div>

  {#if printing}
    <p class="note">Manual controls are disabled while a print is running.</p>
  {:else if !connected}
    <p class="note">The printer is offline — controls are disabled.</p>
  {:else if needsHome}
    <p class="note warn">
      Not homed yet, so moves will be refused.
      <button class="btn btn-ghost btn-xs" onclick={home} disabled={acting === 'home'}>
        {acting === 'home' ? 'Homing…' : '⌂ Home now'}
      </button>
    </p>
  {/if}

  <div class="grid">
    <!-- ============ temperatures + machine toggles ============ -->
    <div class="col temps">
      {#each tempRows as r (r.key)}
        <div class="trow">
          <span class="tico" aria-hidden="true">
            {#if r.kind === 'bed'}▭{:else if r.kind === 'chamber'}▣{:else}⬇{/if}
          </span>
          {#if r.badge}<span class="tbadge">{r.badge}</span>{/if}
          <span class="tcur mono">{r.cur != null ? r.cur.toFixed(0) : '—'}</span>
          {#if editing === r.key}
            <!-- svelte-ignore a11y_autofocus -->
            <input class="tinput mono" type="number" min="0" max={r.max} autofocus
                   bind:value={draft}
                   onblur={() => commitTemp(r)}
                   onkeydown={(e) => { if (e.key === 'Enter') commitTemp(r); if (e.key === 'Escape') editing = null; }}
                   aria-label={`${r.label} target temperature`} />
          {:else}
            <button class="ttgt mono" onclick={() => beginEdit(r)}
                    disabled={r.readonly || disabled || acting === 'temp-' + r.key}
                    data-tip={r.readonly ? `${r.label} is read-only on this printer` : `Set the ${r.label.toLowerCase()} target`}
                    aria-label={`Set the ${r.label} target temperature`}>/{r.target || 0}</button>
          {/if}
          <span class="tunit">°C</span>
          {#if r.target > 0 && !r.readonly}
            <button class="toff" onclick={() => setTemp(r, 0)} disabled={disabled || acting === 'temp-' + r.key}
                    data-tip={`Turn the ${r.label.toLowerCase()} off`} aria-label={`Turn the ${r.label} off`}>✕</button>
          {/if}
        </div>
      {/each}

      {#if canAirduct}
        <div class="airduct">
          <span class="alabel"><span aria-hidden="true">❋</span> Air Condition</span>
          <div class="seg">
            <button class:on={st.airduct_mode === 'cooling'} onclick={() => setAirduct('cooling')}
                    disabled={disabled || acting === 'airduct'}
                    data-tip="Filter and cool the chamber air">Cool</button>
            <button class:on={st.airduct_mode === 'heating'} onclick={() => setAirduct('heating')}
                    disabled={disabled || acting === 'airduct'}
                    data-tip="Circulate warm air and close the exhaust">Warm</button>
          </div>
        </div>
      {/if}

      {#if fans.length}
        <div class="fans">
          {#each fans as f (f.key)}
            <div class="fan">
              <span class="fanhd">
                <span class="fanlbl"><span aria-hidden="true">✽</span> {f.label}</span>
                <span class="fanpct mono">{fanVal(f)}%</span>
              </span>
              <input class="fanrange" type="range" min="0" max="100" step="5" value={fanVal(f)}
                     oninput={(e) => (fanUI = { ...fanUI, [f.key]: Number(e.currentTarget.value) })}
                     onchange={(e) => commitFan(f, Number(e.currentTarget.value))}
                     disabled={disabled} aria-label={`${f.label} fan speed percent`} />
            </div>
          {/each}
        </div>
      {/if}

      {#if canSpeedProfile}
        <div class="speed">
          <span class="alabel"><span aria-hidden="true">⏱</span> Print speed</span>
          <div class="seg wrap">
            {#each SPEEDS as [mode, label] (mode)}
              <button class:on={Number(st.speed_level) === mode} onclick={() => setSpeed(mode)}
                      disabled={!connected || acting === 'speed'}
                      data-tip={`Print at ${label.toLowerCase()} speed`}>{label}</button>
            {/each}
          </div>
        </div>
      {/if}

      <button class="lamp" class:on={st.chamber_light} onclick={toggleLamp}
              disabled={!connected || acting === 'lamp'}
              data-tip={st.chamber_light ? 'Turn the light off' : 'Turn the light on'}
              aria-label="Toggle the chamber light" aria-pressed={!!st.chamber_light}>
        <span class="lampico" aria-hidden="true">☀</span>
        <span>Lamp</span>
      </button>
    </div>

    <!-- ============ XY wheel + Z ============ -->
    <div class="col motion">
      <PrinterJogWheel steps={[1, 10]} disabled={disabled} busy={acting}
                       onjog={jog} onhome={home} homeLabel="Home all axes" />
      <div class="zrow">
        <button class="zb" onclick={() => zJog(10)} disabled={disabled} aria-label="Move the bed up 10 millimetres"><span aria-hidden="true">↑</span> 10</button>
        <button class="zb" onclick={() => zJog(1)} disabled={disabled} aria-label="Move the bed up 1 millimetre"><span aria-hidden="true">↑</span> 1</button>
        <span class="zlbl">Bed</span>
        <button class="zb" onclick={() => zJog(-1)} disabled={disabled} aria-label="Move the bed down 1 millimetre"><span aria-hidden="true">↓</span> 1</button>
        <button class="zb" onclick={() => zJog(-10)} disabled={disabled} aria-label="Move the bed down 10 millimetres"><span aria-hidden="true">↓</span> 10</button>
      </div>
    </div>

    <!-- ============ extruder ============ -->
    <div class="col extruder">
      {#if canSelectExtruder}
        <div class="seg lr">
          <button class:on={side === 1} onclick={() => pickSide(1)} disabled={disabled || acting === 'side'}
                  data-tip="Drive the left nozzle">Left</button>
          <button class:on={side === 0} onclick={() => pickSide(0)} disabled={disabled || acting === 'side'}
                  data-tip="Drive the right nozzle">Right</button>
        </div>
      {/if}

      <button class="eb up" onclick={() => extrude(-1)} disabled={disabled || acting === 'e-1'}
              data-tip="Retract filament" aria-label="Retract filament">
        <span aria-hidden="true">⇈</span>
      </button>

      <div class="etube" aria-hidden="true">
        <span class="tube"></span><span class="tube"></span>
        <span class="head"></span>
      </div>

      <button class="eb down" onclick={() => extrude(1)} disabled={disabled || acting === 'e1'}
              data-tip="Extrude filament" aria-label="Extrude filament">
        <span aria-hidden="true">⇊</span>
      </button>

      <label class="emm">
        <input class="input xs mono" type="number" min="1" max="100" bind:value={extrudeMm}
               disabled={disabled} aria-label="Extrude or retract distance in millimetres" />
        <span class="muted tiny">mm</span>
      </label>
      <span class="elabel">Extruder</span>
    </div>
  </div>

  {#if err}<p class="err">{err}</p>{/if}
</div>

{#if partsOpen}
  <ModalShell title="Printer parts" subtitle={meta?.name || 'This printer'} width="600px"
              onclose={() => (partsOpen = false)}>
    <MaintenancePanel printerId={printerId} />
    {#snippet footer()}
      <button class="btn btn-ghost btn-sm" onclick={() => (partsOpen = false)}>Close</button>
    {/snippet}
  </ModalShell>
{/if}
{#if optionsOpen}
  <PrintOptionsModal printerId={printerId} status={st}
                     onclose={() => (optionsOpen = false)} onsaved={refresh} />
{/if}
{#if calibOpen}
  <CalibrationModal printerId={printerId} isBambu={isBambu} connected={connected}
                    printing={printing} supportsChamber={canChamber}
                    onclose={() => (calibOpen = false)} />
{/if}

<style>
  .ctl { padding: 0; overflow: visible; }
  .chead {
    display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap;
    padding: 0.55rem 0.75rem; border-bottom: 1px solid var(--ophq-border-soft);
  }
  .ctitle { font-size: 0.9rem; color: var(--ophq-text-2); font-weight: 600; }
  .sp { flex: 1; }
  .pill {
    border: 1px solid transparent; border-radius: 999px; cursor: pointer;
    padding: 0.28rem 0.85rem; font-size: 0.82rem; font-weight: 600;
    background: var(--ophq-primary); color: #fff;
  }
  .pill:hover:not(:disabled) { filter: brightness(1.08); }
  .pill:disabled { opacity: 0.45; cursor: default; }
  .pill:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: 2px; }

  .note { margin: 0; padding: 0.5rem 0.8rem; font-size: 0.84rem; color: var(--ophq-muted); border-bottom: 1px solid var(--ophq-border-soft); }
  .note.warn { color: var(--ophq-warn); display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
  .btn-xs { padding: 0.1rem 0.5rem; font-size: 0.78rem; }

  .grid {
    display: grid; grid-template-columns: minmax(190px, 1fr) minmax(230px, 1.2fr) minmax(120px, 0.7fr);
    gap: 0; align-items: stretch;
  }
  .col { padding: 0.9rem 0.85rem; }
  .col + .col { border-left: 1px solid var(--ophq-border-soft); }

  /* temperatures */
  .temps { display: flex; flex-direction: column; gap: 0.5rem; }
  .trow { display: flex; align-items: center; gap: 0.4rem; font-size: 0.95rem; }
  .tico { width: 1.2rem; text-align: center; color: var(--ophq-muted); font-size: 0.9rem; }
  .tbadge {
    width: 17px; height: 17px; border-radius: 50%; display: inline-grid; place-items: center;
    font-size: 0.62rem; font-weight: 800; background: var(--ophq-surface-2); border: 1px solid var(--ophq-border); color: var(--ophq-text-2);
  }
  .tcur { font-weight: 700; min-width: 2.2rem; text-align: right; }
  .ttgt { background: none; border: 0; padding: 0 0.1rem; color: var(--ophq-muted); cursor: pointer; font-size: 0.92rem; }
  .ttgt:hover:not(:disabled) { color: var(--ophq-primary-2); text-decoration: underline; }
  .ttgt:disabled { cursor: default; }
  .ttgt:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: 1px; border-radius: 3px; }
  .tinput { width: 3.6rem; padding: 0.1rem 0.3rem; font-size: 0.85rem; background: var(--ophq-bg-2); border: 1px solid var(--ophq-primary); color: var(--ophq-text); border-radius: 4px; }
  .tunit { color: var(--ophq-muted); font-size: 0.82rem; }
  .toff { margin-left: auto; background: none; border: 0; color: var(--ophq-muted); cursor: pointer; font-size: 0.8rem; padding: 0 0.2rem; }
  .toff:hover:not(:disabled) { color: var(--ophq-danger); }

  .speed { margin-top: 0.4rem; display: flex; flex-direction: column; gap: 0.35rem; }
  .seg.wrap { flex-wrap: wrap; border-radius: var(--radius-sm); }
  .airduct { margin-top: 0.4rem; display: flex; flex-direction: column; gap: 0.35rem; }
  .alabel { font-size: 0.86rem; color: var(--ophq-text-2); display: inline-flex; gap: 0.35rem; align-items: center; }

  .seg { display: inline-flex; align-self: flex-start; border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; background: var(--ophq-bg-2); }
  .seg button {
    border: 0; background: none; cursor: pointer; padding: 0.22rem 0.7rem;
    font-size: 0.8rem; color: var(--ophq-text-2);
  }
  .seg button.on { background: var(--ophq-primary); color: #fff; font-weight: 600; }
  .seg button:disabled { opacity: 0.45; cursor: default; }
  .seg button:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: -2px; }

  .fans { display: flex; flex-direction: column; gap: 0.45rem; margin-top: 0.4rem; }
  .fanhd { display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--ophq-text-2); }
  .fanpct { color: var(--ophq-text); font-weight: 600; }
  .fanrange { width: 100%; accent-color: var(--ophq-primary); cursor: pointer; }
  .fanrange:disabled { opacity: 0.5; cursor: default; }

  .lamp {
    margin-top: 0.5rem; align-self: flex-start;
    display: inline-flex; align-items: center; gap: 0.45rem;
    border: 1px solid var(--ophq-border); border-radius: var(--radius-sm);
    background: var(--ophq-bg-2); color: var(--ophq-text-2);
    padding: 0.35rem 0.7rem; font-size: 0.85rem; cursor: pointer;
  }
  .lampico { font-size: 1rem; }
  .lamp.on { border-color: var(--ophq-accent); color: var(--ophq-accent); background: color-mix(in srgb, var(--ophq-accent) 14%, transparent); }
  .lamp:disabled { opacity: 0.45; cursor: default; }
  .lamp:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: 2px; }

  /* motion */
  .motion { display: flex; flex-direction: column; align-items: center; gap: 0.8rem; justify-content: center; }
  .zrow { display: flex; align-items: center; gap: 0.3rem; flex-wrap: wrap; justify-content: center; }
  .zb {
    border: 1px solid var(--ophq-border); border-radius: var(--radius-sm);
    background: var(--ophq-bg-2); color: var(--ophq-text-2);
    padding: 0.32rem 0.6rem; font-size: 0.82rem; cursor: pointer; min-width: 3rem;
  }
  .zb:hover:not(:disabled) { border-color: var(--ophq-primary); color: var(--ophq-text); }
  .zb:disabled { opacity: 0.45; cursor: default; }
  .zb:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: 1px; }
  .zlbl { font-size: 0.8rem; color: var(--ophq-muted); padding: 0 0.2rem; }

  /* extruder */
  .extruder { display: flex; flex-direction: column; align-items: center; gap: 0.55rem; justify-content: center; }
  .seg.lr { margin-bottom: 0.2rem; }
  .eb {
    width: 46px; height: 38px; display: grid; place-items: center;
    border: 1px solid var(--ophq-border); border-radius: var(--radius-sm);
    background: var(--ophq-bg-2); color: var(--ophq-text-2); cursor: pointer; font-size: 1.05rem;
  }
  .eb:hover:not(:disabled) { border-color: var(--ophq-primary); color: var(--ophq-text); }
  .eb:disabled { opacity: 0.45; cursor: default; }
  .eb:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: 1px; }

  .etube { position: relative; height: 46px; width: 44px; display: flex; gap: 6px; justify-content: center; }
  .etube .tube { width: 5px; height: 34px; border-radius: 3px; background: linear-gradient(180deg, var(--ophq-primary-2), var(--ophq-primary)); opacity: 0.85; }
  .etube .head { position: absolute; bottom: 0; left: 50%; transform: translateX(-50%); width: 26px; height: 14px; border-radius: 3px; background: var(--ophq-surface-2); border: 1px solid var(--ophq-border); }

  .emm { display: inline-flex; align-items: center; gap: 0.3rem; }
  .input.xs { max-width: 3.6rem; padding: 0.2rem 0.35rem; font-size: 0.82rem; }
  .elabel { font-size: 0.8rem; color: var(--ophq-muted); }
  .tiny { font-size: 0.78rem; }

  .err { color: var(--ophq-danger); font-size: 0.85rem; margin: 0; padding: 0.5rem 0.8rem; border-top: 1px solid var(--ophq-border-soft); }

  @media (max-width: 900px) {
    .grid { grid-template-columns: 1fr 1fr; }
    .col:nth-child(3) { grid-column: 1 / -1; border-left: 0; border-top: 1px solid var(--ophq-border-soft); flex-direction: row; flex-wrap: wrap; }
  }
  @media (max-width: 620px) {
    .grid { grid-template-columns: 1fr; }
    .col + .col { border-left: 0; border-top: 1px solid var(--ophq-border-soft); }
  }
</style>
