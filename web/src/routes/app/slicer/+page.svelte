<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';

  let loading = $state(true);
  let error = $state(null);
  let models = $state([]);
  let presetCats = $state([]);
  let connected = $state(false);

  async function load() {
    loading = true; error = null;
    try {
      const m = await api.slicerModels();
      models = m && typeof m === 'object' && !Array.isArray(m) ? Object.keys(m) : (Array.isArray(m) ? m : []);
      connected = models.length > 0;
      try {
        const p = await api.slicerPresets();
        presetCats = p && typeof p === 'object' ? Object.keys(p).filter((k) => !k.endsWith('_status')) : [];
      } catch { presetCats = []; }
    } catch (e) {
      error = e.status === 409 ? 'no-instance' : (e.message || 'slicer unreachable');
    } finally {
      loading = false;
    }
  }
  onMount(load);
</script>

<PageTitle page="Slicer" />

<div class="head">
  <div><h1>Slicer</h1><p class="muted">OrcaSlicer, built in — slice server-side, no desktop app.</p></div>
  <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
</div>

{#if loading}
  <div class="card card-pad muted">Checking slicer…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Slicer unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else}
  <div class="card card-pad status glow">
    <div class="flex between center">
      <div class="flex center gap">
        <span class="dot" class:on={connected}></span>
        <div>
          <h3>OrcaSlicer {connected ? 'connected' : 'not reachable'}</h3>
          <p class="muted">Server-side slicing engine{connected ? ' — ready to slice STL & 3MF' : ''}.</p>
        </div>
      </div>
      <span class="chip {connected ? 'ok' : 'accent'}">{connected ? 'ready' : 'offline'}</span>
    </div>
  </div>

  <div class="grid two">
    <div class="card card-pad">
      <span class="eyebrow">Printer models ({models.length})</span>
      <div class="chips">
        {#each models.slice(0, 24) as m}<span class="chip">{m}</span>{/each}
        {#if models.length > 24}<span class="chip">+{models.length - 24} more</span>{/if}
        {#if models.length === 0}<p class="muted">—</p>{/if}
      </div>
    </div>
    <div class="card card-pad">
      <span class="eyebrow">Preset sources</span>
      <div class="chips">
        {#each presetCats as c}<span class="chip primary">{c}</span>{/each}
        {#if presetCats.length === 0}<p class="muted">—</p>{/if}
      </div>
    </div>
  </div>

  <div class="card card-pad how">
    <span class="eyebrow">How to slice</span>
    <p class="muted">Upload a model in <a href="/app/files">Files</a>, then use its <b>Slice</b> action. Pick a printer, process and filament (searchable, and auto-filtered to what's compatible with the printer) — OrcaSlicer runs it server-side and, with "Slice &amp; queue" on, the result drops straight into your <a href="/app/queue">Print queue</a>.</p>
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; }
  .head h1 { margin: 0; }
  .status { margin-bottom: 1.2rem; }
  .status h3 { margin: 0; font-size: 1.1rem; }
  .status p { margin: 0.2rem 0 0; font-size: 0.9rem; }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--ophq-faint); box-shadow: 0 0 0 4px rgba(127,140,161,0.15); }
  .dot.on { background: var(--ophq-success); box-shadow: 0 0 0 4px rgba(53,196,107,0.18); }
  .two { grid-template-columns: 1fr 1fr; }
  .chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.7rem; }
  .how { margin-top: 1.2rem; }
  .how p { margin: 0.5rem 0 0; }
  @media (max-width: 820px) { .two { grid-template-columns: 1fr; } }
</style>
