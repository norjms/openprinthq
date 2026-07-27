<script>
  // OpenPrintHQ — Orca/Bambu-Studio-style AMS panel: individual units, the
  // spools in each slot, external spools, humidity + dryer, drying, load/unload,
  // and filament reread (RFID re-scan). SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';

  let { printerId, status, refresh } = $props();

  // Vendor-agnostic panel label — the box is the same concept across brands
  // (Bambu AMS, Creality CFS, Prusa MMU…); the specific model shows as a sub-name.
  const PANEL_LABEL = 'Multi-material unit';

  // Per-vendor module codes → the model shown beneath the panel label.
  const AMS_TYPES = {
    n3f: 'AMS 2 Pro', n3s: 'AMS HT', ams: 'AMS', f1: 'AMS Lite', ams_lite: 'AMS Lite',
    cfs: 'CFS', creality_cfs: 'CFS',
    mmu: 'MMU', mmu2: 'MMU2', mmu3: 'MMU3',
    ace: 'ACE Pro', ace_pro: 'ACE Pro'
  };
  function typeName(u) {
    if (u?.is_ams_ht) return 'AMS HT';
    const mt = String(u?.module_type || '').toLowerCase();
    if (AMS_TYPES[mt]) return AMS_TYPES[mt];
    // Unknown vendor code: show it uppercased rather than assuming "AMS".
    return u?.module_type ? String(u.module_type).toUpperCase() : 'Unit';
  }
  const hex = (c) => (c ? (String(c).startsWith('#') ? c : '#' + String(c).slice(0, 6)) : '');
  const supportsDrying = $derived(!!status?.supports_chamber_heater || !!status?.supports_drying);

  const units = $derived.by(() =>
    (status?.ams || []).map((u, i) => {
      const mt = String(u.module_type || '').toLowerCase();
      const slots = (u.tray || []).map((t, j) => ({
        slotId: j,
        trayId: i * 4 + j,
        material: t?.tray_type || '',
        color: hex(t?.tray_color),
        remain: (t?.remain != null && t.remain >= 0) ? t.remain : null,
        empty: !t?.tray_type
      }));
      return {
        id: u.id, num: i + 1, type: typeName(u),
        humidity: (u.humidity != null && u.humidity !== '') ? Number(u.humidity) : null,
        canDry: supportsDrying && (u.is_ams_ht || ['n3f', 'n3s'].includes(mt)),
        drying: (Number(u.dry_status) || 0) !== 0,
        dryFilament: u.dry_filament || '',
        dryTarget: u.dry_target_temp || null,
        suggest: u.dry_filament || (u.tray || []).find((t) => t?.tray_type)?.tray_type || '',
        slots
      };
    })
  );
  const external = $derived.by(() => {
    const vt = Array.isArray(status?.vt_tray) ? status.vt_tray : (status?.vt_tray ? [status.vt_tray] : []);
    return vt.filter((t) => t?.tray_type).map((t) => ({ material: t.tray_type, color: hex(t.tray_color), remain: t.remain }));
  });

  // Sub-name shown under the vendor-agnostic label: the specific model(s) present
  // (e.g. "AMS 2 Pro", or "AMS 2 Pro · AMS HT"), falling back to external spool.
  const subName = $derived.by(() => {
    const names = [...new Set(units.map((u) => u.type).filter(Boolean))];
    if (names.length) return names.join(' · ');
    return external.length ? 'External spool' : '';
  });

  let busy = $state(null);
  let msg = $state(null);
  let dryIn = $state({});
  function dv(u, k, d) { return dryIn[u.id]?.[k] ?? d; }
  function sd(id, k, v) { dryIn = { ...dryIn, [id]: { ...(dryIn[id] || {}), [k]: v } }; }
  let confirmUnload = $state(false);
  let confirmLoad = $state(null);

  async function run(key, fn, ok) {
    busy = key; msg = null;
    try { await fn(); if (ok) msg = { kind: 'ok', text: ok }; if (refresh) await refresh(); }
    catch (e) { msg = { kind: 'err', text: e.message || 'command failed' }; }
    finally { busy = null; confirmUnload = false; confirmLoad = null; }
  }
  const startDry = (u) => run('dry-' + u.id, () => api.dryingStart(printerId, {
    ams_id: u.id, temp: Number(dv(u, 'temp', u.dryTarget || 45)) || 45,
    duration: Number(dv(u, 'dur', 4)) || 4, filament: dv(u, 'fil', u.suggest) || ''
  }), 'Drying started.');
  const stopDry = (u) => run('dry-' + u.id, () => api.dryingStop(printerId, u.id), 'Drying stopped.');
  const reread = (u) => run('rr-' + u.id, async () => {
    for (const s of u.slots) { try { await api.amsSlotRefresh(printerId, u.id, s.slotId); } catch { /* keep going */ } }
  }, 'Re-reading filament…');
  const loadSlot = (s) => run('load', () => api.amsLoad(printerId, s.trayId), 'Load command sent.');
  const unload = () => run('unload', () => api.amsUnload(printerId), 'Unload command sent.');
</script>

<div class="card card-pad ams">
  <div class="ah">
    <div class="atitle">
      <h3>{PANEL_LABEL}</h3>
      {#if subName}<span class="asub muted">{subName}</span>{/if}
    </div>
    <div class="ah-act">
      {#if confirmUnload}
        <span class="muted tiny">Unload?</span>
        <button class="btn btn-danger btn-sm" onclick={unload} disabled={busy === 'unload'}>Confirm</button>
        <button class="btn btn-ghost btn-sm" onclick={() => (confirmUnload = false)}>Cancel</button>
      {:else}
        <button class="btn btn-ghost btn-sm" onclick={() => (confirmUnload = true)} disabled={!!busy}>Unload</button>
      {/if}
    </div>
  </div>

  {#each units as u (u.id)}
    <div class="unit">
      <div class="unit-hd">
        <span class="ut">{u.type} <span class="muted">#{u.num}</span></span>
        <span class="um">
          {#if u.humidity != null}<span class="hum mono" title="Humidity">◐ {u.humidity}%</span>{/if}
          {#if u.drying}<span class="chip accent">drying</span>{/if}
          <button class="mini" onclick={() => reread(u)} disabled={busy === 'rr-' + u.id} title="Re-read RFID">↻ Reread</button>
        </span>
      </div>

      <div class="slots">
        {#each u.slots as s (s.slotId)}
          <div class="slot" class:empty={s.empty} style={s.empty ? '' : `border-color:${s.color || 'var(--ophq-border)'}`}>
            <div class="swatch" style="background:{s.empty ? 'transparent' : (s.color || 'var(--ophq-faint)')}"></div>
            <span class="mat">{s.empty ? 'Empty' : s.material}</span>
            {#if !s.empty && s.remain != null}<span class="rem mono">{s.remain}%</span>{/if}
            {#if !s.empty}
              {#if confirmLoad === s.trayId}
                <button class="mini pri" onclick={() => loadSlot(s)} disabled={busy === 'load'}>Load?</button>
              {:else}
                <button class="mini" onclick={() => (confirmLoad = s.trayId)} disabled={!!busy}>Load</button>
              {/if}
            {/if}
          </div>
        {/each}
      </div>

      {#if u.canDry}
        <div class="dry">
          {#if u.drying}
            <span class="muted tiny">Drying{#if u.dryFilament} {u.dryFilament}{/if}{#if u.dryTarget} @ {u.dryTarget}°C{/if}</span>
            <button class="btn btn-ghost btn-sm danger-text" onclick={() => stopDry(u)} disabled={busy === 'dry-' + u.id}>Stop drying</button>
          {:else}
            <span class="dryl">Dry:</span>
            <input class="input xs" type="text" value={dv(u, 'fil', u.suggest)} oninput={(e) => sd(u.id, 'fil', e.target.value)} placeholder="PLA" />
            <input class="input xs" type="number" min="45" max="85" value={dv(u, 'temp', u.dryTarget || 45)} oninput={(e) => sd(u.id, 'temp', e.target.value)} /><span class="muted">°C</span>
            <input class="input xs" type="number" min="1" max="24" value={dv(u, 'dur', 4)} oninput={(e) => sd(u.id, 'dur', e.target.value)} /><span class="muted">h</span>
            <button class="btn btn-primary btn-sm" onclick={() => startDry(u)} disabled={busy === 'dry-' + u.id}>Dry</button>
          {/if}
        </div>
      {/if}
    </div>
  {/each}

  {#if external.length}
    <div class="unit ext">
      <div class="unit-hd"><span class="ut">External spool</span></div>
      <div class="slots">
        {#each external as e}
          <div class="slot" style="border-color:{e.color || 'var(--ophq-border)'}">
            <div class="swatch" style="background:{e.color || 'var(--ophq-faint)'}"></div>
            <span class="mat">{e.material}</span>
            {#if e.remain != null && e.remain >= 0}<span class="rem mono">{e.remain}%</span>{/if}
          </div>
        {/each}
      </div>
    </div>
  {/if}

  {#if units.length === 0 && external.length === 0}
    <p class="muted">No AMS or external spool detected.</p>
  {/if}
  {#if msg}<p class={msg.kind === 'ok' ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}
</div>

<style>
  .ams { margin-top: 1.2rem; }
  .ah { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 0.9rem; gap: 0.8rem; }
  .atitle { display: flex; flex-direction: column; gap: 0.1rem; }
  .ah h3 { margin: 0; font-size: 1.05rem; }
  .asub { font-size: 0.8rem; }
  .ah-act { display: flex; align-items: center; gap: 0.4rem; }
  .unit { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.7rem 0.9rem; background: var(--ophq-surface); margin-bottom: 0.7rem; }
  .unit-hd { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.6rem; }
  .ut { font-weight: 600; font-size: 0.92rem; }
  .um { display: flex; align-items: center; gap: 0.6rem; }
  .hum { font-size: 0.8rem; color: var(--ophq-text-2); }
  .slots { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .slot { width: 88px; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.5rem; display: flex; flex-direction: column; align-items: center; gap: 0.3rem; background: var(--ophq-bg-2); }
  .slot.empty { opacity: 0.5; border-style: dashed; }
  .swatch { width: 100%; height: 30px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.12); }
  .slot.empty .swatch { border-style: dashed; background: transparent; }
  .mat { font-size: 0.8rem; font-weight: 600; }
  .rem { font-size: 0.72rem; color: var(--ophq-text-2); }
  .mini { background: var(--ophq-bg); border: 1px solid var(--ophq-border); color: var(--ophq-text-2); border-radius: var(--radius-sm); padding: 0.15rem 0.45rem; font-size: 0.72rem; cursor: pointer; }
  .mini:hover:not(:disabled) { border-color: var(--ophq-primary); color: var(--ophq-text); }
  .mini.pri { border-color: var(--ophq-primary); color: var(--ophq-primary-2); background: var(--ophq-primary-dim); }
  .dry { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; margin-top: 0.7rem; padding-top: 0.6rem; border-top: 1px solid var(--ophq-border-soft); }
  .dryl { font-size: 0.8rem; color: var(--ophq-text-2); }
  .input.xs { max-width: 68px; padding: 0.3rem 0.45rem; font-size: 0.82rem; }
  .tiny { font-size: 0.78rem; }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .chip.accent { color: var(--ophq-accent); border-color: rgba(255,176,32,0.3); background: rgba(255,176,32,0.08); }
  .ok-msg { color: var(--ophq-success); font-size: 0.88rem; margin: 0.7rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0.7rem 0 0; }
</style>
