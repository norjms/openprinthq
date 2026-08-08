<script>
  // Tell the printer what's actually in an AMS slot: filament preset, colour and
  // temperature window. This is the pencil on each slot.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // The engine's configure route wants a full set of fields at once
  // (tray_info_idx, type, sub-brand, colour, nozzle temp min/max), so this
  // always sends a complete slot definition rather than a patch.
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import ModalShell from '$lib/components/ModalShell.svelte';

  let {
    printerId, amsId, trayId, slotLabel = 'Slot',
    current = null,          // { tray_type, tray_color, nozzle_temp_min, nozzle_temp_max, tray_info_idx, tray_sub_brands }
    onclose = () => {}, onsaved = () => {}
  } = $props();

  // Sensible fallbacks for a slot the printer knows nothing about. These match
  // the common vendor presets closely enough to print with, and the user can
  // override every one of them.
  const FALLBACK = [
    { tray_type: 'PLA',  tray_info_idx: 'GFL99', nozzle_temp_min: 190, nozzle_temp_max: 240 },
    { tray_type: 'PETG', tray_info_idx: 'GFG99', nozzle_temp_min: 220, nozzle_temp_max: 270 },
    { tray_type: 'ABS',  tray_info_idx: 'GFB99', nozzle_temp_min: 240, nozzle_temp_max: 280 },
    { tray_type: 'ASA',  tray_info_idx: 'GFB98', nozzle_temp_min: 240, nozzle_temp_max: 280 },
    { tray_type: 'TPU',  tray_info_idx: 'GFU99', nozzle_temp_min: 200, nozzle_temp_max: 250 },
    { tray_type: 'PC',   tray_info_idx: 'GFC99', nozzle_temp_min: 260, nozzle_temp_max: 300 },
    { tray_type: 'PA',   tray_info_idx: 'GFN99', nozzle_temp_min: 260, nozzle_temp_max: 300 },
    { tray_type: 'PVA',  tray_info_idx: 'GFS99', nozzle_temp_min: 190, nozzle_temp_max: 250 }
  ];

  let presets = $state([]);
  let loadingPresets = $state(true);

  // The engine's available-filaments payload has changed shape across firmware
  // revisions, so normalise whatever comes back rather than trusting one key.
  function normalisePresets(raw) {
    const list = Array.isArray(raw) ? raw : (raw?.filaments || raw?.items || []);
    const out = list
      .map((f) => ({
        tray_info_idx: f.tray_info_idx || f.filament_id || f.id || '',
        tray_type: f.tray_type || f.filament_type || f.type || '',
        name: f.name || f.filament_name || `${f.vendor || ''} ${f.tray_type || f.filament_type || ''}`.trim(),
        tray_sub_brands: f.tray_sub_brands || f.sub_brand || f.name || '',
        nozzle_temp_min: Number(f.nozzle_temp_min ?? f.temp_min) || 0,
        nozzle_temp_max: Number(f.nozzle_temp_max ?? f.temp_max) || 0
      }))
      .filter((f) => f.tray_info_idx && f.tray_type);
    return out.length ? out : FALLBACK.map((f) => ({ ...f, name: f.tray_type, tray_sub_brands: f.tray_type }));
  }

  let form = $state({
    tray_info_idx: current?.tray_info_idx || '',
    tray_type: current?.tray_type || 'PLA',
    tray_sub_brands: current?.tray_sub_brands || '',
    // Engine wants RRGGBBAA without '#'. Keep a plain #RRGGBB for <input type=color>.
    color: '#' + String(current?.tray_color || 'FFFFFF').replace(/^#/, '').slice(0, 6).padEnd(6, 'F'),
    nozzle_temp_min: Number(current?.nozzle_temp_min) || 190,
    nozzle_temp_max: Number(current?.nozzle_temp_max) || 240
  });

  let saving = $state(false);
  let clearing = $state(false);
  let confirmClear = $state(false);
  let err = $state(null);

  onMount(async () => {
    try { presets = normalisePresets(await api.availableFilaments()); }
    catch { presets = normalisePresets(null); }
    finally {
      loadingPresets = false;
      // If the slot had no preset, adopt the first one matching its material so
      // the temperature window isn't left at a guess.
      if (!form.tray_info_idx) {
        const m = presets.find((p) => p.tray_type === form.tray_type) || presets[0];
        if (m) pickPreset(m.tray_info_idx);
      }
    }
  });

  function pickPreset(idx) {
    const p = presets.find((x) => x.tray_info_idx === idx);
    if (!p) return;
    form = {
      ...form,
      tray_info_idx: p.tray_info_idx,
      tray_type: p.tray_type,
      tray_sub_brands: p.tray_sub_brands || p.name || p.tray_type,
      nozzle_temp_min: p.nozzle_temp_min || form.nozzle_temp_min,
      nozzle_temp_max: p.nozzle_temp_max || form.nozzle_temp_max
    };
  }

  const tempsSane = $derived(
    form.nozzle_temp_min > 0 && form.nozzle_temp_max > form.nozzle_temp_min && form.nozzle_temp_max <= 350
  );

  async function save() {
    saving = true; err = null;
    try {
      await api.configureSlot(printerId, amsId, trayId, {
        tray_info_idx: form.tray_info_idx,
        tray_type: form.tray_type,
        tray_sub_brands: form.tray_sub_brands || form.tray_type,
        tray_color: form.color.replace(/^#/, '').toUpperCase() + 'FF',
        nozzle_temp_min: form.nozzle_temp_min,
        nozzle_temp_max: form.nozzle_temp_max
      });
      onsaved();
      onclose();
    } catch (e) {
      err = e?.message || 'The printer refused the slot settings.';
    } finally { saving = false; }
  }

  async function clearSlot() {
    clearing = true; err = null;
    try {
      await api.resetSlot(printerId, amsId, trayId);
      onsaved();
      onclose();
    } catch (e) {
      err = e?.message || 'Could not clear the slot.';
      clearing = false; confirmClear = false;
    }
  }
</script>

<ModalShell title="Edit {slotLabel}" subtitle="What's loaded in this slot" width="470px"
            busy={saving || clearing} {onclose}>
  <label class="fld">
    <span class="lb">Filament preset</span>
    <select class="input" bind:value={form.tray_info_idx} disabled={loadingPresets || saving}
            onchange={(e) => pickPreset(e.currentTarget.value)}>
      {#if loadingPresets}<option>Loading…</option>{/if}
      {#each presets as p (p.tray_info_idx)}
        <option value={p.tray_info_idx}>{p.name || p.tray_type}</option>
      {/each}
    </select>
    <span class="muted tiny">Sets the material and its temperature window.</span>
  </label>

  <div class="two">
    <label class="fld">
      <span class="lb">Material</span>
      <input class="input" type="text" bind:value={form.tray_type} maxlength="16" disabled={saving} />
    </label>
    <label class="fld">
      <span class="lb">Colour</span>
      <span class="colorrow">
        <input class="swatch" type="color" bind:value={form.color} disabled={saving}
               aria-label="Filament colour" />
        <input class="input mono" type="text" bind:value={form.color} maxlength="7" disabled={saving} />
      </span>
    </label>
  </div>

  <div class="two">
    <label class="fld">
      <span class="lb">Nozzle min °C</span>
      <input class="input" type="number" min="150" max="350" bind:value={form.nozzle_temp_min} disabled={saving} />
    </label>
    <label class="fld">
      <span class="lb">Nozzle max °C</span>
      <input class="input" type="number" min="150" max="350" bind:value={form.nozzle_temp_max} disabled={saving} />
    </label>
  </div>
  {#if !tempsSane}
    <p class="warn tiny">The maximum has to be above the minimum, and at or below 350 °C.</p>
  {/if}

  {#if confirmClear}
    <p class="cq">Clear {slotLabel} back to "unknown filament"?</p>
  {/if}
  {#if err}<p class="err">{err}</p>{/if}

  {#snippet footer()}
    {#if confirmClear}
      <button class="btn btn-ghost btn-sm" onclick={() => (confirmClear = false)} disabled={clearing}>Cancel</button>
      <button class="btn btn-danger btn-sm" onclick={clearSlot} disabled={clearing}>
        {clearing ? 'Clearing…' : 'Clear slot'}
      </button>
    {:else}
      <button class="btn btn-ghost btn-sm clr" onclick={() => (confirmClear = true)} disabled={saving}>Clear slot</button>
      <button class="btn btn-ghost btn-sm" onclick={() => onclose()} disabled={saving}>Cancel</button>
      <button class="btn btn-primary btn-sm" onclick={save} disabled={saving || !tempsSane || !form.tray_type}>
        {saving ? 'Saving…' : 'Save'}
      </button>
    {/if}
  {/snippet}
</ModalShell>

<style>
  .fld { display: flex; flex-direction: column; gap: 0.3rem; margin-bottom: 0.9rem; }
  .lb { font-size: 0.8rem; font-weight: 600; color: var(--ophq-text-2); }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; }
  .colorrow { display: flex; gap: 0.45rem; align-items: center; }
  .swatch { width: 42px; height: 34px; padding: 2px; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-bg-2); cursor: pointer; flex: 0 0 auto; }
  .tiny { font-size: 0.78rem; }
  .warn { color: var(--ophq-warn); margin: 0 0 0.6rem; }
  .cq { margin: 0.4rem 0 0; font-size: 0.9rem; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0.6rem 0 0; }
  .clr { margin-right: auto; color: var(--ophq-danger); }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .btn-danger:hover { background: #ff7280; color: #fff; }
</style>
