<script module>
  // Cached once per page load, shared across every printer-detail instance.
  let hmsCache = null;
</script>

<script>
  // OpenPrintHQ — per-printer detail & control.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // One shell for every printer type: camera, printing progress, the control
  // cluster, then filament. A machine that can't do something doesn't render a
  // greyed-out version of it — the section is simply absent, decided by the
  // availability sets below rather than by probing the engine.
  //
  // Every section is arrangeable (see $lib/printerSections).
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { get } from 'svelte/store';
  import { api } from '$lib/api';

  import PrinterCameraPanel from '$lib/components/PrinterCameraPanel.svelte';
  import PrinterProgressPanel from '$lib/components/PrinterProgressPanel.svelte';
  import PrinterControlPanel from '$lib/components/PrinterControlPanel.svelte';
  import PrinterFilamentPanel from '$lib/components/PrinterFilamentPanel.svelte';
  import PowerPanel from '$lib/components/PowerPanel.svelte';
  import MaintenancePanel from '$lib/components/MaintenancePanel.svelte';
  import KlipperTuning from '$lib/components/KlipperTuning.svelte';
  import GcodeConsole from '$lib/components/GcodeConsole.svelte';
  import KlipperConsole from '$lib/components/KlipperConsole.svelte';
  import EjectPanel from '$lib/components/EjectPanel.svelte';
  import PrinterSettingsModal from '$lib/components/PrinterSettingsModal.svelte';
  import LocatePrinter from '$lib/components/LocatePrinter.svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import SectionFrame from '$lib/components/SectionFrame.svelte';
  import PrinterLayoutBar from '$lib/components/PrinterLayoutBar.svelte';

  import { printerLabel, printerImage } from '$lib/models.js';
  import { markSeen, recentlyOnline } from '$lib/online.js';
  import { appearance, saveAppearance as persistAppearance } from '$lib/stores/appearance';
  import {
    orderedSections, resolveLayout, hasOverride, layoutFromDraft, mergeLayout
  } from '$lib/printerSections';

  const id = $derived($page.params.id);

  let loading = $state(true);
  let error = $state(null);       // 'not-found' | 'no-instance' | string
  let st = $state(null);
  let meta = $state(null);
  let acting = $state(null);
  let timer = null;
  let klipperHomed = $state(null);

  // ---- live camera --------------------------------------------------------
  let camTick = $state(0);
  let camTimer;
  let camAvailable = $state(true);
  function openCamera() {
    window.open(`/app/printers/${id}/camera`, '_blank');
  }

  // ---- per-printer settings ----------------------------------------------
  let settingsOpen = $state(false);
  const chamberHeaterOn = $derived(!!meta?.chamber_heater);
  const showFilamentPanel = $derived(meta?.show_filament_panel !== false);
  const showBedEjection = $derived(!!meta?.show_bed_ejection);

  // ---- offline relocate ---------------------------------------------------
  const isOffline = $derived(!!st && !online);
  const printerForLocate = $derived(
    meta ? {
      id: meta.id ?? Number(id), name: meta.name || 'Printer',
      ip_address: meta.ip_address, serial_number: meta.serial_number,
      mac_address: meta.mac_address, connection_type: meta.connection_type
    } : null
  );
  function afterRelink() {
    api.printer(id).then((m) => (meta = m)).catch(() => {});
    loadStatus(false);
  }

  async function loadStatus(initial = false) {
    if (initial) { loading = true; error = null; }
    try {
      st = await api.printerStatus(id);
      // Feed the online-hysteresis cache on every poll. The deleted
      // BambuDashboard was the only thing on this page that did this; without it
      // the grace window expires after 90s and a single MQTT blip makes the page
      // declare the printer offline and pop the relocate banner.
      markSeen(id, st?.connected);
      error = null;
    } catch (e) {
      if (e.status === 404) error = 'not-found';
      else if (e.status === 409) error = 'no-instance';
      else error = e.message || 'engine unreachable';
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    // Status is cheap and wants to feel live. The camera snapshot is a full
    // JPEG relayed through the connector and control-plane, so it refreshes far
    // more slowly — WebRTC is what "live" is for; this fallback only needs to
    // look current. Polling pauses while arranging so sections don't shuffle
    // out from under the cursor.
    timer = setInterval(() => {
      if (acting || editing) return;
      loadStatus(false);
    }, 3000);
    camTimer = setInterval(() => { if (camAvailable && !acting) camTick++; }, 60000);
    return () => { clearInterval(timer); clearInterval(camTimer); };
  });

  // The route component is reused across client-side nav between printers, so
  // onMount alone wouldn't refresh.
  $effect(() => {
    const _id = id;
    camAvailable = true; camTick = 0;
    api.printer(_id).then((m) => (meta = m)).catch(() => {});
    loadStatus(true);
  });

  // ---- state helpers ------------------------------------------------------
  // `connected` flaps for a second or two whenever the printer's MQTT session
  // reconnects, even though it never went anywhere. Everything user-visible reads
  // this smoothed value instead; only genuinely-absent-for-90s counts as offline.
  const online = $derived(!!st?.connected || (!!st && recentlyOnline(id)));
  // A stale 'offline' from the engine during a blip must not leak through either.
  const stateStr = $derived.by(() => {
    const raw = (st?.state || '').toString();
    if (raw && !/offline/i.test(raw)) return raw;
    return online ? 'idle' : 'offline';
  });
  const isPrinting = $derived(/run|print/i.test(stateStr));
  const isPaused = $derived(/pause/i.test(stateStr));
  const isKlipper = $derived((meta?.connection_type || '').toLowerCase() === 'klipper');
  const isBambu = $derived((meta?.connection_type || 'bambu').toLowerCase() === 'bambu');

  function tone(s) {
    const x = s.toLowerCase();
    if (/run|print/.test(x)) return 'primary';
    if (/pause/.test(x)) return 'accent';
    if (/idle|ready|finish|online/.test(x)) return 'ok';
    if (/error|offline|fault|fail/.test(x)) return 'danger';
    return '';
  }

  // ---- printer alerts (Bambu HMS) -----------------------------------------
  // The lookup key is "MMMM_EEEE": module from attr bits 16-31, error from the
  // LOW 16 bits of code. The mask matters — some entries carry high bits whose
  // real error nibble is the low half.
  function hmsErrNum(e) {
    return (parseInt(String(e?.code ?? '').replace(/^0x/i, ''), 16) || 0) & 0xffff;
  }
  function hmsShortCode(e) {
    const attr = Number(e?.attr) || 0;
    const moduleHex = ((attr >>> 16) & 0xffff).toString(16).toUpperCase().padStart(4, '0');
    const errHex = hmsErrNum(e).toString(16).toUpperCase().padStart(4, '0');
    return `${moduleHex}_${errHex}`;
  }

  let hmsMap = $state(hmsCache);
  async function ensureHms() {
    if (hmsMap) return;
    try { hmsMap = hmsCache = await api.hmsDescriptions(); }
    catch { hmsMap = hmsCache = {}; }
  }
  $effect(() => {
    if ((st?.hms_errors || []).length && !hmsMap) ensureHms();
  });

  const alerts = $derived.by(() =>
    (st?.hms_errors || [])
      // Bambu reserves error nibbles >= 0x4000 for real faults; lower values are
      // status codes a healthy printer emits during normal operation.
      .filter((e) => hmsErrNum(e) >= 0x4000)
      .map((e) => {
        const code = hmsShortCode(e);
        const sev = Number(e.severity) || 0;
        return { code, desc: hmsMap ? hmsMap[code] : undefined, severe: sev === 1 || sev === 2 };
      })
  );

  let clearingHms = $state(false);
  async function clearHms() {
    clearingHms = true;
    try {
      await api.hmsClear(id);
      await api.printerAction(id, 'refresh-status').catch(() => {});
      await loadStatus(false);
    } catch (e) {
      error = e.message || 'could not clear alerts';
    } finally { clearingHms = false; }
  }

  // ---- what this printer actually has -------------------------------------
  const hasFilamentUnit = $derived.by(() => {
    if ((st?.ams || []).length > 0) return true;
    const vt = Array.isArray(st?.vt_tray) ? st.vt_tray : (st?.vt_tray ? [st.vt_tray] : []);
    return vt.some((t) => t?.tray_type);
  });
  const hasNozzleInfo = $derived((st?.nozzle_rack || []).length > 0 || (st?.nozzles || []).length > 0);

  // ---- actions ------------------------------------------------------------
  async function control(action, label) {
    acting = label;
    try {
      await api.printerAction(id, action);
      await api.printerAction(id, 'refresh-status').catch(() => {});
      await loadStatus(false);
    } catch (e) {
      error = e.message || `${label} failed`;
    } finally { acting = null; }
  }
  async function toggleConnection() {
    await control(online ? 'disconnect' : 'connect', online ? 'disconnect' : 'connect');
  }
  async function clearPlate() {
    acting = 'clear';
    try { await api.clearPlate(id); await loadStatus(false); }
    catch (e) { error = e.message || 'could not clear the plate'; }
    finally { acting = null; }
  }

  // Emergency stop — immediate, NO confirmation (the regular Stop is gated).
  // Klipper gets a true firmware halt; others get an immediate print-stop.
  async function emergencyStop() {
    if (acting) return;
    acting = 'estop';
    try {
      if (isKlipper) await api.klipperEmergencyStop(id);
      else await api.printerAction(id, 'print/stop');
      await api.printerAction(id, 'refresh-status').catch(() => {});
      await loadStatus(false);
    } catch (e) { error = e.message || 'emergency stop failed'; }
    finally { acting = null; }
  }

  // ---- arrangeable sections ----------------------------------------------
  let editing = $state(false);
  let layoutScope = $state('global');
  let layoutSaving = $state(false);
  let layoutMsg = $state(null);
  let draftPage = $state(null);

  const variant = $derived(isBambu ? 'bambu' : 'classic');
  const savedLayout = $derived(resolveLayout($appearance?.printerSections, id));
  const printerHasOverride = $derived(hasOverride($appearance?.printerSections, id));

  // `live: false` is what arranging uses, so a section can be positioned before
  // it first appears (printer offline, AMS unplugged, camera not reachable yet).
  // `live: true` is what renders.
  function availableKeys(live) {
    const a = new Set(['header', 'progress', 'control', 'power', 'maintenance']);
    if (!live || alerts.length) a.add('alerts');
    if (!live || camAvailable || st?.cover_url) a.add('camera');
    if (isBambu && showFilamentPanel && (!live || hasFilamentUnit || hasNozzleInfo)) a.add('filament');
    if (isKlipper) { a.add('klipper-tuning'); if (!live || online) a.add('console'); }
    if (showBedEjection) a.add('eject');
    if (!isKlipper && (!live || online)) a.add('gcode');
    return a;
  }
  const potentialKeys = $derived(availableKeys(false));
  const liveKeys = $derived(availableKeys(true));

  const pageList = $derived.by(() => {
    const base = editing
      ? (draftPage || [])
      : orderedSections(savedLayout.layout, { variant, scope: 'page', available: liveKeys })
          .filter((s) => !s.hidden);
    return base.map((s, i, arr) => ({
      ...s,
      unavailable: editing && !liveKeys.has(s.key),
      first: i === 0,
      last: i === arr.length - 1
    }));
  });

  function seedDraft(layout) {
    draftPage = orderedSections(layout, { variant, scope: 'page', available: potentialKeys });
  }
  function startEditing() {
    seedDraft(savedLayout.layout);
    layoutScope = savedLayout.scope;
    layoutMsg = null;
    editing = true;
  }
  function cancelEditing() { editing = false; draftPage = null; layoutMsg = null; }
  function resetDraft() { seedDraft({ order: [], hidden: [] }); layoutMsg = null; }

  function moveIn(list, key, dir) {
    const i = list.findIndex((s) => s.key === key);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= list.length) return list;
    const next = [...list];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  }
  const movePage = (key, dir) => (draftPage = moveIn(draftPage || [], key, dir));
  const togglePage = (key) =>
    (draftPage = (draftPage || []).map((s) =>
      s.key === key && !s.def?.lockHide ? { ...s, hidden: !s.hidden } : s));

  async function saveLayout() {
    layoutSaving = true; layoutMsg = null;
    try {
      const cfg = get(appearance);
      const ps = { ...(cfg.printerSections || {}) };
      const byPrinter = { ...(ps.byPrinter || {}) };
      // Merge onto the layout we started from so keys that weren't on screen
      // survive (a section only another printer type shows, say).
      const merged = mergeLayout(savedLayout.layout, layoutFromDraft(draftPage || []));
      if (layoutScope === 'printer') {
        byPrinter[String(id)] = merged;
      } else {
        // Saving as the default also drops this printer's override — otherwise
        // the new default would appear to do nothing on the page you set it from.
        delete byPrinter[String(id)];
        ps.order = merged.order;
        ps.hidden = merged.hidden;
      }
      ps.byPrinter = byPrinter;
      await persistAppearance({ ...cfg, printerSections: ps });
      editing = false; draftPage = null;
      layoutMsg = { ok: true, text: layoutScope === 'printer'
        ? 'Layout saved for this printer.'
        : 'Layout saved as your default for every printer.' };
    } catch (e) {
      layoutMsg = { ok: false, text: e?.message || 'Could not save the layout.' };
    } finally { layoutSaving = false; }
  }
</script>

<PageTitle page={st?.name || meta?.name || 'Printer'} />

{#snippet pageSection(s)}
  <SectionFrame def={s.def} hidden={s.hidden} unavailable={s.unavailable} {editing}
                first={s.first} last={s.last}
                onmove={(d) => movePage(s.key, d)} ontoggle={() => togglePage(s.key)}>
    {#if s.key === 'header'}
      <div class="phead card">
        <div class="pid">
          {#if printerImage(meta?.connection_type, meta?.model)}
            <div class="pthumb">
              <img src={printerImage(meta?.connection_type, meta?.model)}
                   alt="{printerLabel(meta?.connection_type, meta?.model) || 'printer'}" />
            </div>
          {/if}
          <div class="pnames">
            <h1>{st?.name || meta?.name || 'Printer'}</h1>
            <div class="pmeta mono">
              {#if printerLabel(meta?.connection_type, meta?.model)}
                <span>{printerLabel(meta?.connection_type, meta?.model)}</span>
              {/if}
              <span>#{id}</span>
              {#if st?.firmware_version}<span>{st.firmware_version}</span>{/if}
              {#if st?.wifi_signal != null}<span>{st.wifi_signal}dBm</span>{/if}
            </div>
          </div>
        </div>
        <div class="pactions">
          <span class="chip {tone(stateStr)}">{stateStr}</span>
          <button class="btn btn-ghost btn-sm"
                  data-tip={online ? 'Disconnect from the printer' : 'Connect to the printer'}
                  aria-label={online ? 'Disconnect from the printer' : 'Connect to the printer'}
                  onclick={toggleConnection} disabled={!!acting}>
            {online ? 'Disconnect' : 'Connect'}
          </button>
          <button class="btn btn-ghost btn-sm" data-tip="Printer settings" aria-label="Printer settings"
                  onclick={() => (settingsOpen = true)}>
            <span aria-hidden="true">⚙</span> Settings
          </button>
          <button class="estop" type="button" onclick={emergencyStop}
                  disabled={!online || acting === 'estop'}
                  data-tip="Emergency stop — immediate, no confirmation" data-tip-pos="below"
                  aria-label="Emergency stop — immediate, no confirmation">
            <span class="estop-oct"><span class="estop-txt">{acting === 'estop' ? '…' : 'STOP'}</span></span>
          </button>
        </div>
      </div>

    {:else if s.key === 'alerts'}
      <div class="alerts">
        {#each alerts as a}
          <div class="alert {a.severe ? 'sev' : ''}">
            <span class="ai">⚠</span>
            <span class="atext">
              {#if a.desc}{a.desc}{:else}Printer alert — check the printer's screen for details.{/if}
              <span class="mono acode">HMS {a.code}</span>
            </span>
          </div>
        {/each}
        <button class="btn btn-ghost btn-sm clr" onclick={clearHms} disabled={clearingHms}>
          {clearingHms ? 'Clearing…' : 'Clear alerts'}
        </button>
      </div>

    {:else if s.key === 'camera'}
      <PrinterCameraPanel printerId={id} printerName={meta?.name || 'printer'} status={st}
                          connected={online} isBambu={isBambu} tick={camTick}
                          onerror={() => (camAvailable = false)} onopen={openCamera} />

    {:else if s.key === 'progress'}
      <PrinterProgressPanel printerId={id} status={st} isBambu={isBambu} acting={acting} online={online}
                            onpause={() => control('print/pause', 'pause')}
                            onresume={() => control('print/resume', 'resume')}
                            onstop={() => control('print/stop', 'stop')}
                            onclearplate={clearPlate}
                            refresh={() => loadStatus(false)} />

    {:else if s.key === 'control'}
      <PrinterControlPanel printerId={id} status={st} meta={meta} printing={isPrinting || isPaused}
                           isBambu={isBambu} isKlipper={isKlipper} chamberHeater={chamberHeaterOn}
                           homedAxes={klipperHomed} online={online} refresh={() => loadStatus(false)} />

    {:else if s.key === 'filament'}
      <PrinterFilamentPanel printerId={id} status={st} isBambu={isBambu}
                            refresh={() => loadStatus(false)} />

    {:else if s.key === 'console'}
      <KlipperConsole printerId={id} connected={online} printing={isPrinting}
                      onhomed={(h) => (klipperHomed = h)} />

    {:else if s.key === 'power'}
      <PowerPanel printerId={id} />

    {:else if s.key === 'maintenance'}
      <MaintenancePanel printerId={id} />

    {:else if s.key === 'klipper-tuning'}
      <KlipperTuning printerId={id} connected={online} printing={isPrinting} />

    {:else if s.key === 'eject'}
      <EjectPanel printerId={id} connected={online} kind={meta?.connection_type} status={st} />

    {:else if s.key === 'gcode'}
      <GcodeConsole printerId={id} kind={meta?.connection_type} printing={isPrinting} />
    {/if}
  </SectionFrame>
{/snippet}

<div class="head">
  <a href="/app/printers" class="btn btn-ghost btn-sm" data-tip="Back to all printers"
     aria-label="Back to all printers">← Printers</a>
  <div class="head-actions">
    {#if !editing && !loading && !error}
      <button class="btn btn-ghost btn-sm" data-tip="Reorder or hide the sections on this page"
              aria-label="Arrange sections" onclick={startEditing}>
        <span aria-hidden="true">⇅</span> Arrange
      </button>
    {/if}
    <button class="btn btn-ghost btn-sm" data-tip="Refresh live status now" aria-label="Refresh live status"
            onclick={() => control('refresh-status', 'refresh')} disabled={!!acting}>
      {acting === 'refresh' ? 'Refreshing…' : 'Refresh'}
    </button>
  </div>
</div>

{#if loading}
  <div class="card card-pad muted">Connecting to your printer…</div>
{:else if error === 'not-found'}
  <div class="card card-pad">
    <h3>Printer not found</h3>
    <p class="muted">This printer may have been removed. <a href="/app/printers">Back to printers</a>.</p>
  </div>
{:else if error === 'no-instance'}
  <div class="card card-pad">
    <h3>No instance yet</h3>
    <p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p>
  </div>
{:else}
  {#if error}<p class="err banner">{error}</p>{/if}

  {#if editing}
    <PrinterLayoutBar scope={layoutScope} printerName={st?.name || meta?.name || 'this printer'}
      hasOverride={printerHasOverride} saving={layoutSaving} msg={layoutMsg}
      onscope={(v) => (layoutScope = v)} onsave={saveLayout}
      oncancel={cancelEditing} onreset={resetDraft} />
  {:else if layoutMsg}
    <p class={layoutMsg.ok ? 'ok-msg banner' : 'err banner'}>{layoutMsg.text}</p>
  {/if}

  {#if isOffline && printerForLocate}
    <div class="offline-locate">
      <div class="ol-head">
        <span class="ol-title">⚠ {printerForLocate.name} is offline</span>
        <span class="muted tiny">Checking the network in case its IP changed…</span>
      </div>
      <LocatePrinter printer={printerForLocate} auto={true} onrelinked={afterRelink} />
    </div>
  {/if}

  <div class="stack">
    {#each pageList as s (s.key)}
      {@render pageSection(s)}
    {/each}
  </div>
{/if}

{#if settingsOpen}
  <PrinterSettingsModal
    printerId={id}
    name={st?.name || meta?.name || 'Printer'}
    isKlipper={isKlipper}
    chamberHeater={!!meta?.chamber_heater}
    showFilamentPanel={meta?.show_filament_panel !== false}
    showBedEjection={!!meta?.show_bed_ejection}
    onclose={() => (settingsOpen = false)}
    onsave={(cfg) => { meta = { ...meta, ...cfg }; }}
    ondelete={() => goto('/app/printers')} />
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
  .head-actions { display: flex; gap: 0.5rem; align-items: center; }
  .banner { margin: 0 0 1rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .tiny { font-size: 0.8rem; }

  /* Sections are plain siblings with a single gap: each panel is a card in its
     own right, and outside edit mode SectionFrame adds no wrapper at all. */
  .stack { display: flex; flex-direction: column; gap: 1rem; }

  /* header */
  .phead {
    display: flex; justify-content: space-between; align-items: center; gap: 1rem;
    flex-wrap: wrap; padding: 0.8rem 0.9rem;
  }
  .pid { display: flex; align-items: center; gap: 0.85rem; min-width: 0; }
  .pthumb {
    width: 54px; height: 54px; flex: 0 0 auto; border-radius: var(--radius-sm);
    background: var(--ophq-bg-2); border: 1px solid var(--ophq-border);
    display: grid; place-items: center; overflow: hidden;
  }
  .pthumb img { width: 100%; height: 100%; object-fit: contain; padding: 3px; }
  .pnames { min-width: 0; }
  .pnames h1 { margin: 0; font-size: 1.5rem; line-height: 1.15; }
  .pmeta { display: flex; gap: 0.7rem; color: var(--ophq-muted); font-size: 0.8rem; flex-wrap: wrap; margin-top: 0.2rem; }
  .pactions { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }

  /* Emergency stop — octagon "stop sign", immediate (no confirmation). */
  .estop { background: none; border: 0; padding: 0; cursor: pointer; }
  .estop-oct {
    --oct: polygon(30% 0, 70% 0, 100% 30%, 100% 70%, 70% 100%, 30% 100%, 0 70%, 0 30%);
    position: relative; display: grid; place-items: center; width: 50px; height: 50px;
    background: #fff;
    clip-path: var(--oct);
    filter: drop-shadow(0 3px 8px rgba(229,52,47,0.5));
    transition: transform 0.12s ease, filter 0.12s ease;
    animation: estopPulse 2.6s ease-in-out infinite;
  }
  .estop-oct::before {
    content: ''; position: absolute; inset: 3px; clip-path: var(--oct);
    background: radial-gradient(circle at 50% 33%, #ff6a5f 0%, #e5342f 52%, #b81c17 100%);
  }
  .estop-txt {
    position: relative; z-index: 1; color: #fff; font-weight: 900; font-size: 0.62rem;
    letter-spacing: 0.04em; text-shadow: 0 1px 2px rgba(0,0,0,0.4);
  }
  .estop:hover:not(:disabled) .estop-oct { transform: scale(1.06); filter: drop-shadow(0 4px 12px rgba(229,52,47,0.78)); animation: none; }
  .estop:active:not(:disabled) .estop-oct { transform: scale(0.95); }
  .estop:focus-visible .estop-oct { outline: 2px solid var(--ophq-primary); outline-offset: 3px; }
  .estop:disabled { cursor: default; opacity: 0.4; }
  .estop:disabled .estop-oct { animation: none; }
  @keyframes estopPulse {
    0%, 100% { filter: drop-shadow(0 3px 8px rgba(229,52,47,0.4)); }
    50% { filter: drop-shadow(0 3px 14px rgba(229,52,47,0.85)); }
  }
  @media (prefers-reduced-motion: reduce) {
    .estop-oct { animation: none; }
  }

  /* offline banner */
  .offline-locate {
    margin: 0 0 1rem; padding: 0.9rem 1rem;
    border: 1px solid rgba(255,176,32,0.35); background: rgba(255,176,32,0.06);
    border-radius: var(--radius-sm); display: flex; flex-direction: column; gap: 0.7rem;
  }
  .ol-head { display: flex; flex-direction: column; gap: 0.15rem; }
  .ol-title { font-weight: 600; color: var(--ophq-warn); }

  /* alerts */
  .alerts { display: flex; flex-direction: column; gap: 0.5rem; }
  .alert {
    display: flex; align-items: center; gap: 0.6rem; padding: 0.7rem 1rem;
    border-radius: var(--radius-sm); font-size: 0.9rem;
    border: 1px solid rgba(245,166,35,0.35); background: rgba(245,166,35,0.08); color: var(--ophq-warn);
  }
  .alert.sev { border-color: rgba(255,92,108,0.35); background: rgba(255,92,108,0.08); color: var(--ophq-danger); }
  .alert .ai { font-size: 1rem; }
  .alert .atext { flex: 1; }
  .alert .acode { opacity: 0.7; font-size: 0.8rem; margin-left: 0.4rem; white-space: nowrap; }
  .alerts .clr { align-self: flex-end; }

  @media (max-width: 620px) {
    .pactions { width: 100%; justify-content: space-between; }
  }
</style>
