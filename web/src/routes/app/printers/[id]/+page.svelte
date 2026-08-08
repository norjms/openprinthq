<script module>
  // Cached once per page load, shared across every printer-detail instance.
  let hmsCache = null;
</script>

<script>
  // OpenPrintHQ — per-printer detail & control
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import CameraStream from '$lib/components/CameraStream.svelte';
  import PowerPanel from '$lib/components/PowerPanel.svelte';
  import ControlPanel from '$lib/components/ControlPanel.svelte';
  import AmsPanel from '$lib/components/AmsPanel.svelte';
  import MaintenancePanel from '$lib/components/MaintenancePanel.svelte';
  import KlipperTuning from '$lib/components/KlipperTuning.svelte';
  import GcodeConsole from '$lib/components/GcodeConsole.svelte';
  import KlipperConsole from '$lib/components/KlipperConsole.svelte';
  import EjectPanel from '$lib/components/EjectPanel.svelte';
  import BambuDashboard from '$lib/components/BambuDashboard.svelte';
  import PrinterSettingsModal from '$lib/components/PrinterSettingsModal.svelte';
  import LocatePrinter from '$lib/components/LocatePrinter.svelte';
  import { printerLabel, printerImage } from '$lib/models.js';
  import { recentlyOnline } from '$lib/online.js';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import SectionFrame from '$lib/components/SectionFrame.svelte';
  import PrinterLayoutBar from '$lib/components/PrinterLayoutBar.svelte';
  import { get } from 'svelte/store';
  import { appearance, saveAppearance as persistAppearance } from '$lib/stores/appearance';
  import {
    orderedSections, resolveLayout, hasOverride, layoutFromDraft, mergeLayout
  } from '$lib/printerSections';

  const id = $derived($page.params.id);

  let loading = $state(true);
  let error = $state(null);       // 'not-found' | 'no-instance' | string
  let st = $state(null);          // PrinterStatus
  let meta = $state(null);        // static printer record
  let acting = $state(null);      // name of the control currently in flight
  let confirmStop = $state(false);
  let targets = $state({});       // editable temperature targets, keyed by kind
  let timer = null;
  // Klipper toolhead homed_axes (from the console poll) → drives the Move &
  // control "not homed" prompt so jogs don't fail with a cryptic error.
  let klipperHomed = $state(null);

  // ---- live camera (polled snapshot through the engine gateway) ----
  let camTick = $state(0);
  let camTimer;
  let camAvailable = $state(true);
  // The camera view always opens in its own tab (via window.open so that tab's ✕
  // can close itself). The tab that launched it is never affected.
  function openCamera() {
    window.open(`/app/printers/${id}/camera`, '_blank');
  }

  // ---- per-printer settings (popup) — persisted on the printer record (DB) ----
  let settingsOpen = $state(false);
  const chamberHeaterOn = $derived(!!meta?.chamber_heater);
  const showFilamentPanel = $derived(meta?.show_filament_panel !== false);
  // Bed ejection & continuous printing is opt-in per printer (settings popup).
  const showBedEjection = $derived(!!meta?.show_bed_ejection);

  // ---- offline relocate ----
  const isOffline = $derived(!!st && !st.connected && !recentlyOnline(id));
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
      error = null;
    } catch (e) {
      if (e.status === 404) error = 'not-found';
      else if (e.status === 409) error = 'no-instance';
      else error = e.message ||'engine unreachable';
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    // Status is cheap and wants to feel live, so it stays on a short interval.
    // The camera snapshot is a full JPEG relayed from the printer through the
    // connector and the control-plane, so refreshing it on the same 3s tick
    // meant twenty times the intended traffic for a still image. The live view
    // is what WebRTC is for; this fallback only needs to look current.
    timer = setInterval(() => {
      if (acting) return;
      loadStatus(false);
    }, 3000);
    camTimer = setInterval(() => { if (camAvailable && !acting) camTick++; }, 60000);
    return () => { clearInterval(timer); clearInterval(camTimer); };
  });

  // (Re)load whenever the printer id changes — the route component is reused
  // across client-side nav between printers, so onMount alone wouldn't refresh.
  $effect(() => {
    const _id = id;
    camAvailable = true; camTick = 0;
    api.printer(_id).then((m) => (meta = m)).catch(() => {});
    loadStatus(true);
  });

  // ---- state helpers ----
  const stateStr = $derived((st?.state || (st?.connected ? 'idle' : 'offline')).toString());
  const isPrinting = $derived(/run|print/i.test(stateStr));
  const isPaused = $derived(/pause/i.test(stateStr));
  const hasJob = $derived(isPrinting || isPaused || !!st?.current_print || !!st?.subtask_name);

  function tone(s) {
    const x = s.toLowerCase();
    if (/run|print/.test(x)) return 'primary';
    if (/pause/.test(x)) return 'accent';
    if (/idle|ready|finish|online/.test(x)) return 'ok';
    if (/error|offline|fault|fail/.test(x)) return 'danger';
    return '';
  }

  function fmtEta(mins) {
    if (mins == null || mins <= 0) return null;
    const h = Math.floor(mins / 60), m = Math.round(mins % 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }

  // ---- temperature cards (flat dict: nozzle / nozzle_target, bed / bed_target, …) ----
  const tempCards = $derived.by(() => {
    const t = st?.temperatures || {};
    const kinds = [
      { kind: 'nozzle', label: 'Nozzle', key: 'nozzle', nozzle: 0 },
      { kind: 'nozzle', label: 'Nozzle 2', key: 'nozzle_2', nozzle: 1 },
      { kind: 'bed', label: 'Bed', key: 'bed' },
      { kind: 'chamber', label: 'Chamber', key: 'chamber' }
    ];
    const cards = kinds
      .filter((k) => t[k.key] !== undefined && t[k.key] !== null)
      .map((k) => ({
        ...k,
        current: Number(t[k.key]) || 0,
        target: Number(t[`${k.key}_target`]) || 0,
        heating: !!t[`${k.key}_heating`],
        settable: k.settable !== false
      }));
    // If the printer is configured to have a chamber heater but the engine isn't
    // (yet) reporting a chamber reading, still offer the control below Bed.
    if (chamberHeaterOn && !cards.some((c) => c.key === 'chamber')) {
      cards.push({
        kind: 'chamber', label: 'Chamber', key: 'chamber',
        current: Number(t.chamber) || 0,
        target: Number(t.chamber_target) || 0,
        heating: !!t.chamber_heating,
        settable: true
      });
    }
    return cards;
  });

  // ---- printer alerts (Bambu HMS errors) ----
  // The engine hands us raw HMS entries {code:"0x4038", attr:<int>, severity}.
  // The lookup key for the description table is the short code "MMMM_EEEE":
  // module from attr bits 16-31, error from the LOW 16 BITS of code. Masking to
  // 0xFFFF matters: some entries carry high bits (e.g. 0x20006) whose real error
  // nibble is the low half (0x0006). Without the mask we'd render "20006" and
  // miss the catalogue key.
  function hmsErrNum(e) {
    return (parseInt(String(e?.code ?? '').replace(/^0x/i, ''), 16) || 0) & 0xffff;
  }
  function hmsShortCode(e) {
    const attr = Number(e?.attr) || 0;
    const moduleHex = ((attr >>> 16) & 0xffff).toString(16).toUpperCase().padStart(4, '0');
    const errHex = hmsErrNum(e).toString(16).toUpperCase().padStart(4, '0');
    return `${moduleHex}_${errHex}`;
  }

  // HMS description dictionary (short_code -> text), served by the control-plane
  // and cached module-wide so it's fetched at most once across printer pages.
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
      // Only genuine faults surface as alerts. Bambu reserves error nibbles
      // >= 0x4000 for real HMS faults; lower values are status/phase codes the
      // firmware emits during normal operation (e.g. 0x0006) and must not be
      // shown as warnings — otherwise a healthy printer looks like it's erroring.
      .filter((e) => hmsErrNum(e) >= 0x4000)
      .map((e) => {
        const code = hmsShortCode(e);
        const sev = Number(e.severity) || 0;
        return {
          code,
          desc: hmsMap ? hmsMap[code] : undefined,
          // Bambu severity: 1 = fatal, 2 = serious → red; everything else amber.
          severe: sev === 1 || sev === 2
        };
      })
  );

  // ---- clear HMS flags (acknowledge/dismiss, like tapping the printer screen) ----
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

  // ---- loaded filament (Bambu AMS units + external spool) ----
  const hexColor = (c) => (c ? '#' + String(c).slice(0, 6) : '');
  const loadedFilament = $derived.by(() => {
    const out = [];
    for (const [i, u] of (st?.ams || []).entries()) {
      for (const [j, t] of (u?.tray || []).entries()) {
        // Bambu tray id encoding for load: ams_id * 4 + slot_id (0-15).
        if (t?.tray_type) out.push({ where: `AMS ${i + 1}·${j + 1}`, color: hexColor(t.tray_color), type: t.tray_type, remain: t.remain, trayId: i * 4 + j });
      }
    }
    const vtArr = Array.isArray(st?.vt_tray) ? st.vt_tray : (st?.vt_tray ? [st.vt_tray] : []);
    for (const vt of vtArr) {
      // 254 = external spool / Ext-L on dual-nozzle machines.
      if (vt?.tray_type) out.push({ where: 'External', color: hexColor(vt.tray_color), type: vt.tray_type, remain: vt.remain, trayId: 254 });
    }
    return out;
  });

  // ---- AMS load / unload (Bambu; live hardware action, confirm-gated) ----
  let amsBusy = $state(false);
  let confirmLoad = $state(null);   // trayId pending confirm
  let confirmUnload = $state(false);
  let amsMsg = $state(null);

  async function amsLoad(trayId) {
    amsBusy = true; amsMsg = null;
    try {
      await api.amsLoad(id, trayId);
      amsMsg = { kind: 'ok', text: 'Load command sent.' };
      await loadStatus(false);
    } catch (e) {
      amsMsg = { kind: 'err', text: e.message || 'load failed' };
    } finally {
      amsBusy = false; confirmLoad = null;
    }
  }
  async function amsUnload() {
    amsBusy = true; amsMsg = null;
    try {
      await api.amsUnload(id);
      amsMsg = { kind: 'ok', text: 'Unload command sent.' };
      await loadStatus(false);
    } catch (e) {
      amsMsg = { kind: 'err', text: e.message || 'unload failed' };
    } finally {
      amsBusy = false; confirmUnload = false;
    }
  }

  // ---- AMS filament backup (Bambu; auto-switch to backup spool on runout) ----
  const hasAms = $derived((st?.ams || []).length > 0);
  // A real multi-material unit or a loaded external spool — i.e. something worth
  // showing. When neither is present the filament panel is hidden entirely
  // (no "nothing detected" placeholder), per the vendor-agnostic panel rules.
  const hasFilamentUnit = $derived.by(() => {
    if ((st?.ams || []).length > 0) return true;
    const vt = Array.isArray(st?.vt_tray) ? st.vt_tray : (st?.vt_tray ? [st.vt_tray] : []);
    return vt.some((t) => t?.tray_type);
  });
  const isKlipper = $derived((meta?.connection_type || '').toLowerCase() === 'klipper');
  // Bambu printers get the full skinned dashboard; others keep the classic layout.
  const isBambu = $derived((meta?.connection_type || 'bambu').toLowerCase() === 'bambu');
  let amsBackupBusy = $state(false);
  async function toggleAmsBackup() {
    amsBackupBusy = true;
    try { await api.amsBackup(id, !st?.ams_filament_backup); await loadStatus(false); }
    catch (e) { error = e.message || 'could not toggle backup'; }
    finally { amsBackupBusy = false; }
  }

  // ---- AMS units + per-unit filament drying (Bambu) ----
  // Represent the actual hardware: AMS 2 Pro (n3f, 4-slot, dries), AMS HT (n3s,
  // single-spool dryer), original AMS (no heater), AMS Lite. Drying is per-unit
  // (each has its own humidity + heater) and only offered on drying-capable units.
  const AMS_TYPES = { n3f: 'AMS 2 Pro', n3s: 'AMS HT', ams: 'AMS', f1: 'AMS Lite', ams_lite: 'AMS Lite' };
  function amsTypeName(u) {
    if (u?.is_ams_ht) return 'AMS HT';
    return AMS_TYPES[String(u?.module_type || '').toLowerCase()] || 'AMS';
  }
  const amsUnits = $derived.by(() =>
    (st?.ams || []).map((u, i) => {
      const loaded = (u.tray || []).find((t) => t?.tray_type);
      const mt = String(u.module_type || '').toLowerCase();
      return {
        id: u.id, num: i + 1, type: amsTypeName(u),
        humidity: (u.humidity != null && u.humidity !== '') ? Number(u.humidity) : null,
        canDry: !!st?.supports_drying && (u.is_ams_ht || ['n3f', 'n3s'].includes(mt)),
        drying: (Number(u.dry_status) || 0) !== 0,
        dryFilament: u.dry_filament || '',
        dryTarget: u.dry_target_temp || null,
        suggestFilament: u.dry_filament || loaded?.tray_type || ''
      };
    })
  );

  // Per-unit drying form inputs (keyed by ams id); updated via handlers so we
  // never mutate state during render.
  let dryInputs = $state({});
  function dryVal(u, key, dflt) { return dryInputs[u.id]?.[key] ?? dflt; }
  function setDry(amsId, key, val) {
    dryInputs = { ...dryInputs, [amsId]: { ...(dryInputs[amsId] || {}), [key]: val } };
  }
  let dryBusyId = $state(null);
  let dryMsg = $state(null);
  async function startDrying(u) {
    dryBusyId = u.id; dryMsg = null;
    try {
      await api.dryingStart(id, {
        ams_id: u.id,
        temp: Number(dryVal(u, 'temp', u.dryTarget || 45)) || 45,
        duration: Number(dryVal(u, 'duration', 4)) || 4,
        filament: dryVal(u, 'filament', u.suggestFilament) || ''
      });
      dryMsg = { kind: 'ok', text: `Drying started on ${u.type} #${u.num}.` };
      await loadStatus(false);
    } catch (e) { dryMsg = { kind: 'err', text: e.message || 'could not start drying' }; }
    finally { dryBusyId = null; }
  }
  async function stopDrying(u) {
    dryBusyId = u.id; dryMsg = null;
    try { await api.dryingStop(id, u.id); dryMsg = { kind: 'ok', text: 'Drying stopped.' }; await loadStatus(false); }
    catch (e) { dryMsg = { kind: 'err', text: e.message || 'could not stop drying' }; }
    finally { dryBusyId = null; }
  }

  // ---- actions ----
  async function control(action, label) {
    acting = label;
    try {
      await api.printerAction(id, action);
      await api.printerAction(id, 'refresh-status').catch(() => {});
      await loadStatus(false);
    } catch (e) {
      error = e.message ||`${label} failed`;
    } finally {
      acting = null; confirmStop = false;
    }
  }

  async function setTemp(kind, key, nozzle) {
    const v = targets[key];
    if (v === undefined || v === '') return;
    acting = `set-${key}`;
    try {
      await api.setTemp(id, kind, Number(v), nozzle);
      targets[key] = '';
      await loadStatus(false);
    } catch (e) {
      error = e.message ||'set temperature failed';
    } finally {
      acting = null;
    }
  }

  async function toggleConnection() {
    await control(st?.connected ? 'disconnect' : 'connect', st?.connected ? 'disconnect' : 'connect');
  }

  // ---- arrangeable sections ------------------------------------------------
  // The page is a stack of independent sections. Each user can reorder them and
  // hide the ones they don't want, either as a default for every printer or as
  // an override for this one. The saved layout lives in the per-user appearance
  // config (same place as the left-nav prefs), so it follows them between
  // browsers and never touches the printer record.
  let editing = $state(false);
  let layoutScope = $state('global');    // 'global' = every printer | 'printer'
  let layoutSaving = $state(false);
  let layoutMsg = $state(null);
  // Working copies while arranging; null when not editing.
  let draftPage = $state(null);
  let draftDash = $state(null);

  const variant = $derived(isBambu ? 'bambu' : 'classic');
  const savedLayout = $derived(resolveLayout($appearance?.printerSections, id));
  const printerHasOverride = $derived(hasOverride($appearance?.printerSections, id));

  const hasFans = $derived(
    [st?.cooling_fan_speed, st?.big_fan1_speed, st?.big_fan2_speed].some((v) => v != null)
  );
  const hasNozzleRack = $derived((st?.nozzle_rack || []).length > 0);

  // Which sections belong to this printer at all (`live: false` — used while
  // arranging, so an offline printer or an unplugged AMS can still be placed)
  // versus which have something to draw this second (`live: true` — used to
  // render). Anything structurally irrelevant (Klipper tuning on a Bambu, the
  // dashboard blocks on a Klipper machine) is absent from both.
  function availableKeys(live) {
    const a = new Set(['power', 'maintenance']);
    if (isBambu) {
      a.add('bambu-dashboard');
      a.add('bambu-header'); a.add('bambu-status'); a.add('bambu-temps');
      a.add('bambu-controls'); a.add('bambu-footer');
      if (!live || hasFans) a.add('bambu-fans');
      if (!live || hasNozzleRack) a.add('bambu-nozzles');
      if (!live || hasFilamentUnit) a.add('bambu-filaments');
    } else {
      a.add('title'); a.add('job'); a.add('temps');
    }
    if (!live || alerts.length) a.add('alerts');
    if (!live || st?.connected) a.add('move');
    if (showFilamentPanel && (!live || hasFilamentUnit)) a.add('filament');
    if (isKlipper) a.add('klipper-tuning');
    if (showBedEjection) a.add('eject');
    if (!isKlipper && (!live || st?.connected)) a.add('gcode');
    if (!live || camAvailable || st?.cover_url) a.add('camera');
    return a;
  }
  const potentialKeys = $derived(availableKeys(false));
  const liveKeys = $derived(availableKeys(true));

  // The page-level stack. While arranging, hidden sections stay in the list as
  // stubs so they can be brought back; otherwise they're dropped outright.
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

  // Current job and Temperatures are half-width cards that sit side by side when
  // they end up adjacent, exactly as they always have. A half with no partner —
  // or any section while arranging — takes the full width instead of leaving a
  // hole beside it.
  const pageRows = $derived.by(() => {
    const rows = [];
    for (let i = 0; i < pageList.length; i++) {
      const a = pageList[i], b = pageList[i + 1];
      if (!editing && a.def.width === 'half' && b && b.def.width === 'half') {
        rows.push([a, b]); i++;
      } else {
        rows.push([a]);
      }
    }
    return rows;
  });

  // The blocks inside the Bambu dashboard card, ordered the same way.
  const dashList = $derived.by(() => {
    const base = editing
      ? (draftDash || [])
      : orderedSections(savedLayout.layout, { variant, scope: 'dashboard', available: liveKeys });
    return base.map((s) => ({ ...s, unavailable: editing && !liveKeys.has(s.key) }));
  });

  function seedDraft(layout) {
    const opts = { variant, available: potentialKeys };
    draftPage = orderedSections(layout, { ...opts, scope: 'page' });
    draftDash = orderedSections(layout, { ...opts, scope: 'dashboard' });
  }
  function startEditing() {
    seedDraft(savedLayout.layout);
    layoutScope = savedLayout.scope;
    layoutMsg = null;
    editing = true;
  }
  function cancelEditing() {
    editing = false; draftPage = null; draftDash = null; layoutMsg = null;
  }
  function resetDraft() {
    seedDraft({ order: [], hidden: [] });
    layoutMsg = null;
  }

  function moveIn(list, key, dir) {
    const i = list.findIndex((s) => s.key === key);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= list.length) return list;
    const next = [...list];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  }
  function toggleIn(list, key) {
    return list.map((s) => (s.key === key && !s.def?.lockHide ? { ...s, hidden: !s.hidden } : s));
  }
  const movePage = (key, dir) => (draftPage = moveIn(draftPage || [], key, dir));
  const togglePage = (key) => (draftPage = toggleIn(draftPage || [], key));
  const moveDash = (key, dir) => (draftDash = moveIn(draftDash || [], key, dir));
  const toggleDash = (key) => (draftDash = toggleIn(draftDash || [], key));

  async function saveLayout() {
    layoutSaving = true; layoutMsg = null;
    try {
      const cfg = get(appearance);
      const ps = { ...(cfg.printerSections || {}) };
      const byPrinter = { ...(ps.byPrinter || {}) };
      // Merge onto the layout we started from so section keys that weren't on
      // screen (the Bambu blocks while arranging a Klipper printer, say) survive.
      const merged = mergeLayout(
        savedLayout.layout,
        layoutFromDraft(draftPage || []),
        layoutFromDraft(draftDash || [])
      );
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
      editing = false; draftPage = null; draftDash = null;
      layoutMsg = { ok: true, text: layoutScope === 'printer'
        ? 'Layout saved for this printer.'
        : 'Layout saved as your default for every printer.' };
    } catch (e) {
      layoutMsg = { ok: false, text: e?.message || 'Could not save the layout.' };
    } finally { layoutSaving = false; }
  }

  // Emergency stop — immediate, NO confirmation (the regular Stop is confirm-gated).
  // Klipper gets a true firmware halt (M112); others get an immediate print-stop.
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
</script>

<PageTitle page={st?.name || meta?.name || 'Printer'} />

<!-- One arrangeable section of the page. The frame is edit-mode chrome only:
     outside edit mode it adds no wrapper element at all, so an unarranged page
     renders exactly the markup it always did. -->
{#snippet pageSection(s)}
  <SectionFrame def={s.def} hidden={s.hidden} unavailable={s.unavailable} {editing}
                first={s.first} last={s.last}
                onmove={(d) => movePage(s.key, d)} ontoggle={() => togglePage(s.key)}>
    {#if s.key === 'bambu-dashboard'}
      <BambuDashboard printerId={id} status={st} meta={meta} refresh={() => loadStatus(false)}
        oncamera={openCamera} onsettings={() => (settingsOpen = true)}
        sections={dashList} {editing} onmove={moveDash} ontoggle={toggleDash} />

    {:else if s.key === 'title'}
      <div class="title">
        <div class="title-id">
          {#if printerImage(meta?.connection_type, meta?.model)}
            <div class="pthumb"><img src={printerImage(meta?.connection_type, meta?.model)} alt="{printerLabel(meta?.connection_type, meta?.model) || 'printer'}" /></div>
          {/if}
          <div>
            <h1>{st?.name || meta?.name || 'Printer'}</h1>
            <div class="meta mono">
              {#if printerLabel(meta?.connection_type, meta?.model)}<span>{printerLabel(meta?.connection_type, meta?.model)}</span>{/if}
              <span>#{id}</span>
            </div>
          </div>
        </div>
        <div class="actions">
          <div class="flex gap center">
            <span class="chip {tone(stateStr)}">{stateStr}</span>
            <button class="btn btn-ghost btn-sm" data-tip={st?.connected ? 'Disconnect from the printer' : 'Connect to the printer'} aria-label={st?.connected ? 'Disconnect from the printer' : 'Connect to the printer'} onclick={toggleConnection} disabled={!!acting}>
              {st?.connected ? 'Disconnect' : 'Connect'}
            </button>
            <button class="estop" type="button" onclick={emergencyStop} disabled={!st?.connected || acting === 'estop'}
                    data-tip="Emergency stop — immediate, no confirmation" data-tip-pos="below" aria-label="Emergency stop — immediate, no confirmation">
              <span class="estop-oct"><span class="estop-txt">{acting === 'estop' ? '…' : 'STOP'}</span></span>
            </button>
          </div>
          <button class="btn btn-ghost btn-sm gear" data-tip="Printer settings" aria-label="Printer settings" onclick={() => (settingsOpen = true)}>
            <span aria-hidden="true">⚙</span> Settings
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

    {:else if s.key === 'job'}
      <div class="card card-pad job">
        <h3>Current job</h3>
        {#if hasJob}
          <div class="jobname">{st?.subtask_name || st?.gcode_file || st?.current_print || 'Printing'}</div>
          <div class="bar"><div class="fill" style="width:{Math.min(100, Math.max(0, st?.progress || 0))}%"></div></div>
          <div class="jobmeta mono">
            <span>{Math.round(st?.progress || 0)}%</span>
            {#if st?.layer_num != null && st?.total_layers}<span>layer {st.layer_num}/{st.total_layers}</span>{/if}
            {#if fmtEta(st?.remaining_time)}<span>~{fmtEta(st?.remaining_time)} left</span>{/if}
          </div>
          <div class="controls flex gap">
            {#if isPrinting}
              <button class="btn btn-ghost" onclick={() => control('print/pause', 'pause')} disabled={!!acting}>
                {acting === 'pause' ? 'Pausing…' : 'Pause'}
              </button>
            {/if}
            {#if isPaused}
              <button class="btn btn-primary" onclick={() => control('print/resume', 'resume')} disabled={!!acting}>
                {acting === 'resume' ? 'Resuming…' : 'Resume'}
              </button>
            {/if}
            {#if isPrinting || isPaused}
              {#if confirmStop}
                <button class="btn btn-danger" onclick={() => control('print/stop', 'stop')} disabled={!!acting}>
                  {acting === 'stop' ? 'Stopping…' : 'Confirm stop'}
                </button>
                <button class="btn btn-ghost" onclick={() => (confirmStop = false)} disabled={!!acting}>Cancel</button>
              {:else}
                <button class="btn btn-ghost danger-text" onclick={() => (confirmStop = true)} disabled={!!acting}>Stop</button>
              {/if}
            {/if}
          </div>
        {:else}
          <p class="muted">No active print. {st?.connected ? 'Printer is idle and ready.' : 'Printer is offline.'}</p>
        {/if}
      </div>

    {:else if s.key === 'temps'}
      <div class="card card-pad temps">
        <h3>Temperatures</h3>
        {#if tempCards.length === 0}
          <p class="muted">No temperature data{st?.connected ? '' : ' — printer offline'}.</p>
        {:else}
          {#each tempCards as c}
            <div class="temp">
              <div class="tinfo">
                <span class="tlabel">{c.label}</span>
                <span class="tval mono">
                  {c.current.toFixed(1)}°<span class="tgt"> / {c.target || '—'}{c.target ? '°' : ''}</span>
                  {#if c.heating}<span class="chip accent heat">heating</span>{/if}
                </span>
              </div>
              {#if c.settable}
                <div class="tset">
                  <input class="input" type="number" min="0" placeholder="target °C"
                         bind:value={targets[c.key]} />
                  <button class="btn btn-ghost btn-sm" data-tip={`Set ${c.label.toLowerCase()} target`} aria-label={`Set ${c.label} target temperature`} onclick={() => setTemp(c.kind, c.key, c.nozzle)}
                          disabled={acting === `set-${c.key}`}>Set</button>
                  {#if c.target}
                    <button class="btn btn-ghost btn-sm" data-tip={`Turn ${c.label.toLowerCase()} heater off`} aria-label={`Turn ${c.label} heater off`} onclick={() => { targets[c.key] = 0; setTemp(c.kind, c.key, c.nozzle); }}
                            disabled={acting === `set-${c.key}`}>Off</button>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>

    {:else if s.key === 'move'}
      <div id="move"><span id="temps"></span>
      {#if st?.connected}
        {#if isKlipper}
          <div class="move-console">
            <ControlPanel printerId={id} status={st} refresh={() => loadStatus(false)} kind={meta?.connection_type} homedAxes={klipperHomed} />
            <KlipperConsole printerId={id} connected={st?.connected} printing={isPrinting} onhomed={(h) => (klipperHomed = h)} />
          </div>
        {:else}
          <ControlPanel printerId={id} status={st} refresh={() => loadStatus(false)} kind={meta?.connection_type} />
        {/if}
      {/if}
      </div>

    {:else if s.key === 'filament'}
      <AmsPanel printerId={id} status={st} refresh={() => loadStatus(false)} />
      {#if hasAms}
        <label class="opt bkp standalone">
          <input type="checkbox" checked={st?.ams_filament_backup} onchange={toggleAmsBackup} disabled={amsBackupBusy} />
          <span>Filament backup — auto-switch to another spool of the same type when one runs out</span>
        </label>
      {/if}

    {:else if s.key === 'power'}
      <PowerPanel printerId={id} />

    {:else if s.key === 'maintenance'}
      <MaintenancePanel printerId={id} />

    {:else if s.key === 'klipper-tuning'}
      <KlipperTuning printerId={id} connected={st?.connected} printing={isPrinting} />

    {:else if s.key === 'eject'}
      <EjectPanel printerId={id} connected={st?.connected} kind={meta?.connection_type} status={st} />

    {:else if s.key === 'gcode'}
      <GcodeConsole printerId={id} kind={meta?.connection_type} printing={isPrinting} />

    {:else if s.key === 'camera'}
      {#if camAvailable}
        <div class="card card-pad cover" id="camera">
          <h3>Camera</h3>
          <CameraStream printerId={id} tick={camTick} alt="{meta?.name || 'printer'} camera live view" mode="detail"
               onerror={() => (camAvailable = false)} onclick={openCamera} title="Open camera in a new tab" />
        </div>
      {:else if st?.cover_url}
        <div class="card card-pad cover">
          <h3>Preview</h3>
          <img src={st.cover_url} alt="print preview" />
        </div>
      {/if}
    {/if}
  </SectionFrame>
{/snippet}

<div class="head">
  <a href="/app/printers" class="btn btn-ghost btn-sm" data-tip="Back to all printers" aria-label="Back to all printers">← Printers</a>
  <div class="head-actions">
    {#if !editing && !loading && !error}
      <button class="btn btn-ghost btn-sm" data-tip="Reorder or hide the sections on this page" aria-label="Arrange sections" onclick={startEditing}>
        <span aria-hidden="true">⇅</span> Arrange
      </button>
    {/if}
    <button class="btn btn-ghost btn-sm" data-tip="Refresh live status now" aria-label="Refresh live status" onclick={() => control('refresh-status', 'refresh')} disabled={!!acting}>
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

  {#each pageRows as row (row[0].key)}
    {#if row.length === 2}
      <div class="cols">
        {@render pageSection(row[0])}
        {@render pageSection(row[1])}
      </div>
    {:else}
      {@render pageSection(row[0])}
    {/if}
  {/each}
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
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; }
  .banner { margin: 0 0 1rem; }
  .title { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; margin-bottom: 1.4rem; }
  .title h1 { margin: 0 0 0.3rem; }
  .title-id { display: flex; align-items: flex-start; gap: 0.9rem; }
  .pthumb { width: 64px; height: 64px; flex: 0 0 auto; border-radius: var(--radius-sm); background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); display: grid; place-items: center; overflow: hidden; }
  .pthumb img { width: 100%; height: 100%; object-fit: contain; padding: 3px; }
  .actions { display: flex; flex-direction: column; align-items: flex-end; gap: 0.5rem; }
  .gear { align-self: flex-end; }
  /* Emergency stop — octagon "stop sign", immediate (no confirmation). */
  .estop { background: none; border: 0; padding: 0; cursor: pointer; }
  .estop-oct {
    --oct: polygon(30% 0, 70% 0, 100% 30%, 100% 70%, 70% 100%, 30% 100%, 0 70%, 0 30%);
    position: relative; display: grid; place-items: center; width: 50px; height: 50px;
    background: #fff;                         /* white stop-sign ring */
    clip-path: var(--oct);
    filter: drop-shadow(0 3px 8px rgba(229,52,47,0.5));
    transition: transform 0.12s ease, filter 0.12s ease;
    animation: estopPulse 2.6s ease-in-out infinite;
  }
  .estop-oct::before {
    content: ''; position: absolute; inset: 3px; clip-path: var(--oct);
    background: radial-gradient(circle at 50% 33%, #ff6a5f 0%, #e5342f 52%, #b81c17 100%);
  }
  .estop-txt { position: relative; z-index: 1; color: #fff; font-weight: 900; font-size: 0.62rem;
    letter-spacing: 0.04em; text-shadow: 0 1px 2px rgba(0,0,0,0.4); }
  .estop:hover:not(:disabled) .estop-oct { transform: scale(1.06); filter: drop-shadow(0 4px 12px rgba(229,52,47,0.78)); animation: none; }
  .estop:active:not(:disabled) .estop-oct { transform: scale(0.95); }
  .estop:focus-visible .estop-oct { outline: 2px solid var(--ophq-primary); outline-offset: 3px; }
  .estop:disabled { cursor: default; opacity: 0.4; }
  .estop:disabled .estop-oct { animation: none; }
  @keyframes estopPulse {
    0%, 100% { filter: drop-shadow(0 3px 8px rgba(229,52,47,0.4)); }
    50% { filter: drop-shadow(0 3px 14px rgba(229,52,47,0.85)); }
  }
  /* Klipper Move & control + realtime console side by side. */
  .move-console { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 1.2rem; align-items: stretch; }
  .move-console > :global(.control) { margin-top: 1.2rem; }
  @media (max-width: 1024px) { .move-console { grid-template-columns: 1fr; } }
  .offline-locate { margin: 0 0 1.2rem; padding: 0.9rem 1rem; border: 1px solid rgba(255,176,32,0.35); background: rgba(255,176,32,0.06); border-radius: var(--radius-sm); display: flex; flex-direction: column; gap: 0.7rem; }
  .ol-head { display: flex; flex-direction: column; gap: 0.15rem; }
  .ol-title { font-weight: 600; color: var(--ophq-warn); }
  .meta { display: flex; gap: 0.7rem; color: var(--ophq-muted); font-size: 0.85rem; flex-wrap: wrap; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
  @media (max-width: 720px) { .cols { grid-template-columns: 1fr; } }
  .job h3, .temps h3, .cover h3 { margin: 0 0 0.9rem; font-size: 1.05rem; }
  .jobname { font-weight: 600; margin-bottom: 0.7rem; }
  .bar { height: 10px; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; }
  .fill { height: 100%; background: linear-gradient(90deg, var(--ophq-primary), var(--ophq-primary-2)); transition: width 0.4s ease; }
  .jobmeta { display: flex; gap: 1rem; margin-top: 0.6rem; color: var(--ophq-text-2); font-size: 0.85rem; }
  .controls { margin-top: 1.1rem; }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .btn-danger:hover { background: #ff7280; color: #fff; }
  .danger-text { color: var(--ophq-danger); border-color: rgba(255,92,108,0.35); }
  .danger-text:hover { border-color: var(--ophq-danger); color: var(--ophq-danger); }
  .temp { padding: 0.7rem 0; border-bottom: 1px solid var(--ophq-border-soft); }
  .temp:last-child { border-bottom: 0; }
  .tinfo { display: flex; justify-content: space-between; align-items: center; }
  .tlabel { font-weight: 600; }
  .tval { color: var(--ophq-text); font-size: 0.95rem; }
  .tgt { color: var(--ophq-muted); }
  .heat { margin-left: 0.5rem; }
  .tset { display: flex; gap: 0.5rem; margin-top: 0.55rem; }
  .tset .input { max-width: 130px; }
  .alerts { display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 1.2rem; }
  .alert { display: flex; align-items: center; gap: 0.6rem; padding: 0.7rem 1rem; border-radius: var(--radius-sm); font-size: 0.9rem; border: 1px solid rgba(245,166,35,0.35); background: rgba(245,166,35,0.08); color: var(--ophq-warn); }
  .alert.sev { border-color: rgba(255,92,108,0.35); background: rgba(255,92,108,0.08); color: var(--ophq-danger); }
  .alert .ai { font-size: 1rem; }
  .alert .atext { flex: 1; }
  .alert .acode { opacity: 0.7; font-size: 0.8rem; margin-left: 0.4rem; white-space: nowrap; }
  .alerts .clr { align-self: flex-end; }
  .filament { margin-top: 1.2rem; }
  .filament h3 { margin: 0; font-size: 1.05rem; }
  .fils { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-top: 0.9rem; }
  .fil { display: flex; align-items: center; gap: 0.5rem; padding: 0.4rem 0.5rem 0.4rem 0.7rem; border: 1px solid var(--ophq-border); border-radius: 999px; background: var(--ophq-surface-2); }
  .fil .sw { width: 14px; height: 14px; border-radius: 50%; border: 1px solid rgba(255,255,255,0.15); flex-shrink: 0; }
  .fil .ft { font-weight: 600; font-size: 0.88rem; }
  .fil .fw { font-size: 0.78rem; }
  .btn-xs { padding: 0.15rem 0.5rem; font-size: 0.72rem; border-radius: 999px; line-height: 1.4; }
  .fil .load { opacity: 0.85; }
  .fil .load:hover { opacity: 1; }
  .tiny { font-size: 0.8rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; margin: 0.7rem 0 0; }
  .opt { display: flex; align-items: center; gap: 0.5rem; font-size: 0.86rem; color: var(--ophq-text-2); cursor: pointer; }
  .opt input { width: auto; accent-color: var(--ophq-primary); }
  .opt.bkp { margin-top: 0.9rem; padding-top: 0.8rem; border-top: 1px solid var(--ophq-border-soft); }
  .opt.bkp.standalone { margin: 0.7rem 0.2rem 0; padding: 0; border: none; }
  .amscard { margin-top: 1.2rem; }
  .amscard h3 { margin: 0 0 0.8rem; font-size: 1.05rem; }
  .amslist { display: flex; flex-direction: column; gap: 0.6rem; }
  .amsu { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.7rem 0.9rem; background: var(--ophq-surface); }
  .amsu-hd { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.55rem; }
  .amst { font-weight: 600; font-size: 0.95rem; }
  .amsmeta { display: flex; align-items: center; gap: 0.6rem; }
  .hum { font-size: 0.82rem; color: var(--ophq-text-2); }
  .dryactive { display: flex; align-items: center; justify-content: space-between; gap: 0.8rem; font-size: 0.88rem; }
  .dryrow { display: flex; align-items: center; gap: 0.9rem; flex-wrap: wrap; }
  .dryrow label { display: flex; align-items: center; gap: 0.35rem; font-size: 0.84rem; color: var(--ophq-text-2); }
  .input.sm { max-width: 80px; }
  .input.xs { max-width: 72px; padding: 0.35rem 0.5rem; font-size: 0.85rem; }
  .nodry { margin: 0; }
  .chip.accent { color: var(--ophq-accent); border-color: rgba(255,176,32,0.35); background: rgba(255,176,32,0.08); }
  .cover { margin-top: 1.2rem; }
  .cover img { width: 100%; max-width: 640px; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); display: block; }
  .cover img.cam { background: var(--ophq-bg-2); aspect-ratio: 16 / 9; object-fit: contain; }
  .cover img.zoomable { cursor: zoom-in; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
</style>
