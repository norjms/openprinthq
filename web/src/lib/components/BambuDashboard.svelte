<script>
  // OpenPrintHQ — Bambu printer dashboard (skinned like the app; data-driven from
  // the engine's PrinterStatus). Mirrors the Bambuddy printer view: header chips,
  // status + progress, temps/nozzle, fans, nozzle rack (tool-changer), controls,
  // AMS/filament grid, and a print/camera footer. All colours come from --ophq-*
  // theme variables so it follows Light / Dark / Accessible.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';
  import { printerLabel, nozzleType } from '$lib/models.js';
  import { markSeen, recentlyOnline } from '$lib/online.js';

  let { printerId, status = null, meta = null, refresh = () => {}, oncamera = () => {} } = $props();

  const st = $derived(status || {});

  // Online with hysteresis — the engine's connected flag can flap on transient
  // MQTT reconnects; a recent connection still reads online (and stops the
  // header/state from bouncing to "Offline").
  $effect(() => { markSeen(printerId, st?.connected); });
  const online = $derived(!!st?.connected || recentlyOnline(printerId));

  // ---- identity / header ------------------------------------------------
  const name = $derived(st?.name || meta?.name || 'Printer');
  const model = $derived(printerLabel(meta?.connection_type || 'bambu', meta?.model || st?.model || ''));
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
  // A job is shown only when a print is actually active (printing/paused). A
  // plate merely loaded while the printer sits Ready is NOT a job — idle shows
  // no model name / layer / cover, just "Ready to print".
  const hasJob = $derived(isPrinting || isPaused);
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
    !online ? 'Offline' :
    isPrinting ? 'Printing' :
    isPaused ? 'Paused' :
    awaitingClear ? stateLabel(stateStr) :
    'Ready'
  );
  const dispTone = $derived(
    !online ? 'danger' :
    isPrinting ? 'primary' :
    isPaused ? 'accent' :
    awaitingClear ? 'accent' :
    'ok'
  );
  const readyLine = $derived(
    !online ? 'Printer offline' :
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
  const diaL = $derived(st?.nozzles?.[0]?.nozzle_diameter || nozzleDia || '');
  const diaR = $derived(st?.nozzles?.[1]?.nozzle_diameter || '');

  // Settable temperature cards. Dual-nozzle machines get one card per nozzle
  // (index 0 = default → reports as T.nozzle; index 1 → T.nozzle_2), each set
  // independently so you can watch that nozzle heat toward its target. Chamber is
  // only settable on machines with a chamber heater (H2C/H2D/H2DPro/H2S/X2D).
  const tempCards = $derived.by(() => {
    const num = (v) => (v != null && v !== '' ? Number(v) : null);
    const heatingTo = (cur, tgt) => tgt > 0 && cur != null && cur < tgt - 1;
    const cards = [];
    if (dualNozzle) {
      cards.push({ key: 'nzL', kind: 'nozzle', nozzle: 0, label: 'Left nozzle', cur: num(T.nozzle), target: num(T.nozzle_target) || 0, settable: true });
      cards.push({ key: 'nzR', kind: 'nozzle', nozzle: 1, label: 'Right nozzle', cur: num(T.nozzle_2), target: num(T.nozzle_2_target) || 0, settable: true });
    } else {
      cards.push({ key: 'nz', kind: 'nozzle', nozzle: 0, label: 'Nozzle', cur: num(T.nozzle), target: num(T.nozzle_target) || 0, settable: true });
    }
    cards.push({ key: 'bed', kind: 'bed', nozzle: 0, label: 'Bed', cur: num(T.bed), target: num(T.bed_target) || 0, settable: true });
    cards.push({ key: 'chamber', kind: 'chamber', nozzle: 0, label: 'Chamber', cur: num(T.chamber), target: num(T.chamber_target) || 0, settable: !!st?.supports_chamber_heater });
    return cards.map((c) => ({ ...c, heating: heatingTo(c.cur, c.target) }));
  });

  // Editable target inputs, keyed by card. Set on Enter or the Set button.
  let tset = $state({});
  async function setTempCard(c) {
    const v = tset[c.key];
    if (v === '' || v == null) return;
    acting = 'temp-' + c.key;
    try {
      await api.setTemp(printerId, c.kind, Number(v), c.nozzle || 0);
      tset[c.key] = '';
      await api.printerAction(printerId, 'refresh-status').catch(() => {});
      await refresh();
    } catch (e) { /* surfaced on next poll */ } finally { acting = null; }
  }
  async function tempOff(c) {
    acting = 'temp-' + c.key;
    try {
      await api.setTemp(printerId, c.kind, 0, c.nozzle || 0);
      await api.printerAction(printerId, 'refresh-status').catch(() => {});
      await refresh();
    } catch (e) { /* */ } finally { acting = null; }
  }

  // ---- fans (report as %) — slider-adjustable ---------------------------
  const fans = $derived([
    { label: 'Part', key: 'part', v: st?.cooling_fan_speed },
    { label: 'Aux', key: 'aux', v: st?.big_fan1_speed },
    { label: 'Chamber', key: 'chamber', v: st?.big_fan2_speed }
  ].filter((f) => f.v != null));
  // Optimistic slider positions while dragging / just after a set; falls back to
  // the live reported speed.
  let fanUI = $state({});
  function fanVal(f) { return fanUI[f.label] != null ? fanUI[f.label] : Math.round(Number(f.v) || 0); }
  async function commitFan(f, pct) {
    fanUI[f.label] = pct;
    acting = 'fan-' + f.key;
    try { await api.fanSpeed(printerId, f.key, pct); await refresh(); }
    catch (e) { /* */ } finally { acting = null; }
  }

  // ---- nozzles: toolhead + rack (H2C/H2D tool-changer) -----------------
  // The engine's nozzle_rack carries ALL nozzle hardware: the hotend/toolhead
  // nozzles (ids 0,1) and the physical rack slots (ids 16-21 on H2C). Split them
  // so we can show what's currently in the toolhead separately from the rack.
  const nozzleInfo = $derived(st?.nozzle_rack || []);
  const rack = $derived(nozzleInfo); // (kept: presence of any nozzle info)
  const isRealNozzle = (n) => !!(n && n.serial_number && n.serial_number !== 'N/A' && (Number(n.max_temp) > 0 || n.nozzle_type));
  const toolheadNozzles = $derived(nozzleInfo.filter((n) => Number(n.id) < 16 && isRealNozzle(n)));
  const rackSlots = $derived.by(() => {
    const r = nozzleInfo.filter((n) => Number(n.id) >= 16);
    if (!r.length) return [];
    const base = Math.min(...r.map((n) => Number(n.id)));   // rack ids start at 16 on H2C
    return r.map((n) => ({ ...n, pos: Number(n.id) - base + 1 }));
  });

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
  // Emergency stop — a deliberate panic action, so it fires immediately with NO
  // confirmation (unlike the regular Stop). Bambu has no motor-kill over MQTT, so
  // the strongest immediate action is stopping the print.
  async function emergencyStop() {
    if (acting) return;
    await act('print/stop', 'estop');
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
    <div class="pd-thumb" title="{model || 'Printer'}">
      <svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round" aria-hidden="true">
        <rect x="8" y="8" width="48" height="48" rx="5" />
        <line x1="8" y1="21" x2="56" y2="21" />
        <rect x="25" y="21" width="14" height="8" rx="1.5" fill="currentColor" stroke="none" />
        <line x1="14" y1="47" x2="50" y2="47" />
      </svg>
    </div>
    <div class="pd-idbox">
      <h1 class="pd-name">{name}</h1>
      <div class="pd-sub">
        {#if model}<span>{model}</span>{/if}
        {#if nozzleDia}<span>· {nozzleDia}mm</span>{/if}
        {#if printHours != null}<span>· ⏱ {printHours}h</span>{/if}
      </div>
      <div class="pd-chips">
        <span class="pchip {online ? 'ok' : 'danger'}">🔗 {online ? 'Connected' : 'Offline'}</span>
        {#if wifi != null}<span class="pchip ok">▂▄▆ {wifi}dBm</span>{/if}
        <span class="pchip {faultCount ? 'warn' : 'ok'}">{faultCount ? '⚠' : '✓'} {faultCount ? faultCount : 'OK'}</span>
        {#if faultCount}<span class="pchip danger">🔧 {faultCount}</span>{/if}
        {#if firmware}<span class="pchip ok">✓ {firmware}</span>{/if}
        {#if doorOpen}<span class="pchip warn">🚪 Door</span>{/if}
      </div>
    </div>
    <button class="estop" type="button" onclick={emergencyStop} disabled={!online || acting === 'estop'}
            title="Emergency stop (immediate — no confirmation)" aria-label="Emergency stop">
      <span class="estop-oct"><span class="estop-txt">{acting === 'estop' ? '…' : 'STOP'}</span></span>
    </button>
  </div>

  <!-- ============ STATUS ============ -->
  <div class="pd-label">STATUS</div>
  <div class="pd-status" class:idle={!hasJob}>
    {#if hasJob}
      <div class="pd-cover">
        {#if st?.cover_url}<img src={st.cover_url} alt="current print" />{:else}<span class="pd-cube" aria-hidden="true">◲</span>{/if}
      </div>
    {/if}
    <div class="pd-status-body">
      <div class="pd-status-hd">
        <span class="pd-state {dispTone}">{dispState}</span>
        {#if awaitingClear}
          <button class="btn btn-primary btn-sm clearbtn" onclick={clearPlate} disabled={acting === 'clear' || !st?.connected}>
            {acting === 'clear' ? 'Clearing…' : '✓ Clear plate'}
          </button>
        {/if}
      </div>
      {#if hasJob}
        <div class="pd-job">{jobName || 'Printing'}</div>
        <div class="pd-bar"><div class="fill" style="width:{progress}%"></div></div>
      {/if}
      <div class="pd-ready">
        {readyLine}
        {#if hasJob && fmtEta(st?.remaining_time)}<span class="mono"> · ~{fmtEta(st.remaining_time)} left</span>{/if}
        {#if hasJob && st?.layer_num != null && st?.total_layers}<span class="mono"> · layer {st.layer_num}/{st.total_layers}</span>{/if}
      </div>
    </div>
    {#if hasJob}<div class="pd-pct mono">{Math.round(progress)}%</div>{/if}
  </div>

  <!-- temps (settable) + nozzle / rack -->
  <div class="pd-temps">
    {#each tempCards as c (c.key)}
      <div class="tcard settable" class:heating={c.heating}>
        <div class="tc-hd">
          <span class="tico" aria-hidden="true">🌡</span>
          <span class="tk">{c.label}</span>
          {#if c.heating}<span class="heatchip">heating</span>{/if}
        </div>
        <span class="tv mono">{c.cur != null ? c.cur.toFixed(0) + '°' : '—'}{#if c.target}<span class="tgt"> / {c.target.toFixed(0)}°</span>{/if}</span>
        {#if c.settable}
          <div class="tset">
            <input class="tinput mono" type="number" min="0" placeholder="set °C"
                   bind:value={tset[c.key]}
                   onkeydown={(e) => { if (e.key === 'Enter') setTempCard(c); }}
                   disabled={!online} aria-label="{c.label} target temperature" />
            <button class="tsetbtn" onclick={() => setTempCard(c)} disabled={!online || acting === 'temp-' + c.key}>Set</button>
            {#if c.target > 0}<button class="tsetbtn off" onclick={() => tempOff(c)} disabled={!online || acting === 'temp-' + c.key}>Off</button>{/if}
          </div>
        {/if}
      </div>
    {/each}
    {#if !nozzleInfo.length && (diaL || diaR)}
      <div class="tcard nz">
        <div class="tc-hd"><span class="tico" aria-hidden="true">⬇</span><span class="tk">Nozzle</span></div>
        <span class="tv mono nzv">{diaL ? `L ${diaL}` : ''}{diaR ? ` · R ${diaR}` : ''}</span>
      </div>
    {/if}
  </div>

  {#if fans.length}
    <div class="pd-fans">
      {#each fans as f (f.key)}
        <div class="fanctl">
          <div class="fanhd"><span class="fanlbl"><span aria-hidden="true">✽</span> {f.label}</span><span class="fanpct mono">{fanVal(f)}%</span></div>
          <input class="fanrange" type="range" min="0" max="100" step="1"
                 value={fanVal(f)}
                 oninput={(e) => (fanUI[f.label] = Number(e.target.value))}
                 onchange={(e) => commitFan(f, Number(e.target.value))}
                 disabled={!online} aria-label="{f.label} fan speed percent" />
        </div>
      {/each}
    </div>
  {/if}

  {#if nozzleInfo.length}
    <!-- ============ NOZZLES (toolhead + rack) ============ -->
    <div class="pd-label">NOZZLES</div>
    <div class="nzpanel">
      <div class="nz-head">
        <span class="nz-cap">In toolhead</span>
        {#if toolheadNozzles.length}
          {#each toolheadNozzles as n}
            <span class="nz-inhead" title={nozzleType(n.nozzle_type).full || n.nozzle_type}>
              <b class="mono">{n.nozzle_diameter} mm</b>
              <span class="nz-mat">{nozzleType(n.nozzle_type).full || n.nozzle_type || '—'}</span>
            </span>
          {/each}
        {:else}
          <span class="muted">— no nozzle installed</span>
        {/if}
      </div>
      {#if rackSlots.length}
        <div class="nz-rack">
          <span class="nz-cap">Rack</span>
          <div class="nz-slots">
            {#each rackSlots as s (s.id)}
              <span class="nz-slot"
                    title={`Position ${s.pos} · ${s.nozzle_diameter} mm · ${nozzleType(s.nozzle_type).full || s.nozzle_type || 'nozzle'}`}>
                <span class="nz-pos">P{s.pos}</span>
                <span class="nz-dia mono">{s.nozzle_diameter}</span>
                <span class="nz-ty">{nozzleType(s.nozzle_type).short || s.nozzle_type}</span>
              </span>
            {/each}
          </div>
        </div>
      {/if}
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
  .pd-head { display: flex; gap: 1rem; align-items: flex-start; position: relative; }
  .pd-thumb { width: 74px; height: 74px; flex: 0 0 auto; border-radius: var(--radius-sm); overflow: hidden; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); display: grid; place-items: center; color: var(--ophq-text-2); }
  .pd-thumb img { width: 100%; height: 100%; object-fit: cover; }
  .pd-thumb svg { width: 60%; height: 60%; opacity: 0.85; }
  /* Emergency stop — octagon "stop sign", top-right of the header. Immediate. */
  .estop { position: absolute; top: 0; right: 0; background: none; border: 0; padding: 0; cursor: pointer; }
  .estop-oct { display: grid; place-items: center; width: 62px; height: 62px; background: var(--ophq-danger, #e5342f);
    clip-path: polygon(30% 0, 70% 0, 100% 30%, 100% 70%, 70% 100%, 30% 100%, 0 70%, 0 30%);
    border: 3px solid #fff2; box-shadow: 0 2px 10px rgba(229,52,47,0.4); transition: transform 0.1s, filter 0.1s; }
  .estop:hover:not(:disabled) .estop-oct { filter: brightness(1.08); transform: scale(1.04); }
  .estop:active:not(:disabled) .estop-oct { transform: scale(0.96); }
  .estop:disabled { cursor: default; opacity: 0.4; }
  .estop-txt { color: #fff; font-weight: 800; font-size: 0.82rem; letter-spacing: 0.02em; }
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

  /* temp cards (settable) */
  .pd-temps { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.6rem; margin-top: 0.6rem; }
  .tcard { background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.6rem 0.7rem; display: flex; flex-direction: column; align-items: flex-start; gap: 0.35rem; text-align: left; }
  .tcard.settable.heating { border-color: rgba(255,176,32,0.4); }
  .tc-hd { display: flex; align-items: center; gap: 0.35rem; width: 100%; }
  .tico { font-size: 0.95rem; filter: grayscale(0.2); }
  .tk { font-size: 0.78rem; color: var(--ophq-muted); }
  .heatchip { margin-left: auto; font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 700; color: var(--ophq-accent); border: 1px solid rgba(255,176,32,0.35); background: rgba(255,176,32,0.1); border-radius: 999px; padding: 0.05rem 0.4rem; }
  .tv { font-size: 1.15rem; color: var(--ophq-text); font-weight: 700; }
  .tv .tgt { color: var(--ophq-muted); font-weight: 500; font-size: 0.9rem; }
  .tset { display: flex; gap: 0.3rem; width: 100%; margin-top: 0.1rem; }
  .tinput { flex: 1; min-width: 0; background: var(--ophq-surface); border: 1px solid var(--ophq-border); color: var(--ophq-text); border-radius: calc(var(--radius-sm) - 3px); padding: 0.28rem 0.4rem; font-size: 0.82rem; }
  .tinput:focus { outline: none; border-color: var(--ophq-primary); }
  .tsetbtn { background: var(--ophq-surface); border: 1px solid var(--ophq-border); color: var(--ophq-text-2); border-radius: calc(var(--radius-sm) - 3px); padding: 0.28rem 0.5rem; font-size: 0.78rem; cursor: pointer; }
  .tsetbtn:hover:not(:disabled) { border-color: var(--ophq-primary); color: var(--ophq-text); }
  .tsetbtn:disabled { opacity: 0.45; cursor: default; }
  .tsetbtn.off:hover:not(:disabled) { border-color: var(--ophq-danger); color: var(--ophq-danger); }
  .tcard.nz { align-items: center; text-align: center; justify-content: center; }
  .tcard.nz .nzv { color: var(--ophq-accent); }
  /* nozzles (toolhead + rack) */
  .nzpanel { display: flex; flex-direction: column; gap: 0.6rem; margin-top: 0.6rem; }
  .nz-head, .nz-rack { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
  .nz-cap { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); min-width: 5.5rem; }
  .nz-inhead { display: inline-flex; align-items: baseline; gap: 0.4rem; padding: 0.3rem 0.6rem; border-radius: var(--radius-sm); background: var(--ophq-primary-dim); border: 1px solid var(--ophq-primary); color: var(--ophq-primary-2); font-size: 0.85rem; }
  .nz-inhead b { color: var(--ophq-text); }
  .nz-mat { color: var(--ophq-primary-2); }
  .nz-slots { display: flex; gap: 0.4rem; flex-wrap: wrap; }
  .nz-slot { display: inline-flex; flex-direction: column; align-items: center; gap: 0.05rem; min-width: 3rem; padding: 0.35rem 0.45rem; border-radius: calc(var(--radius-sm) - 2px); background: var(--ophq-surface); border: 1px solid var(--ophq-border); cursor: default; }
  .nz-slot:hover { border-color: var(--ophq-primary); }
  .nz-pos { font-size: 0.62rem; font-weight: 700; letter-spacing: 0.03em; color: var(--ophq-muted); }
  .nz-dia { font-size: 0.92rem; font-weight: 700; color: var(--ophq-text); line-height: 1.1; }
  .nz-ty { font-size: 0.64rem; color: var(--ophq-text-2); }

  /* fans (slider-adjustable) */
  .pd-fans { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.6rem; margin-top: 0.6rem; }
  .fanctl { background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.5rem 0.7rem; display: flex; flex-direction: column; gap: 0.35rem; }
  .fanhd { display: flex; align-items: center; justify-content: space-between; font-size: 0.85rem; color: var(--ophq-text-2); }
  .fanpct { color: var(--ophq-text); font-weight: 600; }
  .fanrange { width: 100%; accent-color: var(--ophq-primary); cursor: pointer; }
  .fanrange:disabled { opacity: 0.5; cursor: default; }

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
