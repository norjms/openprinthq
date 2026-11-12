<script module>
  import { base as basePath } from '$app/paths';
  // Cached once per page load, shared across every printer-detail instance.
  let hmsCache = null;
</script>

<script>
  // ArtisanHub Farm, per-printer detail & control.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // One shell for every printer type: camera, printing progress, the control
  // cluster, then filament. A machine that can't do something doesn't render a
  // greyed-out version of it, the section is simply absent, decided by the
  // availability sets below rather than by probing the engine.
  //
  // Every section is arrangeable (see $lib/printerSections).
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { get } from 'svelte/store';
  import { api } from '$lib/api';

  import PrinterCameraPanel from '$lib/components/PrinterCameraPanel.svelte';
  import PrinterCameras from '$lib/components/PrinterCameras.svelte';
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
  import CameraStream from '$lib/components/CameraStream.svelte';
  import Icon from '$lib/components/AppIcon.svelte';
  import { liveFeed } from '$lib/stores/feed';
  import { setCrumb } from '$lib/stores/crumb';

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

  // ---- the fleet, for the printer switcher ---------------------------------
  let fleet = $state([]);
  onMount(() => {
    api.printers().then((d) => { fleet = (Array.isArray(d) ? d : (d?.printers || d?.items || [])).map((p) => ({ id: p.id, name: p.name || `Printer ${p.id}` })); }).catch(() => {});
    liveFeed.set(true);
    return () => liveFeed.set(false);
  });
  const fleetIdx = $derived(fleet.findIndex((p) => String(p.id) === String(id)));
  function stepPrinter(dir) {
    if (!fleet.length) return;
    const i = ((fleetIdx < 0 ? 0 : fleetIdx) + dir + fleet.length) % fleet.length;
    goto(`${basePath}/app/printers/${fleet[i].id}`);
  }
  $effect(() => { setCrumb(st?.name || meta?.name || ''); });

  // ---- live camera --------------------------------------------------------
  let camTick = $state(0);
  let camTimer;
  let camAvailable = $state(true);
  function openCamera() {
    window.open(`${basePath}/app/printers/${id}/camera`, '_blank');
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
    // more slowly, WebRTC is what "live" is for; this fallback only needs to
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
    if (/run|print/.test(x)) return 'live';
    if (/pause/.test(x)) return 'warn';
    if (/idle|ready|finish|online/.test(x)) return 'ok';
    if (/error|offline|fault|fail/.test(x)) return 'danger';
    return '';
  }
  function stateLabel(s) {
    const x = s.toLowerCase();
    if (/run|print/.test(x)) return 'Printing';
    if (/pause/.test(x)) return 'Paused';
    if (/finish/.test(x)) return 'Finished';
    if (/idle|ready|online/.test(x)) return st?.awaiting_plate_clear ? 'Clear plate' : 'Ready';
    if (/offline/.test(x)) return 'Offline';
    if (/error|fault|fail/.test(x)) return 'Error';
    return s ? s[0].toUpperCase() + s.slice(1).toLowerCase() : '';
  }
  const brandLabel = $derived(printerLabel(meta?.connection_type, meta?.model));
  async function toggleLight() {
    acting = 'light';
    try { await api.chamberLight(id, !st?.chamber_light); await loadStatus(false); }
    catch (e) { error = e.message || 'Could not switch the light.'; }
    finally { acting = null; }
  }

  // ---- printer alerts (Bambu HMS) -----------------------------------------
  // The lookup key is "MMMM_EEEE": module from attr bits 16-31, error from the
  // LOW 16 bits of code. The mask matters, some entries carry high bits whose
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

  // Emergency stop, immediate, NO confirmation (the regular Stop is gated).
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
    // Always: even with no feed of its own, a printer's camera list is where a
    // camera is added (a chamber camera, or any camera for a printer without one).
    a.add('camera');
    if (isBambu && showFilamentPanel && (!live || hasFilamentUnit || hasNozzleInfo)) a.add('filament');
    if (isKlipper) { a.add('klipper-tuning'); if (!live || online) a.add('console'); }
    if (showBedEjection) a.add('eject');
    if (!isKlipper && (!live || online)) a.add('gcode');
    return a;
  }
  const potentialKeys = $derived(availableKeys(false));
  const liveKeys = $derived(availableKeys(true));

  const pageKeys = $derived(new Set(pageList.map((s) => s.key)));
  const showFeed = $derived(pageKeys.has('camera') && camAvailable && online);

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
        // Saving as the default also drops this printer's override, otherwise
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

{#snippet sectionBody(s)}
  {#if s.key === 'header'}
    <div class="phead card">
      <div class="pid">
        {#if printerImage(meta?.connection_type, meta?.model)}
          <div class="pthumb">
            <img src={printerImage(meta?.connection_type, meta?.model)} alt="{brandLabel || 'printer'}" />
          </div>
        {/if}
        <div class="pnames">
          <b>{st?.name || meta?.name || 'Printer'}</b>
          <div class="pmeta">
            {#if brandLabel}<span>{brandLabel}</span>{/if}
            <span>#{id}</span>
            {#if st?.firmware_version}<span>Firmware {st.firmware_version}</span>{/if}
            {#if st?.wifi_signal != null}<span>Wi-Fi {st.wifi_signal} dBm</span>{/if}
          </div>
        </div>
      </div>
    </div>
  {:else if s.key === 'alerts'}
    <!-- Alerts show as banners above the view; in arranging they hold their place. -->
    <div class="card card-pad muted">Printer alerts appear above the printer view when the printer reports a fault.</div>
  {:else if s.key === 'camera'}
    <div class="card card-pad camrow">
      <span class="ah-muted">The live view fills the page behind these panels.</span>
      <PrinterCameraPanel printerId={id} printerName={meta?.name || 'printer'} status={st}
                          connected={online} isBambu={isBambu} tick={camTick} bare
                          onerror={() => (camAvailable = false)} onopen={openCamera} />
    </div>
    <PrinterCameras printerId={id} />
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
{/snippet}

{#snippet pageSection(s)}
  <SectionFrame def={s.def} hidden={s.hidden} unavailable={s.unavailable} {editing}
                first={s.first} last={s.last}
                onmove={(d) => movePage(s.key, d)} ontoggle={() => togglePage(s.key)}>
    {@render sectionBody(s)}
  </SectionFrame>
{/snippet}

<!-- The live view: the camera fills the page behind the glass panels. -->
<div class="feed-bg" aria-hidden="true">
  {#if showFeed}
    <CameraStream printerId={id} tick={camTick} mode="fill" alt="" onerror={() => (camAvailable = false)} />
  {/if}
  <div class="feed-shade"></div>
</div>

{#if loading}
  <div class="card card-pad muted">Connecting to your printer</div>
{:else if error === 'not-found'}
  <div class="card card-pad">
    <h3>Printer not found</h3>
    <p class="muted">This printer may have been removed. <a href="{basePath}/app/printers">Back to printers</a>.</p>
  </div>
{:else if error === 'no-instance'}
  <div class="card card-pad">
    <h3>No instance yet</h3>
    <p class="muted">Provision your instance from the <a href="{basePath}/app">overview</a> first.</p>
  </div>
{:else}
  {#if alerts.length && !editing}
    <div class="alerts" role="alert">
      {#each alerts as a}
        <div class="ah-banner {a.severe ? 'ah-banner--bad' : ''}">
          <Icon name="alert" size={18} />
          <span class="atext">
            {#if a.desc}{a.desc}{:else}Printer alert. Check the printer's screen for details.{/if}
            <span class="acode">HMS {a.code}</span>
          </span>
          <button class="btn btn-sm" onclick={clearHms} disabled={clearingHms}>{clearingHms ? 'Clearing' : 'Clear alerts'}</button>
        </div>
      {/each}
    </div>
  {/if}
  {#if isOffline && printerForLocate}
    <div class="offline-locate ah-banner">
      <Icon name="alert" size={18} />
      <div class="ol-body">
        <div class="ol-head"><b>{printerForLocate.name} is offline</b><span class="muted tiny">Checking the network in case its address changed</span></div>
        <LocatePrinter printer={printerForLocate} auto={true} onrelinked={afterRelink} />
      </div>
    </div>
  {/if}
  {#if error}<div class="ah-banner ah-banner--bad" role="alert">{error}</div>{/if}

  <div class="pv-head">
    <div class="pv-title">
      <div class="eyebrow">{brandLabel || 'Printer'} · {stateLabel(stateStr)}</div>
      <h1>{st?.name || meta?.name || 'Printer'}{#if brandLabel}{' '}<span class="dim">{brandLabel}</span>{/if}</h1>
    </div>
    <div class="toolbar ah-glass" role="toolbar" aria-label="Printer actions">
      {#if isBambu || st?.chamber_light != null}
        <button class="tb" class:on={!!st?.chamber_light} onclick={toggleLight} disabled={!online || !!acting}
                aria-pressed={!!st?.chamber_light}><Icon name="light" size={16} />Light</button>
      {/if}
      <button class="tb" onclick={openCamera}><Icon name="external" size={16} />Camera tab</button>
      {#if !editing}
        <button class="tb" onclick={startEditing}><Icon name="grid" size={16} />Arrange</button>
      {/if}
      <button class="tb" onclick={() => (settingsOpen = true)}><Icon name="cog" size={16} />Settings</button>
      <button class="tb" onclick={toggleConnection} disabled={!!acting}>
        <Icon name="plug" size={16} />{online ? 'Disconnect' : 'Connect'}
      </button>
      <button class="tb" onclick={() => control('refresh-status', 'refresh')} disabled={!!acting}
              aria-label="Refresh live status"><Icon name="refresh" size={16} />{acting === 'refresh' ? 'Refreshing' : 'Refresh'}</button>
      <button class="tb estop" type="button" onclick={emergencyStop}
              disabled={!online || acting === 'estop'}
              data-tip="Stops at once, with no confirmation" data-tip-pos="below">
        <Icon name="stop" size={16} />{acting === 'estop' ? 'Stopping' : 'Emergency stop'}
      </button>
    </div>
  </div>

  <div class="switcher">
    <button class="sw-step" onclick={() => stepPrinter(-1)} disabled={fleet.length < 2} aria-label="Previous printer"><Icon name="back" size={18} /></button>
    <label class="sr-only" for="pv-pick">Switch printer</label>
    <select id="pv-pick" class="input sw-pick" value={String(id)} onchange={(e) => goto(`${basePath}/app/printers/${e.currentTarget.value}`)}>
      {#each fleet as p (p.id)}<option value={String(p.id)}>{p.name}</option>{/each}
      {#if !fleet.length}<option value={String(id)}>{st?.name || meta?.name || 'Printer'}</option>{/if}
    </select>
    <button class="sw-step" onclick={() => stepPrinter(1)} disabled={fleet.length < 2} aria-label="Next printer"><Icon name="arrow" size={18} /></button>
    <span class="chip {tone(stateStr)}">{stateLabel(stateStr)}</span>
    {#if fleet.length}<span class="ah-muted">{fleetIdx + 1} of {fleet.length} printers</span>{/if}
  </div>

  {#if editing}
    <PrinterLayoutBar scope={layoutScope} printerName={st?.name || meta?.name || 'this printer'}
      hasOverride={printerHasOverride} saving={layoutSaving} msg={layoutMsg}
      onscope={(v) => (layoutScope = v)} onsave={saveLayout}
      oncancel={cancelEditing} onreset={resetDraft} />
    <div class="stack arrange">
      {#each pageList as s (s.key)}
        {@render pageSection(s)}
      {/each}
    </div>
  {:else}
    {#if layoutMsg}<div class="ah-banner {layoutMsg.ok ? 'ah-banner--live' : 'ah-banner--bad'}" role="status">{layoutMsg.text}</div>{/if}
    <div class="pv fill">
      <section class="hero" aria-label="Printer view">
        <div class="hero-top">
          <span class="live-pill">
            {#if showFeed}<span class="dot"></span>Live view{:else}No camera{/if}
          </span>
          {#if pageKeys.has('camera')}
            <div class="hero-cam ah-glass">
              <PrinterCameraPanel printerId={id} printerName={meta?.name || 'printer'} status={st}
                                  connected={online} isBambu={isBambu} tick={camTick} bare
                                  onerror={() => (camAvailable = false)} onopen={openCamera} />
            </div>
          {/if}
        </div>
        {#if pageKeys.has('progress')}
          <div class="hero-job">{@render sectionBody({ key: 'progress' })}</div>
        {/if}
      </section>
      <div class="wcol" aria-label="Printer panels">
        {#each pageList.filter((s) => !['header', 'alerts', 'camera', 'progress'].includes(s.key)) as s (s.key)}
          {@render pageSection(s)}
        {/each}
        {#if pageKeys.has('camera')}<PrinterCameras printerId={id} />{/if}
      </div>
    </div>
  {/if}
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
    ondelete={() => goto(basePath + '/app/printers')} />
{/if}

<style>
  /* ---- the live view behind everything ---- */
  .feed-bg { position: fixed; inset: 0; z-index: -1; pointer-events: none; overflow: hidden;
             background: radial-gradient(80% 75% at 46% 42%, var(--ah-page-top) 0%, var(--ah-page-mid) 45%, var(--ah-page) 100%); }
  .feed-bg :global(img), .feed-bg :global(video) { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  .feed-bg :global(.badge), .feed-bg :global(.recon) { display: none; }
  /* Darken the edges where the glass sits so text over the feed stays readable. */
  .feed-shade { position: absolute; inset: 0;
    background: linear-gradient(90deg, rgba(14, 20, 26, 0.6) 0%, rgba(14, 20, 26, 0.2) 24%, rgba(14, 20, 26, 0) 45%, rgba(14, 20, 26, 0.25) 66%, rgba(14, 20, 26, 0.6) 100%),
                linear-gradient(180deg, rgba(14, 20, 26, 0.45) 0%, rgba(14, 20, 26, 0) 22%, rgba(14, 20, 26, 0) 60%, rgba(14, 20, 26, 0.6) 100%); }

  .alerts { display: flex; flex-direction: column; gap: 8px; }
  .atext { flex: 1; }
  .acode { opacity: 0.8; font-size: 12px; margin-left: 6px; white-space: nowrap; }
  .offline-locate { align-items: flex-start; }
  .ol-body { flex: 1; display: flex; flex-direction: column; gap: 10px; min-width: 0; color: var(--ah-text); }
  .ol-head { display: flex; flex-direction: column; color: var(--ah-chip-warn-text); }
  .tiny { font-size: 12px; }

  /* ---- title and actions ---- */
  .pv-head { display: flex; align-items: center; justify-content: space-between; gap: 14px 24px; flex-wrap: wrap; }
  .pv-title { min-width: 0; }
  .pv-title h1 { margin: 2px 0 0; font-size: clamp(26px, 2.4vw, 40px); }
  .pv-title .dim { color: var(--ah-text-3); }
  .toolbar { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px; border-radius: 20px; }
  .tb { display: inline-flex; align-items: center; gap: 7px; height: 36px; padding: 0 12px; border-radius: 12px; border: 1px solid transparent; background: transparent; color: var(--ah-text); font: 600 13px var(--ah-font-sans); cursor: pointer; white-space: nowrap; }
  .tb:hover:not(:disabled) { background: var(--ah-segment); border-color: var(--ah-field-line); }
  .tb:disabled { opacity: 0.45; cursor: default; }
  .tb.on { background: var(--ah-selected); color: var(--ah-text-on-selected); }
  .tb.estop { background: var(--ah-danger-bg); border-color: var(--ah-chip-bad-line); color: var(--ah-chip-bad-text); }

  .switcher { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .sw-step { width: 40px; height: 40px; border-radius: 14px; border: 1px solid var(--ah-field-line); background: var(--ah-glass); backdrop-filter: var(--ah-blur); -webkit-backdrop-filter: var(--ah-blur); color: var(--ah-text); display: grid; place-items: center; cursor: pointer; }
  .sw-step:disabled { opacity: 0.45; cursor: default; }
  .sw-pick { width: min(360px, 100%); min-height: 40px; border-radius: 14px; font-weight: 600; }

  /* ---- the view: a window onto the feed, and the panels beside it ---- */
  .pv { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(0, 1fr); gap: 14px; min-height: 0; }
  .hero { position: relative; display: flex; flex-direction: column; justify-content: space-between; gap: 14px; min-height: 360px;
          border-radius: 28px; border: 1px solid rgba(255, 255, 255, 0.22); padding: 16px; }
  .hero-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
  .live-pill { display: inline-flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 999px; background: rgba(20, 30, 38, 0.6); border: 1px solid var(--ah-glass-line); font-size: 13px; font-weight: 600; }
  .live-pill .dot { width: 8px; height: 8px; border-radius: 4px; background: var(--ah-attention); }
  .hero-cam { border-radius: 14px; padding: 4px 8px; }
  .hero-job :global(.card) { background: rgba(28, 40, 50, 0.55); }
  .wcol { display: flex; flex-direction: column; gap: 14px; min-height: 0; overflow: auto; padding-right: 2px; }
  .stack { display: flex; flex-direction: column; gap: 14px; }
  .wcol > :global(*), .stack > :global(*) { flex-shrink: 0; }

  /* header section (shown while arranging) */
  .phead { display: flex; align-items: center; gap: 14px; padding: 12px 14px; }
  .pid { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .pthumb { width: 54px; height: 54px; flex: 0 0 auto; border-radius: 12px; background: var(--ah-panel); border: 1px solid var(--ah-panel-line); display: grid; place-items: center; overflow: hidden; }
  .pthumb img { width: 100%; height: 100%; object-fit: contain; padding: 3px; }
  .pnames { min-width: 0; display: flex; flex-direction: column; }
  .pmeta { display: flex; gap: 10px; color: var(--ah-text-3); font-size: 12px; flex-wrap: wrap; }
  .camrow { display: flex; align-items: center; justify-content: space-between; gap: 10px; }

  @media (max-width: 1099px) {
    .pv { grid-template-columns: 1fr; }
    .wcol { overflow: visible; }
    .hero { min-height: 50vh; }
  }
</style>
