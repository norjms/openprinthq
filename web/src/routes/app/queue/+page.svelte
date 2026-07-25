<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let items = $state([]);

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.items || d?.queue || d?.results || []);
    return arr.map((q) => ({
      id: q.id ?? q.queue_id,
      name: q.name ?? q.filename ?? q.file_name ?? q.display_name ?? 'Job',
      status: (q.status ?? q.state ?? 'queued').toString(),
      printer: q.printer_name ?? q.printer ?? q.target_printer ?? '',
      qty: q.quantity ?? q.amount ?? 1
    }));
  }
  async function load() {
    loading = true; error = null;
    try { items = norm(await api.queue()); }
    catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(load);
</script>

<svelte:head><title>Print queue · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div><h1>Print queue</h1><p class="muted">One queue across your whole fleet.</p></div>
  <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
</div>

{#if loading}
  <div class="card card-pad muted">Loading queue…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else if items.length === 0}
  <div class="card card-pad empty">
    <div class="ic">≣</div>
    <h3>Queue is empty</h3>
    <p class="muted">Add printers and send jobs — queued prints across your whole fleet show up here.</p>
    <a class="btn btn-primary" href="/app/files">Go to files</a>
  </div>
{:else}
  <div class="card list">
    <div class="row head-row muted mono"><span>#</span><span>Job</span><span>Printer</span><span>Qty</span><span>Status</span></div>
    {#each items as q, i}
      <div class="row">
        <span class="mono muted">{i + 1}</span>
        <span class="name">{q.name}</span>
        <span class="muted">{q.printer || '—'}</span>
        <span class="mono">{q.qty}</span>
        <span class="chip">{q.status}</span>
      </div>
    {/each}
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; }
  .head h1 { margin: 0; }
  .empty { text-align: center; padding: 2.6rem; }
  .empty .ic { font-size: 1.8rem; margin-bottom: 0.3rem; }
  .empty p { max-width: 46ch; margin: 0.6rem auto 1.4rem; }
  .list { overflow: hidden; }
  .row { display: grid; grid-template-columns: 40px 1fr 160px 60px 120px; gap: 1rem; align-items: center; padding: 0.8rem 1.2rem; border-bottom: 1px solid var(--ophq-border); }
  .row:last-child { border-bottom: none; }
  .head-row { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .name { font-weight: 500; }
</style>
