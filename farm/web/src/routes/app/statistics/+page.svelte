<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let s = $state(null);

  async function load() {
    loading = true; error = null;
    try { s = await api.printStats(); }
    catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(load);

  const successRate = $derived(s && s.total_prints > 0 ? Math.round((s.successful_prints / s.total_prints) * 100) : null);
  const tiles = $derived(s ? [
    { label: 'Total prints', value: s.total_prints ?? 0 },
    { label: 'Success rate', value: successRate == null ? '—' : successRate + '%', ok: true },
    { label: 'Failed', value: s.failed_prints ?? 0 },
    { label: 'Print time', value: (s.total_print_time_hours ?? 0).toFixed(1) + ' h' },
    { label: 'Filament used', value: ((s.total_filament_grams ?? 0) / 1000).toFixed(2) + ' kg' },
    { label: 'Cost', value: '$' + (s.total_cost ?? 0).toFixed(2) },
    { label: 'Energy', value: (s.total_energy_kwh ?? 0).toFixed(1) + ' kWh' },
    { label: 'Energy cost', value: '$' + (s.total_energy_cost ?? 0).toFixed(2) }
  ] : []);
  const byType = $derived(s?.prints_by_filament_type ? Object.entries(s.prints_by_filament_type) : []);
  const byPrinter = $derived(s?.prints_by_printer ? Object.entries(s.prints_by_printer) : []);
</script>

<svelte:head><title>Statistics · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div><h1>Statistics</h1><p class="muted">Production analytics for your fleet.</p></div>
  <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
</div>

{#if loading}
  <div class="card card-pad muted">Loading analytics…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else}
  <div class="tiles">
    {#each tiles as t}
      <div class="card card-pad tile"><span class="muted">{t.label}</span><b class:ok={t.ok}>{t.value}</b></div>
    {/each}
  </div>
  {#if s.total_prints === 0}
    <div class="card card-pad muted note">No prints recorded yet — analytics fill in as you print across your fleet.</div>
  {:else}
    <div class="grid two">
      <div class="card card-pad">
        <span class="eyebrow">By filament type</span>
        {#if byType.length}{#each byType as [k, v]}<div class="row"><span>{k}</span><span class="mono">{v}</span></div>{/each}{:else}<p class="muted">—</p>{/if}
      </div>
      <div class="card card-pad">
        <span class="eyebrow">By printer</span>
        {#if byPrinter.length}{#each byPrinter as [k, v]}<div class="row"><span>{k}</span><span class="mono">{v}</span></div>{/each}{:else}<p class="muted">—</p>{/if}
      </div>
    </div>
  {/if}
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; }
  .head h1 { margin: 0; }
  .tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; }
  .tile { display: flex; flex-direction: column; gap: 0.3rem; }
  .tile b { font-size: 1.7rem; font-family: var(--font-mono); }
  .tile b.ok { color: var(--ophq-success); }
  .note { margin-top: 1.2rem; }
  .two { grid-template-columns: 1fr 1fr; margin-top: 1.2rem; }
  .row { display: flex; justify-content: space-between; padding: 0.4rem 0; border-bottom: 1px solid var(--ophq-border-soft); }
  .row:last-child { border-bottom: none; }
  @media (max-width: 820px) { .tiles { grid-template-columns: repeat(2, 1fr); } .two { grid-template-columns: 1fr; } }
</style>
