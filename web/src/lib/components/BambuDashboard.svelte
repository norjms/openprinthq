<script>
  // OpenPrintHQ — Bambu printer dashboard (skinned like the app; data-driven from
  // the engine's PrinterStatus). Mirrors the Bambuddy printer view: header chips,
  // status + progress, temps/nozzle, fans, nozzle rack (tool-changer), controls,
  // AMS/filament grid, and a print/camera footer. All colours come from --ophq-*
  // theme variables so it follows Light / Dark / Accessible.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';

  let { printerId, status = null, meta = null, refresh = () => {}, oncamera = () => {} } = $props();

  const st = $derived(status || {});

  // ---- identity / header ------------------------------------------------
  const name = $derived(st?.name || meta?.name || 'Printer');
  const model = $derived(meta?.model || st?.model || '');
  const nozzleDia = $derived(st?.nozzles?.[0]?.nozzle_diameter || '');
  const printHours = $derived(meta?.print_hours_offset ? Math.round(meta.print_hours_offset) : null);

  const wifi = $derived(st?.wifi_signal ?? null);
  const firmware = $derived(st?.firmware_version || '');
  const doorOpen = $derived(!!st?.door_open);

  // Genuine HMS faults only (error nibble >= 0x4000); lower codes are normal phase codes.
  function hmsErrNum(e) { return (parseInt(String(e?.code ?? '').replace(/^0x/i, ''), 16) || 0) & 0xffff; }
  const faults = $derived((st?.hms_errors || []).filter((e) => hmsErrNum(e) >= 0x4000));
  const faultCount = $derived(faults.length);

  // ---- state / job ------------------------------------------------------
  const stateStr = $derived((st?.state || (st?.connected ? 'idle' : 'offline')).toString());
  const isPrinting = $derived(/run|print/i.test(stateStr));
  const isPaused = $derived(/pause/i.test(stateStr));
  const isFailed = $derived(/fail|error|fault/i.test(stateStr));
  // After a finished/failed/stopped print the printer waits for the plate to be
  // cleared; until then the last outcome is shown. Once cleared it's Ready again.
  const awaitingClear = $derived(!!st?.awaiting_plate_clear);
  const hasJob = $derived(isPrinting || isPaused || !!st?.subtask_name || !!st?.gcode_file);
  const jobName = $derived(st?.subtask_name || st?.gcode_file || st?.current_print || '');
  const progress = $derived(Math.min(100, Math.max(0, Number(st?.progress) || 0)));

  function stateLabel(s) {
    const x = s.toLowerCase();
    if (/finish/.test(x)) return 'Finished';
    if (/fail/.test(x)) return 'Failed';
    if (/pause/.test(x)) return 'Paused';
    if (/run|print/.test(x)) return 'Printing';
    if (/idle|ready/.test(x)) return 'Idle';
    if (/offline/.test(x)) return 'Offline';
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  function stateTone(s) {
    const x = s.toLowerCase();
    if (/run|print/.test(x)) return 'primary';
    if (/pause/.test(x)) return 'accent';
    if (/finish|idle|ready|online/.test(x)) return 'ok';
    if (/fail|error|offline|fault/.test(x)) return 'danger';
    return '';
  }
  // Headline status: idle (and plate already clear) reads "Ready", never the last
  // job's Failed/Finished — that outcome only shows while awaiting a plate clear.
  const dispState = $derived(
    !st?.connected ? 'Offline' :
    isPrinting ? 'Printing' :
    isPaused ? 'Paused' :
    awaitingClear ? stateLabel(stateStr) :
    'Ready'
  );
  const dispTone = $derived(
    !st?.connected ? 'danger' :
    isPrinting ? 'primary' :
    isPaused ? 'accent' :
    awaitingClear ? 'accent' :
    'ok'
  );
  const readyLine = $derived(
    !st?.connected ? 'Printer offline' :
    isPrinting ? 'Printing…' :
    isPaused ? 'Paused' :
    awaitingClear ? 'Print done — clear the build plate, then mark it clear' :
    'Ready to print'
  );

  function fmtEta(mins) {
    if (mins == null || mins <= 0) return null;
    const h = Math.floor(mins / 60), m = Math.round(mins % 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }

  // ---- temperatures -----------------------------------------------------
  const T = $derived(st?.temperatures || {});
  const dualNozzle = $derived((st?.nozzles?.length || 0) > 1 || T.nozzle_2 != null);
  const nozzleL = $derived(Number(T.nozzle) || 0);
  const nozzleR = $derived(Number(T.nozzle_2) || 0);
  const bed = $derived(T.bed != null ? Number(T.bed) : null);
  const chamber = $derived(T.chamber != null ? Number(T.chamber) : null);
  const diaL = $derived(st?.nozzles?.[0]?.nozzle_diameter || nozzleDia || '');
  const diaR = $derived(st?.nozzles?.[1]?.nozzle_diameter || '');

  // ---- fans (report as %) ----------------------------------------------
  const fans = $derived([
    { label: 'Part', v: st?.cooling_fan_speed },
    { label: 'Aux', v: st?.big_fan1_speed },
    { label: 'Chamber', v: st?.big_fan2_speed }
  ].filter((f) => f.v != null));

  // ---- nozzle rack (H2C tool-changer) ----------------------------------
  const rack = $derived(st?.nozzle_rack || []);

  // ---- AMS / filaments --------------------------------------------------
  const hex = (c) => (c && String(c).replace(/0+$/, '') ? '#' + String(c).slice(0, 6) : '');
  const letters = ['A', 'B', 'C', 'D'];
  function amsSide(u, i) {
    const m = st?.ams_extruder_map || {};
    const e = m[String(u.id)] ?? m[u.id];
    if (e === 0) return 'R';
    if (e === 1) return 'L';
    return '';
  }
  const amsUnits = $derived.by(() =>
    (st?.ams || []).map((u, i) => ({
      id: u.id,
      label: (u.is_ams_ht ? 'HT' : 'AMS') + '-' + (letters[i] || (i + 1)),
      side: amsSide(u, i),
      humidity: (u.humidity != null && u.humidity !== '') ? Number(u.humidity) : null,
      temp: (u.temp != null && u.temp !== '') ? Number(u.temp) : null,
      slots: (u.tray || []).map((t, j) => ({
        n: j + 1,
        type: t?.tray_type || '',
        color: hex(t?.tray_color),
        remain: (t?.remain != null && t.remain >= 0) ? t.remain : null,
        active: st?.tray_now === u.id * 4 + j,
        empty: !t?.tray_type
      }))
    }))
  );
  // External spool(s) — vt_tray. Dual-nozzle machines expose L/R externals.
  const externals = $derived.by(() => {
    const arr = Array.isArray(st?.vt_tray) ? st.vt_tray : (st?.vt_tray ? [st.vt_tray] : []);
    return arr.map((t, i) => ({
      side: dualNozzle ? (i === 0 ? 'L' : 'R') : '',
      type: t?.tray_type || '',
      color: hex(t?.tray_color),
      remain: (t?.remain != null && t.remain >= 0) ? t.remain : null,
      active: st?.tray_now === 254,
      empty: !t?.tray_type
    }));
  });
  const hasFilaments = $derived(amsUnits.length > 0 || externals.length > 0);

  // ---- controls ---------------------------------------------------------
  let acting = $state(null);
  let confirmStop = $state(false);
  async function act(action, label) {
    acting = label;
    try {
      await api.printerAction(printerId, action);
      await api.printerAction(printerId, 'refresh-status').catch(() => {});
      await refresh();
    } catch (e) { /* surfaced by parent on next poll */ }
    finally { acting = null; confirmStop = false; }
  }
  async function toggleLight() {
    acting = 'light';
    try { await api.chamberLight(printerId, !st?.chamber_light); await refresh(); }
    catch (e) {} finally { acting = null; }
  }
  async function clearPlate() {
    acting = 'clear';
    try {
      await api.clearPlate(printerId);
      await api.printerAction(printerId, 'refresh-status').catch(() => {});
      await refresh();
    } catch (e) {} finally { acting = null; }
  }
</script>

<div class="pdash card">
  <!-- ============ HEADER ============ -->
  <div class="pd-head">
    <div class="pd-thumb">
      {#if st?.cover_url}<img src={st.cover_url} alt="" />{:else}<span class="pd-thumb-i" aria-hidden="true">🖨</span>{/if}
    </div>
    <div class="pd-idbox">
      <h1 class="pd-name">{name}</h1>
      <div class="pd-sub">
        {#if model}<span>{model}</span>{/if}
        {#if nozzleDia}<span>· {nozzleDia}mm</span>{/if}
        {#if printHours != null}<span>· ⏱ {printHours}h</span>{/if}
      </div>
      <div class="pd-chips">
        <span class="pchip {st?.connected ? 'ok' : 'danger'}">🔗 {st?.connected ? 'Connected' : 'Offline'}</span>
        {#if wifi != null}<span class="pchip ok">▂▄▆ {wifi}dBm</span>{/if}
        <span class="pchip {faultCount ? 'warn' : 'ok'}">{faultCount ? '⚠' : '✓'} {faultCount ? faultCount : 'OK'}</span>
        {#if faultCount}<span class="pchip danger">🔧 {faultCount}</span>{/if}
        {#if firmware}<span class="pchip ok">✓ {firmware}</span>{/if}
        {#if doorOpen}<span class="pchip warn">🚪 Door</span>{/if}
      </div>
    </div>
  </div>

  <!-- ============ STATUS ============ -->
  <div class="pd-label">STATUS</div>
  <div class="pd-status">
    <div class="pd-cover">
      {#if st?.cover_url}<img src={st.cover_url} alt="current print" />{:else}<span class="pd-cube" aria-hidden="true">◲</span>{/if}
    </div>
    <div class="pd-status-body">
      <div class="pd-status-hd">
        <span class="pd-state {dispTone}">{dispState}</span>
        {#if awaitingClear}
          <button class="btn btn-primary btn-sm clearbtn" onclick={clearPlate} disabled={acting === 'clear' || !st?.connected}>
            {acting === 'clear' ? 'Clearing…' : '✓ Clear plate'}
          </button>
        {/if}
      </div>
      <div class="pd-job {hasJob ? '' : 'muted'}">{hasJob ? jobName || 'Printing' : 'No active job'}</div>
      <div class="pd-bar"><div class="fill" style="width:{progress}%"></div></div>
      <div class="pd-ready">
        {readyLine}
        {#if hasJob && fmtEta(st?.remaining_time)}<span class="mono"> · ~{fmtEta(st.remaining_time)} left</span>{/if}
        {#if hasJob && st?.layer_num != null && st?.total_layers}<span class="mono"> · layer {st.layer_num}/{st.total_layers}</span>{/if}
      </div>
    </div>
    <div class="pd-pct mono">{hasJob ? Math.round(progress) + '%' : '---%'}</div>
  </div>

  <!-- temps + nozzle / rack -->
  <div class="pd-temps" class:has-rack={rack.length > 0}>
    <div class="tcard">
      <span class="tico n" aria-hidden="true">🌡</span>
      <span class="tk">{dualNozzle ? 'L / R' : 'Nozzle'}</span>
      <span class="tv mono">{dualNozzle ? `${nozzleL.toFixed(0)}° / ${nozzleR.toFixed(0)}°` : `${nozzleL.toFixed(0)}°`}</span>
    </div>
    <div class="tcard">
      <span class="tico b" aria-hidden="true">🌡</span>
      <span class="tk">Bed</span>
      <span class="tv mono">{bed != null ? bed.toFixed(0) + '°C' : '—'}</span>
    </div>
    <div class="tcard">
      <span class="tico c" aria-hidden="true">🌡</span>
      <span class="tk">Chamber</span>
      <span class="tv mono">{chamber != null ? chamber.toFixed(0) + '°C' : '—'}</span>
    </div>
    {#if rack.length === 0}
      <div class="tcard nz">
        <span class="tico y" aria-hidden="true">⬇</span>
        <span class="tv mono nzv">{diaL ? `L ${diaL}` : ''}{diaR ? ` · R ${diaR}` : ''}{!diaL && !diaR ? '—' : ''}</span>
        <span class="tk">Nozzle</span>
      </div>
    {:else}
      <div class="rack">
        <div class="rack-t">Nozzle Rack</div>
        <div class="rack-slots">
          {#each rack as r}
            <span class="rslot" class:mounted={r.stat === 1 || r.stat === 2}>{r.nozzle_diameter || '—'}</span>
          {/each}
        </div>
      </div>
    {/if}
  </div>

  {#if fans.length}
    <div class="pd-fans">
      {#each fans as f}
        <div class="fanpill"><span aria-hidden="true">✽</span> {f.label} {Math.round(Number(f.v) || 0)}%</div>
      {/each}
    </div>
  {/if}

  <!-- ============ CONTROLS ============ -->
  <div class="pd-label">CONTROLS</div>
  <div class="pd-controls">
    <button class="ctl" class:on={st?.chamber_light} title="Chamber light" aria-label="Toggle chamber light"
            onclick={toggleLight} disabled={acting === 'light' || !st?.connected}>💡</button>
    <a class="ctl" href="#temps" title="Temperatures & preheat" aria-label="Temperatures">🔥</a>
    <a class="ctl" href="#move" title="Move / jog" aria-label="Move">✥</a>
    <a class="ctl" href="#camera" title="Camera" aria-label="Camera" onclick={oncamera}>📷</a>
    <span class="ctl-sp"></span>
    <button class="btn btn-ghost" onclick={() => act(isPaused ? 'print/resume' : 'print/pause', 'pp')}
            disabled={!!acting || (!isPrinting && !isPaused)}>
      {isPaused ? '▶ Resume' : '❙❙ Pause'}
    </button>
    {#if confirmStop}
      <button class="btn btn-danger" onclick={() => act('print/stop', 'stop')} disabled={!!acting}>Confirm stop</button>
      <button class="btn btn-ghost" onclick={() => (confirmStop = false)} disabled={!!acting}>Cancel</button>
    {:else}
      <button class="btn btn-ghost stopb" onclick={() => (confirmStop = true)}
              disabled={!isPrinting && !isPaused}>◻ Stop</button>
    {/if}
  </div>

  <!-- ============ FILAMENTS ============ -->
  {#if hasFilaments}
    <div class="pd-label">FILAMENTS</div>
    <div class="pd-ams">
      {#each amsUnits as u}
        <div class="amsbox">
          <div class="amsbox-hd">
            <span class="amsname">{u.label}{#if u.side}<span class="sidebadge {u.side === 'L' ? 'l' : 'r'}">{u.side}</span>{/if}</span>
            <span class="amsmeta">
              {#if u.humidity != null}<span title="Humidity">💧 {u.humidity}%</span>{/if}
              {#if u.temp != null}<span title="Temperature">🌡 {u.temp.toFixed(1)}°C</span>{/if}
            </span>
          </div>
          <div class="slots" style="--n:{u.slots.length}">
            {#each u.slots as s}
              <div class="slot" class:active={s.active} class:empty={s.empty}>
                <span class="slotnum">{s.n}</span>
                <span class="slottype">{s.empty ? 'Empty' : s.type}</span>
                <span class="slotbar"><span class="slotfill" style="width:{s.remain ?? (s.empty ? 0 : 100)}%; background:{s.color || 'var(--ophq-primary)'}"></span></span>
              </div>
            {/each}
          </div>
        </div>
      {/each}
      {#if externals.length}
        <div class="amsbox ext">
          <div class="amsbox-hd"><span class="amsname">External</span></div>
          <div class="slots" style="--n:{externals.length}">
            {#each externals as e}
              <div class="slot" class:active={e.active} class:empty={e.empty}>
                {#if e.side}<span class="slotnum sq {e.side === 'L' ? 'l' : 'r'}">{e.side}</span>{/if}
                <span class="slottype">{e.empty ? 'Empty' : e.type}</span>
                <span class="slotbar"><span class="slotfill" style="width:{e.remain ?? (e.empty ? 0 : 100)}%; background:{e.color || 'var(--ophq-primary)'}"></span></span>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    </div>
  {/if}

  <!-- ============ FOOTER ============ -->
  <div class="pd-foot">
    <span class="foot-sp"></span>
    <a class="footbtn" href="#camera" onclick={oncamera} aria-label="Camera">📷</a>
    <a class="footbtn" href="/app/files" aria-label="Files">🗐</a>
    <a class="footbtn print" href="/app/files">🖨 Print</a>
  </div>
</div>

<style>
  .pdash { padding: 1.4rem 1.4rem 1.1rem; }

  /* header */
  .pd-head { display: flex; gap: 1rem; align-items: flex-start; }
  .pd-thumb { width: 74px; height: 74px; flex: 0 0 auto; border-radius: var(--radius-sm); overflow: hidden; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); display: grid; place-items: center; }
  .pd-thumb img { width: 100%; height: 100%; object-fit: cover; }
  .pd-thumb-i { font-size: 1.9rem; opacity: 0.7; }
  .pd-name { margin: 0; font-size: 1.9rem; line-height: 1; }
  .pd-sub { display: flex; gap: 0.35rem; flex-wrap: wrap; color: var(--ophq-muted); font-size: 0.9rem; margin: 0.35rem 0 0.7rem; }
  .pd-chips { display: flex; gap: 0.45rem; flex-wrap: wrap; }
  .pchip { display: inline-flex; align-items: center; gap: 0.3rem; padding: 0.28rem 0.6rem; border-radius: 999px; font-size: 0.8rem; font-weight: 600; border: 1px solid var(--ophq-border); color: var(--ophq-text-2); background: var(--ophq-surface); white-space: nowrap; }
  .pchip.ok { color: var(--ophq-success); border-color: color-mix(in srgb, var(--ophq-success) 35%, transparent); background: color-mix(in srgb, var(--ophq-success) 10%, transparent); }
  .pchip.warn { color: var(--ophq-warn); border-color: color-mix(in srgb, var(--ophq-warn) 35%, transparent); background: color-mix(in srgb, var(--ophq-warn) 10%, transparent); }
  .pchip.danger { color: var(--ophq-danger); border-color: color-mix(in srgb, var(--ophq-danger) 40%, transparent); background: color-mix(in srgb, var(--ophq-danger) 12%, transparent); }

  /* section labels */
  .pd-label { text-transform: uppercase; letter-spacing: 0.14em; font-size: 0.72rem; font-weight: 700; color: var(--ophq-muted); margin: 1.4rem 0 0.6rem; border-top: 1px solid var(--ophq-border-soft); padding-top: 0.9rem; }

  /* status */
  .pd-status { display: flex; gap: 1rem; align-items: stretch; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.9rem; }
  .pd-cover { width: 96px; height: 96px; flex: 0 0 auto; border-radius: var(--radius-sm); background: var(--ophq-surface); border: 1px solid var(--ophq-border); display: grid; place-items: center; }
  .pd-cover img { width: 100%; height: 100%; object-fit: cover; border-radius: var(--radius-sm); }
  .pd-cube { font-size: 2rem; color: var(--ophq-muted); }
  .pd-status-body { flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center; gap: 0.5rem; }
  .pd-status-hd { display: flex; align-items: center; gap: 0.6rem; }
  .pd-state { font-weight: 700; font-size: 1.05rem; }
  .pd-state.ok { color: var(--ophq-success); }
  .pd-state.primary { color: var(--ophq-primary-2); }
  .pd-state.accent { color: var(--ophq-accent); }
  .pd-state.danger { color: var(--ophq-danger); }
  .pd-job { font-size: 0.95rem; color: var(--ophq-text); }
  .pd-job.muted { color: var(--ophq-muted); }
  .pd-bar { height: 8px; background: var(--ophq-surface); border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; }
  .pd-bar .fill { height: 100%; background: linear-gradient(90deg, var(--ophq-primary), var(--ophq-primary-2)); transition: width 0.4s ease; }
  .pd-ready { font-size: 0.85rem; color: var(--ophq-text-2); }
  .pd-pct { align-self: flex-end; color: var(--ophq-muted); font-size: 0.95rem; }

  /* temp cards */
  .pd-temps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.6rem; margin-top: 0.6rem; }
  .pd-temps.has-rack { grid-template-columns: repeat(3, 1fr) 1.6fr; }
  .tcard { background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.7rem 0.8rem; display: flex; flex-direction: column; align-items: center; gap: 0.2rem; text-align: center; }
  .tico { font-size: 1rem; filter: grayscale(0.2); }
  .tk { font-size: 0.78rem; color: var(--ophq-muted); }
  .tv { font-size: 1.05rem; color: var(--ophq-text); font-weight: 600; }
  .tcard.nz .nzv { color: var(--ophq-accent); }
  .rack { background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.6rem 0.8rem; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.4rem; }
  .rack-t { font-size: 0.78rem; color: var(--ophq-muted); }
  .rack-slots { display: flex; gap: 0.3rem; flex-wrap: wrap; justify-content: center; }
  .rslot { min-width: 2.2rem; text-align: center; padding: 0.28rem 0.4rem; border-radius: calc(var(--radius-sm) - 3px); font-size: 0.82rem; font-weight: 600; font-family: var(--font-mono); background: var(--ophq-surface); border: 1px solid var(--ophq-border); color: var(--ophq-text-2); }
  .rslot.mounted { background: var(--ophq-accent); color: #1a1205; border-color: var(--ophq-accent); }

  /* fans */
  .pd-fans { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.6rem; margin-top: 0.6rem; }
  .fanpill { background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.5rem; text-align: center; font-size: 0.85rem; color: var(--ophq-text-2); }

  /* controls */
  .pd-controls { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
  .ctl { width: 44px; height: 44px; display: inline-grid; place-items: center; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); background: var(--ophq-bg-2); color: var(--ophq-text-2); font-size: 1.15rem; cursor: pointer; text-decoration: none; }
  .ctl:hover { border-color: var(--ophq-primary); color: var(--ophq-text); }
  .ctl.on { background: color-mix(in srgb, var(--ophq-accent) 20%, transparent); border-color: var(--ophq-accent); }
  .ctl:disabled { opacity: 0.45; cursor: not-allowed; }
  .ctl-sp { flex: 1; }
  .stopb { color: var(--ophq-muted); }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .btn-danger:hover { background: #ff7280; }

  /* filaments / AMS */
  .pd-ams { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.7rem; }
  @media (max-width: 620px) { .pd-ams { grid-template-columns: 1fr; } }
  .amsbox { background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.7rem 0.8rem; }
  .amsbox-hd { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.6rem; }
  .amsname { font-weight: 600; font-size: 0.9rem; display: inline-flex; align-items: center; gap: 0.45rem; }
  .sidebadge { font-size: 0.7rem; font-weight: 700; padding: 0.05rem 0.4rem; border-radius: 4px; background: var(--ophq-success); color: #06210f; }
  .sidebadge.l { background: var(--ophq-primary); color: #fff; }
  .amsmeta { display: flex; gap: 0.7rem; font-size: 0.8rem; color: var(--ophq-success); }
  .slots { display: grid; grid-template-columns: repeat(var(--n), 1fr); gap: 0.4rem; }
  .slot { border: 1px solid var(--ophq-border); border-radius: calc(var(--radius-sm) - 2px); padding: 0.5rem 0.4rem 0.4rem; display: flex; flex-direction: column; align-items: center; gap: 0.35rem; background: var(--ophq-surface); position: relative; min-height: 62px; justify-content: center; }
  .slot.active { border-color: var(--ophq-success); box-shadow: 0 0 0 1px var(--ophq-success); }
  .slot.empty { opacity: 0.6; }
  .slotnum { position: absolute; top: 4px; left: 6px; font-size: 0.62rem; color: var(--ophq-muted); width: 15px; height: 15px; border-radius: 50%; border: 1px solid var(--ophq-border); display: grid; place-items: center; }
  .slotnum.sq { position: static; width: 18px; height: 18px; border-radius: 50%; color: #fff; }
  .slotnum.sq.l { background: var(--ophq-primary); border-color: var(--ophq-primary); }
  .slotnum.sq.r { background: var(--ophq-danger); border-color: var(--ophq-danger); }
  .slottype { font-size: 0.82rem; font-weight: 600; text-align: center; }
  .slot.empty .slottype { color: var(--ophq-muted); font-weight: 500; }
  .slotbar { width: 100%; height: 4px; border-radius: 999px; background: var(--ophq-bg-2); overflow: hidden; }
  .slotfill { display: block; height: 100%; border-radius: 999px; }

  /* footer */
  .pd-foot { display: flex; align-items: center; gap: 0.5rem; margin-top: 1.3rem; padding-top: 0.9rem; border-top: 1px solid var(--ophq-border-soft); }
  .foot-sp { flex: 1; }
  .footbtn { width: 44px; height: 44px; display: inline-grid; place-items: center; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); background: var(--ophq-bg-2); color: var(--ophq-text-2); text-decoration: none; font-size: 1.05rem; }
  .footbtn:hover { border-color: var(--ophq-primary); color: var(--ophq-text); }
  .footbtn.print { width: auto; padding: 0 1.2rem; background: var(--ophq-success); color: #06210f; border-color: var(--ophq-success); font-weight: 700; gap: 0.4rem; }
  .footbtn.print:hover { filter: brightness(1.05); color: #06210f; }

  @media (max-width: 560px) {
    .pd-temps, .pd-temps.has-rack { grid-template-columns: repeat(2, 1fr); }
    .pd-name { font-size: 1.5rem; }
  }
</style>
