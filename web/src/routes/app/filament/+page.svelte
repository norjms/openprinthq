<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let spools = $state([]);

  let busyId = $state(null);
  let toast = $state(null);
  let confirmArch = $state(null);
  let cur = $state('USD');
  const CUR_SYM = { USD: '$', EUR: '€', GBP: '£', CAD: '$', AUD: '$', NZD: '$', JPY: '¥', CNY: '¥', CHF: 'CHF ', SEK: 'kr ', NOK: 'kr ', DKK: 'kr ', PLN: 'zł ', INR: '₹', ZAR: 'R ', BRL: 'R$ ', MXN: '$' };
  const sym = $derived(CUR_SYM[cur] || (cur + ' '));
  const money = (v) => sym + (Number(v) || 0).toFixed(2);

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.spools || d?.items || d?.results || []);
    return arr.map((s) => {
      const label = s.label_weight ?? s.total_weight ?? s.spool_weight ?? null;
      const used = s.weight_used ?? null;
      const remaining = s.remaining_weight ?? s.remaining_grams ?? s.weight_remaining ??
        (label != null && used != null ? Math.max(0, label - used) : null);
      // Bambu AMS colours come as RRGGBBAA hex; browsers accept 8-digit hex but
      // drop the alpha so the swatch is always opaque.
      const raw = s.rgba || s.color_hex || s.color || '';
      const color = raw ? (String(raw).startsWith('#') ? raw : '#' + String(raw).slice(0, 6)) : '';
      const cpk = s.cost_per_kg ?? null;
      const value = (cpk != null && remaining != null) ? (cpk * remaining / 1000) : null;
      return {
        id: s.id ?? s.spool_id ?? s.tray_uuid,
        name: s.slicer_filament_name ?? s.name ?? s.filament_name ??
          (`${s.brand ?? ''} ${s.material ?? ''} ${s.subtype ?? ''}`.replace(/\s+/g, ' ').trim() || 'Spool'),
        material: [s.material ?? s.filament_type, s.subtype].filter(Boolean).join(' · '),
        brand: s.brand ?? '',
        colorName: s.color_name ?? '',
        color,
        remaining: remaining != null ? Math.round(remaining) : null,
        total: label,
        costPerKg: cpk,
        value,
        location: s.location ?? s.location_name ?? ''
      };
    });
  }
  async function load() {
    loading = true; error = null;
    try {
      const eng = await api.engineSettings().catch(() => null);
      if (eng?.currency) cur = eng.currency;
      spools = norm(await api.spools());
    }
    catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(load);
  const pct = (s) => (s.remaining != null && s.total ? Math.max(0, Math.min(100, Math.round((s.remaining / s.total) * 100))) : null);

  // Inventory summary (deep inventory: standing stock, weight, value).
  const summary = $derived.by(() => {
    const remG = spools.reduce((a, s) => a + (s.remaining || 0), 0);
    const val = spools.reduce((a, s) => a + (s.value || 0), 0);
    const hasVal = spools.some((s) => s.value != null);
    return { count: spools.length, kg: remG / 1000, value: hasVal ? val : null };
  });

  async function archive(s) {
    busyId = s.id;
    try {
      await api.archiveSpool(s.id);
      toast = { kind: 'ok', text: `Archived ${s.name}.` };
      await load();
    } catch (e) { toast = { kind: 'err', text: e.message || 'could not archive' }; }
    finally { busyId = null; confirmArch = null; setTimeout(() => (toast = null), 5000); }
  }

  // ---- add spool (#16 — manual add; only `material` is required) ----
  const MATERIALS = ['PLA', 'PLA Matte', 'PLA Silk', 'PETG', 'ABS', 'ASA', 'TPU', 'PC', 'Nylon (PA)', 'PVA', 'HIPS', 'PLA-CF', 'PETG-CF', 'PA-CF', 'PA-GF'];
  let locations = $state([]);
  let showAdd = $state(false);
  let saving = $state(false);
  let addErr = $state(null);
  let form = $state(newForm());
  function newForm() {
    return { material: 'PLA', subtype: '', brand: '', color_name: '', color: '#1f9d55', label_weight: 1000, cost_per_kg: '', storage_location: '', nozzle_temp_min: '', nozzle_temp_max: '', note: '', quantity: 1 };
  }
  async function openAdd() {
    form = newForm(); addErr = null; showAdd = true;
    try { locations = (await api.spoolLocations().catch(() => [])) || []; } catch { locations = []; }
  }
  function hexToRgba(hex) { const h = String(hex || '').replace('#', ''); return (h.length >= 6 ? h.slice(0, 6) : 'ffffff').toUpperCase() + 'FF'; }
  async function saveAdd() {
    const f = form;
    if (!f.material.trim()) { addErr = 'Material is required.'; return; }
    saving = true; addErr = null;
    const body = { material: f.material.trim(), rgba: hexToRgba(f.color), label_weight: Math.max(1, Math.round(Number(f.label_weight) || 1000)) };
    if (f.subtype.trim()) body.subtype = f.subtype.trim();
    if (f.brand.trim()) body.brand = f.brand.trim();
    if (f.color_name.trim()) body.color_name = f.color_name.trim();
    if (f.cost_per_kg !== '') body.cost_per_kg = Number(f.cost_per_kg);
    if (f.storage_location.trim()) body.storage_location = f.storage_location.trim();
    if (f.nozzle_temp_min !== '') body.nozzle_temp_min = Number(f.nozzle_temp_min);
    if (f.nozzle_temp_max !== '') body.nozzle_temp_max = Number(f.nozzle_temp_max);
    if (f.note.trim()) body.note = f.note.trim();
    try {
      const qty = Math.max(1, Math.round(Number(f.quantity) || 1));
      if (qty > 1) await api.addSpoolsBulk(body, qty); else await api.addSpool(body);
      toast = { kind: 'ok', text: qty > 1 ? `Added ${qty} spools.` : `Added ${(body.brand || '') + ' ' + body.material}`.trim() + ' spool.' };
      showAdd = false; await load();
    } catch (e) { addErr = e.message || 'could not add spool'; }
    finally { saving = false; setTimeout(() => (toast = null), 5000); }
  }

  // ---- adjust remaining weight (log waste / manual usage) ----
  let adjust = $state(null);   // { id, name, total, remaining }
  let adjBusy = $state(false);
  function openAdjust(s) { adjust = { id: s.id, name: s.name, total: s.total, remaining: s.remaining }; }
  async function saveAdjust() {
    if (!adjust) return;
    adjBusy = true;
    try {
      const total = Number(adjust.total) || 0;
      const rem = Math.max(0, total ? Math.min(total, Number(adjust.remaining) || 0) : (Number(adjust.remaining) || 0));
      const body = total ? { weight_used: Math.max(0, total - rem) } : {};
      await api.updateSpool(adjust.id, body);
      toast = { kind: 'ok', text: `Updated ${adjust.name}.` };
      adjust = null; await load();
    } catch (e) { toast = { kind: 'err', text: e.message || 'could not update' }; }
    finally { adjBusy = false; setTimeout(() => (toast = null), 5000); }
  }

  // ---- printable spool label ----
  let labelSpool = $state(null);
  function printLabel(s) {
    labelSpool = s;
    if (typeof requestAnimationFrame !== 'undefined') requestAnimationFrame(() => window.print());
    else window.print();
  }
</script>
<svelte:window onafterprint={() => (labelSpool = null)} />

<svelte:head><title>Filament · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div><h1>Filament</h1><p class="muted">Spool inventory, usage and cost.</p></div>
  <div class="flex gap">
    <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
    <button class="btn btn-primary btn-sm" onclick={openAdd}>+ Add spool</button>
  </div>
</div>

{#if toast}<div class="toast {toast.kind}">{toast.text}</div>{/if}

{#if loading}
  <div class="card card-pad muted">Loading spools…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else if spools.length === 0}
  <div class="card card-pad empty">
    <div class="ic">🧵</div>
    <h3>No spools yet</h3>
    <p class="muted">Track your filament here — remaining weight is deducted automatically as you print, with per-print cost.</p>
    <button class="btn btn-primary btn-sm" onclick={openAdd}>+ Add your first spool</button>
  </div>
{:else}
  <div class="sumrow">
    <div class="card card-pad st"><span class="muted">Spools</span><b>{summary.count}</b></div>
    <div class="card card-pad st"><span class="muted">Remaining</span><b>{summary.kg.toFixed(2)} kg</b></div>
    {#if summary.value != null}<div class="card card-pad st"><span class="muted">Stock value</span><b>{money(summary.value)}</b></div>{/if}
  </div>
  <div class="grid spools">
    {#each spools as s}
      <div class="card card-pad spool">
        <div class="flex center gap">
          <span class="dot" style="background:{s.color || 'var(--ophq-faint)'}"></span>
          <div class="grow">
            <div class="sname">{s.name}</div>
            <div class="muted mono mat">{s.material}{#if s.colorName} · {s.colorName}{/if}</div>
          </div>
        </div>
        {#if pct(s) != null}
          <div class="bar"><div class="fill" style="width:{pct(s)}%"></div></div>
          <div class="muted mono rem">{s.remaining} g left · {pct(s)}%{#if s.value != null} · {money(s.value)}{/if}</div>
        {/if}
        <div class="sfoot">
          <span class="muted mono tiny">{s.brand}{#if s.location} · {s.location}{/if}{#if s.costPerKg != null} · {sym}{s.costPerKg}/kg{/if}</span>
          <div class="acts">
            <button class="btn btn-ghost btn-xs" onclick={() => openAdjust(s)} disabled={busyId === s.id} title="Adjust remaining weight / log waste">Adjust</button>
            <button class="btn btn-ghost btn-xs" onclick={() => printLabel(s)} title="Print a spool label">Label</button>
            {#if confirmArch === s.id}
              <button class="btn btn-danger btn-xs" onclick={() => archive(s)} disabled={busyId === s.id}>Confirm</button><button class="btn btn-ghost btn-xs" onclick={() => (confirmArch = null)}>✕</button>
            {:else}
              <button class="btn btn-ghost btn-xs" onclick={() => (confirmArch = s.id)} disabled={busyId === s.id} title="Archive (used up / removed)">Archive</button>
            {/if}
          </div>
        </div>
      </div>
    {/each}
  </div>
{/if}

{#if showAdd}
  <div class="overlay no-print" role="presentation" onclick={() => (showAdd = false)}>
    <div class="dialog card" role="dialog" onclick={(e) => e.stopPropagation()}>
      <div class="dhead"><div><span class="eyebrow">Inventory</span><h3>Add spool</h3></div><button class="btn btn-ghost btn-sm" onclick={() => (showAdd = false)}>✕</button></div>
      <div class="grid2">
        <div class="fld">
          <label for="mat">Material *</label>
          <input id="mat" class="input" list="mats" bind:value={form.material} placeholder="PLA" />
          <datalist id="mats">{#each MATERIALS as m}<option value={m}></option>{/each}</datalist>
        </div>
        <div class="fld"><label for="sub">Sub-type</label><input id="sub" class="input" bind:value={form.subtype} placeholder="Matte, Silk, CF…" /></div>
        <div class="fld"><label for="brd">Brand</label><input id="brd" class="input" bind:value={form.brand} placeholder="Bambu, Polymaker…" /></div>
        <div class="fld"><label for="cn">Colour name</label><input id="cn" class="input" bind:value={form.color_name} placeholder="Galaxy Black" /></div>
        <div class="fld"><label for="col">Colour</label><input id="col" class="input color" type="color" bind:value={form.color} /></div>
        <div class="fld"><label for="wt">Net weight (g)</label><input id="wt" class="input" type="number" min="1" step="50" bind:value={form.label_weight} /></div>
        <div class="fld"><label for="cpk">Cost /kg ({sym.trim()})</label><input id="cpk" class="input" type="number" min="0" step="0.5" bind:value={form.cost_per_kg} placeholder="optional" /></div>
        <div class="fld">
          <label for="loc">Location</label>
          <input id="loc" class="input" list="locs" bind:value={form.storage_location} placeholder="Dry box A…" />
          <datalist id="locs">{#each locations as l}<option value={l.name || l.location_name || l}></option>{/each}</datalist>
        </div>
        <div class="fld"><label for="tmin">Nozzle °C min</label><input id="tmin" class="input" type="number" bind:value={form.nozzle_temp_min} placeholder="optional" /></div>
        <div class="fld"><label for="tmax">Nozzle °C max</label><input id="tmax" class="input" type="number" bind:value={form.nozzle_temp_max} placeholder="optional" /></div>
        <div class="fld"><label for="qty">Quantity</label><input id="qty" class="input" type="number" min="1" max="50" bind:value={form.quantity} /></div>
      </div>
      <div class="fld"><label for="nt">Note</label><input id="nt" class="input" bind:value={form.note} placeholder="optional" /></div>
      {#if addErr}<p class="err">{addErr}</p>{/if}
      <div class="flex gap dactions">
        <button class="btn btn-primary" onclick={saveAdd} disabled={saving}>{saving ? 'Saving…' : (Number(form.quantity) > 1 ? `Add ${form.quantity} spools` : 'Add spool')}</button>
        <button class="btn btn-ghost" onclick={() => (showAdd = false)} disabled={saving}>Cancel</button>
      </div>
    </div>
  </div>
{/if}

{#if adjust}
  <div class="overlay no-print" role="presentation" onclick={() => (adjust = null)}>
    <div class="dialog card sm" role="dialog" onclick={(e) => e.stopPropagation()}>
      <div class="dhead"><div><span class="eyebrow">Adjust / log waste</span><h3>{adjust.name}</h3></div><button class="btn btn-ghost btn-sm" onclick={() => (adjust = null)}>✕</button></div>
      <div class="fld"><label for="rem">Remaining weight (g){#if adjust.total} of {adjust.total}{/if}</label><input id="rem" class="input" type="number" min="0" max={adjust.total || undefined} bind:value={adjust.remaining} /></div>
      <p class="muted hint">Set the true remaining weight — e.g. after weighing the spool, or to write off wasted filament. Used weight is recalculated so cost stays accurate.</p>
      <div class="flex gap dactions">
        <button class="btn btn-primary" onclick={saveAdjust} disabled={adjBusy}>{adjBusy ? 'Saving…' : 'Save'}</button>
        <button class="btn btn-ghost" onclick={() => (adjust = null)} disabled={adjBusy}>Cancel</button>
      </div>
    </div>
  </div>
{/if}

{#if labelSpool}
  <div class="printonly">
    <div class="label-card">
      <div class="lc-top"><span class="lc-dot" style="background:{labelSpool.color || '#ccc'}"></span><span class="lc-name">{labelSpool.name}</span></div>
      <div class="lc-mat">{labelSpool.material}{#if labelSpool.colorName} · {labelSpool.colorName}{/if}</div>
      <div class="lc-row"><span>{labelSpool.brand || '—'}</span><span>{labelSpool.total ? labelSpool.total + ' g' : ''}</span></div>
      {#if labelSpool.location}<div class="lc-loc">📍 {labelSpool.location}</div>{/if}
      <div class="lc-foot">OpenPrintHQ</div>
    </div>
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; }
  .head h1 { margin: 0; }
  .empty { text-align: center; padding: 2.6rem; }
  .empty .ic { font-size: 1.8rem; margin-bottom: 0.3rem; }
  .empty p { max-width: 48ch; margin: 0.6rem auto 0; }
  .spools { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
  .dot { width: 22px; height: 22px; border-radius: 50%; border: 1px solid var(--ophq-border); flex: none; }
  .sname { font-weight: 500; }
  .mat { font-size: 0.8rem; }
  .bar { height: 6px; background: var(--ophq-bg-2); border-radius: 3px; margin-top: 0.8rem; overflow: hidden; }
  .fill { height: 100%; background: var(--ophq-primary); }
  .rem { font-size: 0.8rem; margin-top: 0.4rem; }
  .grow { flex: 1; }
  .sumrow { display: flex; gap: 1rem; margin-bottom: 1.2rem; flex-wrap: wrap; }
  .st { display: flex; flex-direction: column; gap: 0.2rem; min-width: 130px; }
  .st b { font-family: var(--font-mono); font-size: 1.4rem; }
  .sfoot { display: flex; align-items: center; justify-content: space-between; gap: 0.6rem; margin-top: 0.7rem; padding-top: 0.6rem; border-top: 1px solid var(--ophq-border-soft); }
  .tiny { font-size: 0.74rem; }
  .btn-xs { padding: 0.12rem 0.5rem; font-size: 0.72rem; }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .toast { padding: 0.7rem 1rem; border-radius: var(--radius-sm); margin-bottom: 1.1rem; font-size: 0.9rem; border: 1px solid; }
  .toast.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .toast.err { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .empty .btn { margin-top: 1rem; }
  .acts { display: flex; align-items: center; gap: 0.3rem; flex-wrap: wrap; }

  .overlay { position: fixed; inset: 0; background: rgba(5,8,12,0.66); backdrop-filter: blur(3px); display: grid; place-items: center; z-index: 100; padding: 1.5rem; }
  .dialog { width: 100%; max-width: 520px; padding: 1.5rem; box-shadow: var(--shadow-glow); }
  .dialog.sm { max-width: 380px; }
  .dhead { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1rem; }
  .dhead h3 { margin: 0.2rem 0 0; font-size: 1.15rem; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.7rem; }
  .fld { display: flex; flex-direction: column; gap: 0.3rem; margin-top: 0.7rem; }
  .grid2 .fld { margin-top: 0; }
  .fld label { font-size: 0.78rem; color: var(--ophq-text-2); }
  .input.color { padding: 0.15rem; height: 38px; }
  .dactions { margin-top: 1.2rem; }
  .hint { font-size: 0.8rem; margin: 0.5rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0.7rem 0 0; }

  /* printable label — hidden on screen, shown alone when printing */
  .printonly { display: none; }
  @media print {
    :global(.side) { display: none !important; }
    :global(.shell) * { visibility: hidden !important; }
    .printonly, .printonly * { visibility: visible !important; }
    .printonly { display: block !important; position: fixed; top: 0; left: 0; }
    .no-print { display: none !important; }
    @page { margin: 0.6cm; }
  }
  .label-card { width: 62mm; border: 1px solid #333; border-radius: 3mm; padding: 3mm 4mm; color: #111; font-family: var(--font-sans, sans-serif); }
  .lc-top { display: flex; align-items: center; gap: 2mm; }
  .lc-dot { width: 6mm; height: 6mm; border-radius: 50%; border: 0.3mm solid #333; flex: none; }
  .lc-name { font-weight: 700; font-size: 4mm; }
  .lc-mat { font-size: 3.2mm; margin: 1.5mm 0; }
  .lc-row { display: flex; justify-content: space-between; font-size: 3mm; color: #333; }
  .lc-loc { font-size: 3mm; margin-top: 1mm; }
  .lc-foot { margin-top: 2mm; font-size: 2.6mm; color: #777; text-align: right; letter-spacing: 0.02em; }
</style>
