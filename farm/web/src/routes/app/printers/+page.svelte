<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let printers = $state([]);

  function normalize(data) {
    const arr = Array.isArray(data) ? data : (data?.printers || data?.items || data?.results || []);
    return arr.map((p) => ({
      id: p.id ?? p.printer_id ?? p.serial ?? p.name,
      name: p.name ?? p.friendly_name ?? p.model ?? 'Printer',
      model: p.model ?? p.printer_type ?? p.type ?? '',
      vendor: p.vendor ?? p.brand ?? (p.printer_type || '').split(/[_-]/)[0] ?? '',
      status: (p.status ?? p.state ?? p.connection_status ?? 'unknown').toString()
    }));
  }

  async function load() {
    loading = true; error = null;
    try {
      printers = normalize(await api.printers());
    } catch (e) {
      error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable');
    } finally {
      loading = false;
    }
  }
  onMount(load);

  function tone(status) {
    const s = status.toLowerCase();
    if (/print|running/.test(s)) return 'primary';
    if (/online|idle|ready/.test(s)) return 'ok';
    if (/error|offline|fault/.test(s)) return 'danger';
    return '';
  }
</script>

<svelte:head><title>Printers · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div>
    <h1>Printers</h1>
    <p class="muted">Bambu Lab, Creality, Prusa, Snapmaker and Voron — one fleet.</p>
  </div>
  <div class="flex gap">
    <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
    <a class="btn btn-primary btn-sm" href="/app/printers/add">+ Add printer</a>
  </div>
</div>

{#if loading}
  <div class="card card-pad muted">Connecting to your engine…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad">
    <h3>No instance yet</h3>
    <p class="muted">Provision your instance from the <a href="/app">overview</a> to start adding printers.</p>
  </div>
{:else if error}
  <div class="card card-pad">
    <h3>Engine unreachable</h3>
    <p class="muted">{error}</p>
    <button class="btn btn-ghost btn-sm" onclick={load}>Retry</button>
  </div>
{:else if printers.length === 0}
  <div class="card card-pad empty">
    <div class="ic">🖨️</div>
    <h3>No printers yet</h3>
    <p class="muted">Your engine is live and ready. Add your first printer — Bambu, Creality/Voron (Klipper), Prusa, Snapmaker and more are supported out of the box.</p>
    <a class="btn btn-primary" href="/app/printers/add">+ Add your first printer</a>
  </div>
{:else}
  <div class="grid printers">
    {#each printers as p}
      <a class="card card-pad printer" href="/app/printers/{p.id}">
        <div class="flex between center">
          <h3>{p.name}</h3>
          <span class="chip {tone(p.status)}">{p.status}</span>
        </div>
        <div class="meta mono">
          {#if p.vendor}<span>{p.vendor}</span>{/if}
          {#if p.model}<span>{p.model}</span>{/if}
        </div>
      </a>
    {/each}
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; gap: 1rem; }
  .head h1 { margin: 0; }
  .printers { grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
  .printer { display: block; color: var(--ophq-text); text-decoration: none; transition: border 0.15s, transform 0.15s; }
  .printer:hover { border-color: var(--ophq-primary); transform: translateY(-2px); color: var(--ophq-text); }
  .printer h3 { margin: 0; font-size: 1.05rem; color: var(--ophq-text); }
  .printer .meta { display: flex; gap: 0.6rem; margin-top: 0.5rem; color: var(--ophq-muted); font-size: 0.85rem; }
  .chip.danger { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .empty { text-align: center; padding: 2.6rem; }
  .empty .ic { font-size: 2rem; margin-bottom: 0.4rem; }
  .empty p { max-width: 48ch; margin: 0.6rem auto 1.4rem; }
</style>
