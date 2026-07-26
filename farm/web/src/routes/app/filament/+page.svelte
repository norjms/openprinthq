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
</script>

<svelte:head><title>Filament · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div><h1>Filament</h1><p class="muted">Spool inventory, usage and cost.</p></div>
  <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
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
          {#if confirmArch === s.id}
            <span class="flex gap center"><button class="btn btn-danger btn-xs" onclick={() => archive(s)} disabled={busyId === s.id}>Confirm</button><button class="btn btn-ghost btn-xs" onclick={() => (confirmArch = null)}>✕</button></span>
          {:else}
            <button class="btn btn-ghost btn-xs" onclick={() => (confirmArch = s.id)} disabled={busyId === s.id} title="Archive (used up / removed)">Archive</button>
          {/if}
        </div>
      </div>
    {/each}
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
</style>
