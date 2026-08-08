<script>
  // Filament / Hotends. The filament tab is the AMS slot grid — pick a slot,
  // load it, edit what's in it, dry a unit. The hotends tab reports what nozzle
  // hardware is fitted.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // Hotends is read-only on purpose: the engine has no write path for nozzle
  // type, diameter or rack slot, so a control here would be a lie.
  import { api } from '$lib/api';
  import SlotEditModal from '$lib/components/SlotEditModal.svelte';
  import { nozzleType } from '$lib/models.js';

  let {
    printerId, status = null, isBambu = true, refresh = () => {}
  } = $props();

  const st = $derived(status || {});
  const connected = $derived(!!st.connected);

  let tab = $state('filament');

  // ---- units --------------------------------------------------------------
  const hex = (c) => {
    const s = String(c || '').slice(0, 6);
    return /^[0-9a-f]{6}$/i.test(s) ? '#' + s : '';
  };
  const LETTERS = ['A', 'B', 'C', 'D'];

  function sideOf(u) {
    const m = st.ams_extruder_map || {};
    const e = m[String(u.id)] ?? m[u.id];
    return e === 0 ? 'R' : e === 1 ? 'L' : '';
  }

  const units = $derived.by(() =>
    (st.ams || []).map((u, i) => {
      const mt = String(u.module_type || '').toLowerCase();
      return {
        id: u.id,
        letter: LETTERS[i] || String(i + 1),
        label: (u.is_ams_ht ? 'HT' : 'AMS') + '-' + (LETTERS[i] || i + 1),
        side: sideOf(u),
        humidity: (u.humidity != null && u.humidity !== '') ? Number(u.humidity) : null,
        temp: (u.temp != null && u.temp !== '') ? Number(u.temp) : null,
        canDry: !!st.supports_drying && (u.is_ams_ht || ['n3f', 'n3s'].includes(mt)),
        drying: (Number(u.dry_status) || 0) !== 0,
        dryFilament: u.dry_filament || '',
        dryTarget: u.dry_target_temp || null,
        slots: (u.tray || []).map((t, j) => ({
          n: j + 1,
          code: `${LETTERS[i] || i + 1}${j + 1}`,
          amsId: u.id,
          trayIdx: j,
          trayId: u.id * 4 + j,
          type: t?.tray_type || '',
          color: hex(t?.tray_color),
          remain: (t?.remain != null && t.remain >= 0) ? t.remain : null,
          empty: !t?.tray_type,
          active: st.tray_now === u.id * 4 + j,
          raw: t || {}
        }))
      };
    })
  );

  // External spools sit alongside the units — same card shape, one slot.
  const externals = $derived.by(() => {
    const arr = Array.isArray(st.vt_tray) ? st.vt_tray : (st.vt_tray ? [st.vt_tray] : []);
    const dual = (st.nozzles?.length || 0) > 1;
    return arr.map((t, i) => ({
      n: i + 1,
      code: dual ? (i === 0 ? 'Ext-L' : 'Ext-R') : 'Ext',
      trayId: i === 0 ? 254 : 255,
      type: t?.tray_type || '',
      color: hex(t?.tray_color),
      remain: (t?.remain != null && t.remain >= 0) ? t.remain : null,
      empty: !t?.tray_type,
      active: st.tray_now === (i === 0 ? 254 : 255)
    }));
  });

  const hasAnything = $derived(units.length > 0 || externals.length > 0);

  // ---- selection + actions ------------------------------------------------
  let picked = $state(null);        // trayId
  let busy = $state(null);
  let msg = $state(null);
  let confirm = $state(null);       // 'load' | 'unload'
  let editing = $state(null);       // { amsId, trayIdx, label, current }
  let dryOpen = $state(null);       // ams id
  let dryForm = $state({});

  const pickedLabel = $derived.by(() => {
    for (const u of units) for (const s of u.slots) if (s.trayId === picked) return s.code;
    for (const e of externals) if (e.trayId === picked) return e.code;
    return null;
  });

  async function doLoad() {
    busy = 'load'; msg = null;
    try {
      await api.amsLoad(printerId, picked);
      msg = { ok: true, text: `Load command sent for ${pickedLabel}.` };
      await refresh();
    } catch (e) { msg = { ok: false, text: e?.message || 'Load failed.' }; }
    finally { busy = null; confirm = null; }
  }
  async function doUnload() {
    busy = 'unload'; msg = null;
    try {
      await api.amsUnload(printerId);
      msg = { ok: true, text: 'Unload command sent.' };
      await refresh();
    } catch (e) { msg = { ok: false, text: e?.message || 'Unload failed.' }; }
    finally { busy = null; confirm = null; }
  }
  async function toggleBackup() {
    busy = 'backup'; msg = null;
    try { await api.amsBackup(printerId, !st.ams_filament_backup); await refresh(); }
    catch (e) { msg = { ok: false, text: e?.message || 'Could not change auto refill.' }; }
    finally { busy = null; }
  }
  async function reread(s) {
    busy = 'rfid' + s.code; msg = null;
    try { await api.amsSlotRefresh(printerId, s.amsId, s.trayIdx); await refresh(); }
    catch (e) { msg = { ok: false, text: e?.message || 'Could not re-read the tag.' }; }
    finally { busy = null; }
  }
  async function startDry(u) {
    const f = dryForm[u.id] || {};
    busy = 'dry' + u.id; msg = null;
    try {
      await api.dryingStart(printerId, {
        ams_id: u.id,
        temp: Number(f.temp ?? u.dryTarget ?? 45) || 45,
        duration: Number(f.duration ?? 4) || 4,
        filament: f.filament ?? u.dryFilament ?? (u.slots.find((s) => !s.empty)?.type || 'PLA')
      });
      msg = { ok: true, text: `Drying started on ${u.label}.` };
      dryOpen = null;
      await refresh();
    } catch (e) { msg = { ok: false, text: e?.message || 'Could not start drying.' }; }
    finally { busy = null; }
  }
  async function stopDry(u) {
    busy = 'dry' + u.id; msg = null;
    try { await api.dryingStop(printerId, u.id); msg = { ok: true, text: 'Drying stopped.' }; await refresh(); }
    catch (e) { msg = { ok: false, text: e?.message || 'Could not stop drying.' }; }
    finally { busy = null; }
  }
  function setDry(id, k, v) { dryForm = { ...dryForm, [id]: { ...(dryForm[id] || {}), [k]: v } }; }

  // ---- hotends ------------------------------------------------------------
  const rack = $derived.by(() => {
    const r = (st.nozzle_rack || []).filter((n) => Number(n.id) >= 16 && (n.nozzle_type || Number(n.nozzle_diameter) > 0));
    if (!r.length) return [];
    const base = Math.min(...r.map((n) => Number(n.id)));
    return r.map((n) => ({ ...n, pos: Number(n.id) - base + 1 }));
  });
  const toolhead = $derived.by(() => {
    const head = (st.nozzle_rack || []).filter((n) => Number(n.id) < 16);
    const real = (n) => !!(n.serial_number && String(n.serial_number).toUpperCase() !== 'N/A' && Number(n.max_temp) > 0);
    const known = (n) => {
      const t = String(n.nozzle_type || '').trim().toUpperCase();
      return (t && t !== 'N/A') || Number(n.nozzle_diameter) > 0;
    };
    let list = rack.length ? head.filter(real) : head.filter(known);
    if (!rack.length && !list.length && st.nozzles?.length) {
      list = st.nozzles.map((n, i) => ({ id: i, nozzle_type: n.nozzle_type, nozzle_diameter: n.nozzle_diameter }));
    }
    const dual = list.length > 1;
    return list.map((n) => ({ ...n, side: dual ? (Number(n.id) === 1 ? 'Right' : 'Left') : '' }));
  });
  const hasHotendInfo = $derived(toolhead.length > 0 || rack.length > 0);
</script>

{#if isBambu && (hasAnything || hasHotendInfo)}
<div class="card fp">
  <div class="tabs" role="tablist" aria-label="Filament and hotends">
    <button role="tab" aria-selected={tab === 'filament'} class:on={tab === 'filament'}
            onclick={() => (tab = 'filament')}>Filament</button>
    <button role="tab" aria-selected={tab === 'hotends'} class:on={tab === 'hotends'}
            onclick={() => (tab = 'hotends')}>Hotends</button>
  </div>

  {#if tab === 'filament'}
    {#if !hasAnything}
      <p class="empty">No multi-material unit or external spool detected.</p>
    {:else}
      <div class="groups">
        {#each units as u (u.id)}
          <section class="grp">
            <header class="ghead">
              <span class="gname">
                {u.label}
                {#if u.side}<span class="side {u.side === 'L' ? 'l' : 'r'}">{u.side}</span>{/if}
              </span>
              <span class="gmeta">
                {#if u.humidity != null}
                  <span class="hum" title="Humidity"><span aria-hidden="true">💧</span> {u.humidity}%</span>
                {/if}
                {#if u.temp != null}<span class="hum mono">{u.temp.toFixed(1)}°</span>{/if}
                {#if u.canDry}
                  {#if u.drying}
                    <button class="dryb on" onclick={() => stopDry(u)} disabled={busy === 'dry' + u.id}
                            data-tip="Drying now — stop it" aria-label="Stop drying">☀ Drying</button>
                  {:else}
                    <button class="dryb" onclick={() => (dryOpen = dryOpen === u.id ? null : u.id)}
                            disabled={!connected} data-tip="Dry this unit" aria-label="Dry this unit">☀</button>
                  {/if}
                {/if}
              </span>
            </header>

            {#if dryOpen === u.id && !u.drying}
              <div class="dryrow">
                <label>Filament
                  <input class="input xs" type="text" value={dryForm[u.id]?.filament ?? (u.dryFilament || u.slots.find((s) => !s.empty)?.type || 'PLA')}
                         oninput={(e) => setDry(u.id, 'filament', e.currentTarget.value)} /></label>
                <label>°C
                  <input class="input xs mono" type="number" min="45" max="85" value={dryForm[u.id]?.temp ?? (u.dryTarget || 45)}
                         oninput={(e) => setDry(u.id, 'temp', e.currentTarget.value)} /></label>
                <label>hours
                  <input class="input xs mono" type="number" min="1" max="24" value={dryForm[u.id]?.duration ?? 4}
                         oninput={(e) => setDry(u.id, 'duration', e.currentTarget.value)} /></label>
                <button class="btn btn-primary btn-sm" onclick={() => startDry(u)} disabled={busy === 'dry' + u.id}>
                  {busy === 'dry' + u.id ? 'Starting…' : 'Dry'}
                </button>
              </div>
            {/if}

            <div class="slots" style="--n:{u.slots.length}">
              {#each u.slots as s (s.code)}
                <div class="slotwrap">
                  <div class="sbar">
                    <span class="scode">{s.code}</span>
                    <button class="sic" onclick={() => reread(s)} disabled={!connected || busy === 'rfid' + s.code}
                            data-tip="Re-read the RFID tag" aria-label={`Re-read the tag in ${s.code}`}>↻</button>
                  </div>
                  <button class="slot" class:empty={s.empty} class:active={s.active} class:picked={picked === s.trayId}
                          style={s.color ? `--sc:${s.color}` : ''}
                          onclick={() => (picked = picked === s.trayId ? null : s.trayId)}
                          disabled={s.empty}
                          aria-pressed={picked === s.trayId}
                          aria-label={`${s.code}: ${s.empty ? 'empty' : s.type}${s.remain != null ? `, ${s.remain}% left` : ''}`}>
                    <span class="stype">{s.empty ? 'Empty' : s.type}</span>
                    {#if s.remain != null && !s.empty}<span class="srem mono">{s.remain}%</span>{/if}
                    {#if s.active}<span class="sactive" aria-hidden="true">◉</span>{/if}
                  </button>
                  <button class="sedit" onclick={() => (editing = { amsId: s.amsId, trayIdx: s.trayIdx, label: s.code, current: { ...s.raw, tray_type: s.type } })}
                          disabled={!connected}
                          data-tip="Set what's in this slot" aria-label={`Edit ${s.code}`}>✎</button>
                </div>
              {/each}
            </div>
          </section>
        {/each}

        {#if externals.length}
          <section class="grp">
            <header class="ghead"><span class="gname">External</span></header>
            <div class="slots" style="--n:{externals.length}">
              {#each externals as e (e.trayId)}
                <div class="slotwrap">
                  <div class="sbar"><span class="scode">{e.code}</span></div>
                  <button class="slot" class:empty={e.empty} class:active={e.active} class:picked={picked === e.trayId}
                          style={e.color ? `--sc:${e.color}` : ''}
                          onclick={() => (picked = picked === e.trayId ? null : e.trayId)}
                          disabled={e.empty} aria-pressed={picked === e.trayId}
                          aria-label={`${e.code}: ${e.empty ? 'empty' : e.type}`}>
                    <span class="stype">{e.empty ? 'Empty' : e.type}</span>
                    {#if e.remain != null && !e.empty}<span class="srem mono">{e.remain}%</span>{/if}
                    {#if e.active}<span class="sactive" aria-hidden="true">◉</span>{/if}
                  </button>
                </div>
              {/each}
            </div>
          </section>
        {/if}
      </div>

      <div class="foot">
        {#if units.length}
          <label class="refill" data-tip="Switch to another spool of the same type when one runs out">
            <input type="checkbox" checked={!!st.ams_filament_backup} disabled={!connected || busy === 'backup'}
                   onchange={toggleBackup} />
            <span>Auto Refill</span>
          </label>
        {/if}
        <span class="sp"></span>
        {#if confirm === 'unload'}
          <span class="cq">Unload the filament in the hotend?</span>
          <button class="btn btn-ghost btn-sm" onclick={() => (confirm = null)} disabled={!!busy}>Cancel</button>
          <button class="btn btn-danger btn-sm" onclick={doUnload} disabled={!!busy}>
            {busy === 'unload' ? 'Unloading…' : 'Confirm'}
          </button>
        {:else if confirm === 'load'}
          <span class="cq">Load {pickedLabel}?</span>
          <button class="btn btn-ghost btn-sm" onclick={() => (confirm = null)} disabled={!!busy}>Cancel</button>
          <button class="btn btn-primary btn-sm" onclick={doLoad} disabled={!!busy}>
            {busy === 'load' ? 'Loading…' : 'Confirm'}
          </button>
        {:else}
          <button class="btn btn-ghost btn-sm" onclick={() => (confirm = 'unload')} disabled={!connected || !!busy}>
            Unload
          </button>
          <button class="btn btn-primary btn-sm" onclick={() => (confirm = 'load')}
                  disabled={!connected || picked == null || !!busy}
                  data-tip={picked == null ? 'Pick a slot first' : `Load ${pickedLabel}`}>
            Load{pickedLabel ? ` ${pickedLabel}` : ''}
          </button>
        {/if}
      </div>
    {/if}
  {:else}
    <!-- ============ hotends (read-only) ============ -->
    {#if !hasHotendInfo}
      <p class="empty">This printer doesn't report its nozzle hardware.</p>
    {:else}
      <div class="hot">
        <div class="hrow">
          <span class="hcap">In the toolhead</span>
          <div class="hlist">
            {#each toolhead as n (n.id)}
              <span class="hcard">
                {#if n.side}<span class="hside">{n.side}</span>{/if}
                <b class="mono">{n.nozzle_diameter} mm</b>
                <span class="hmat">{nozzleType(n.nozzle_type).full || n.nozzle_type || '—'}</span>
              </span>
            {:else}
              <span class="muted">No nozzle installed.</span>
            {/each}
          </div>
        </div>
        {#if rack.length}
          <div class="hrow">
            <span class="hcap">Rack</span>
            <div class="hlist">
              {#each rack as s (s.id)}
                <span class="hslot" data-tip={`Position ${s.pos} · ${s.nozzle_diameter} mm · ${nozzleType(s.nozzle_type).full || s.nozzle_type || 'nozzle'}`}>
                  <span class="hpos">P{s.pos}</span>
                  <span class="hdia mono">{s.nozzle_diameter}</span>
                  <span class="hty">{nozzleType(s.nozzle_type).short || s.nozzle_type}</span>
                </span>
              {/each}
            </div>
          </div>
        {/if}
        <p class="muted tiny hnote">
          Nozzle hardware is reported by the printer and can't be changed from here —
          swap it on the machine and it'll update.
        </p>
      </div>
    {/if}
  {/if}

  {#if msg}<p class={msg.ok ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}
</div>
{/if}

{#if editing}
  <SlotEditModal printerId={printerId} amsId={editing.amsId} trayId={editing.trayIdx}
                 slotLabel={editing.label} current={editing.current}
                 onclose={() => (editing = null)} onsaved={refresh} />
{/if}

<style>
  .fp { padding: 0; overflow: hidden; }
  .tabs { display: flex; justify-content: center; gap: 0; padding: 0.6rem; border-bottom: 1px solid var(--ophq-border-soft); }
  .tabs button {
    border: 1px solid var(--ophq-border); background: var(--ophq-bg-2); color: var(--ophq-text-2);
    padding: 0.3rem 1.1rem; font-size: 0.85rem; cursor: pointer;
  }
  .tabs button:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); }
  .tabs button:last-child { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; border-left: 0; }
  .tabs button.on { background: var(--ophq-primary); border-color: var(--ophq-primary); color: #fff; font-weight: 600; }
  .tabs button:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: 1px; }

  .empty { margin: 0; padding: 1.2rem 0.9rem; color: var(--ophq-muted); font-size: 0.9rem; }

  .groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 0.8rem; padding: 0.85rem; }
  .grp { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-bg-2); padding: 0.65rem 0.7rem; }
  .ghead { display: flex; align-items: center; justify-content: space-between; gap: 0.6rem; margin-bottom: 0.55rem; }
  .gname { font-weight: 600; font-size: 0.9rem; display: inline-flex; align-items: center; gap: 0.4rem; }
  .side { font-size: 0.66rem; font-weight: 700; padding: 0.05rem 0.35rem; border-radius: 4px; background: var(--ophq-success); color: #06210f; }
  .side.l { background: var(--ophq-primary); color: #fff; }
  .gmeta { display: inline-flex; align-items: center; gap: 0.5rem; }
  .hum { font-size: 0.78rem; color: var(--ophq-text-2); white-space: nowrap; }
  .dryb { border: 1px solid var(--ophq-border); background: var(--ophq-surface); color: var(--ophq-text-2); border-radius: 999px; padding: 0.1rem 0.5rem; font-size: 0.76rem; cursor: pointer; }
  .dryb.on { border-color: var(--ophq-accent); color: var(--ophq-accent); background: color-mix(in srgb, var(--ophq-accent) 14%, transparent); }
  .dryb:disabled { opacity: 0.45; cursor: default; }

  .dryrow { display: flex; align-items: flex-end; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.6rem; }
  .dryrow label { display: inline-flex; flex-direction: column; gap: 0.15rem; font-size: 0.74rem; color: var(--ophq-muted); }
  .input.xs { max-width: 5.5rem; padding: 0.2rem 0.35rem; font-size: 0.82rem; }

  /* Slots keep a sane width when there are only one or two of them, instead of
     one external spool stretching the width of the card. */
  .slots { display: grid; grid-template-columns: repeat(var(--n), minmax(0, 1fr)); gap: 0.45rem; max-width: calc(var(--n) * 8.5rem); }
  .slotwrap { display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; }
  .sbar { display: flex; align-items: center; justify-content: space-between; gap: 0.2rem; }
  .scode { font-size: 0.68rem; color: var(--ophq-muted); font-weight: 700; letter-spacing: 0.04em; }
  .sic { border: 0; background: none; color: var(--ophq-muted); cursor: pointer; font-size: 0.78rem; padding: 0 0.15rem; }
  .sic:hover:not(:disabled) { color: var(--ophq-primary-2); }
  .sic:disabled { opacity: 0.4; cursor: default; }

  .slot {
    position: relative; min-height: 74px; border-radius: var(--radius-sm);
    border: 1px solid var(--ophq-border); background: var(--ophq-surface);
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.2rem;
    padding: 0.4rem 0.25rem; cursor: pointer; overflow: hidden;
  }
  /* The filament colour is the card, exactly as in the machine's own UI. The
     label flips to dark text on light spools so it stays readable. */
  .slot[style*="--sc"] { background: var(--sc); border-color: color-mix(in srgb, var(--sc) 70%, #000); }
  .slot[style*="--sc"] .stype, .slot[style*="--sc"] .srem {
    color: color-mix(in srgb, var(--sc) 30%, #000);
    text-shadow: 0 1px 0 color-mix(in srgb, var(--sc) 65%, #fff);
  }
  .slot.empty { background: var(--ophq-surface); border-style: dashed; cursor: default; opacity: 0.75; }
  .slot.empty .stype { color: var(--ophq-muted); }
  .slot.active { box-shadow: 0 0 0 2px var(--ophq-success); }
  .slot.picked { box-shadow: 0 0 0 2px var(--ophq-primary); }
  .slot:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: 2px; }
  .stype { font-size: 0.8rem; font-weight: 700; text-align: center; word-break: break-word; line-height: 1.15; }
  .srem { font-size: 0.7rem; opacity: 0.85; }
  .sactive { position: absolute; top: 3px; right: 4px; font-size: 0.6rem; color: var(--ophq-success); }
  .sedit { border: 0; background: none; color: var(--ophq-muted); cursor: pointer; font-size: 0.8rem; padding: 0; }
  .sedit:hover:not(:disabled) { color: var(--ophq-primary-2); }
  .sedit:disabled { opacity: 0.4; cursor: default; }

  .foot {
    display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
    padding: 0.6rem 0.85rem; border-top: 1px solid var(--ophq-border-soft); background: var(--ophq-bg-2);
  }
  .sp { flex: 1; }
  .refill { display: inline-flex; align-items: center; gap: 0.4rem; font-size: 0.85rem; color: var(--ophq-text-2); cursor: pointer; }
  .refill input { width: 16px; height: 16px; accent-color: var(--ophq-primary); }
  .cq { font-size: 0.85rem; color: var(--ophq-text-2); }

  .hot { padding: 0.9rem; display: flex; flex-direction: column; gap: 0.7rem; }
  .hrow { display: flex; align-items: center; gap: 0.7rem; flex-wrap: wrap; }
  .hcap { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); min-width: 7rem; }
  .hlist { display: flex; gap: 0.4rem; flex-wrap: wrap; }
  .hcard { display: inline-flex; align-items: baseline; gap: 0.4rem; padding: 0.3rem 0.6rem; border-radius: var(--radius-sm); background: var(--ophq-primary-dim); border: 1px solid var(--ophq-primary); color: var(--ophq-primary-2); font-size: 0.85rem; }
  .hcard b { color: var(--ophq-text); }
  .hside { font-size: 0.6rem; font-weight: 800; text-transform: uppercase; padding: 0.08rem 0.34rem; border-radius: 4px; background: var(--ophq-primary); color: #fff; align-self: center; }
  .hmat { color: var(--ophq-primary-2); }
  .hslot { display: inline-flex; flex-direction: column; align-items: center; min-width: 3rem; padding: 0.35rem 0.45rem; border-radius: var(--radius-sm); background: var(--ophq-surface); border: 1px solid var(--ophq-border); }
  .hpos { font-size: 0.62rem; font-weight: 700; color: var(--ophq-muted); }
  .hdia { font-size: 0.92rem; font-weight: 700; color: var(--ophq-text); line-height: 1.1; }
  .hty { font-size: 0.64rem; color: var(--ophq-text-2); }
  .hnote { margin: 0.2rem 0 0; max-width: 62ch; }

  .tiny { font-size: 0.78rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.84rem; margin: 0; padding: 0.5rem 0.85rem; }
  .err { color: var(--ophq-danger); font-size: 0.84rem; margin: 0; padding: 0.5rem 0.85rem; }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .btn-danger:hover { background: #ff7280; color: #fff; }
</style>
