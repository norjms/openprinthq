<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let printers = $state([]);
  let timer = null;

  function base(data) {
    const arr = Array.isArray(data) ? data : (data?.printers || data?.items || data?.results || []);
    return arr.map((p) => ({
      id: p.id ?? p.printer_id ?? p.serial ?? p.name,
      name: p.name ?? p.friendly_name ?? p.model ?? 'Printer',
      model: p.model ?? p.printer_type ?? p.type ?? '',
      vendor: p.connection_type ?? p.vendor ?? p.brand ?? ''
    }));
  }

  async function load(initial = true) {
    if (initial) { loading = true; error = null; }
    try {
      const list = base(await api.printers());
      // Live state comes from the per-printer status endpoint, not the list.
      const live = await Promise.all(list.map((p) => api.printerStatus(p.id).catch(() => null)));
      printers = list.map((p, i) => ({ ...p, live: live[i] }));
      error = null;
    } catch (e) {
      error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable');
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    load(true);
    timer = setInterval(() => load(false), 5000);
    return () => clearInterval(timer);
  });

  function statusOf(p) {
    if (!p.live) return 'unknown';
    if (!p.live.connected) return 'offline';
    return (p.live.state || 'idle').toString().toLowerCase();
  }
  function tone(s) {
    if (/run|print/.test(s)) return 'primary';
    if (/pause/.test(s)) return 'accent';
    if (/idle|ready|online/.test(s)) return 'ok';
    if (/finish|done|complete/.test(s)) return 'ok';
    if (/error|offline|fault|fail/.test(s)) return 'danger';
    return '';
  }
  const t1 = (v) => (v == null ? null : Math.round(Number(v)));
</script>

<svelte:head><title>Printers · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div>
    <h1>Printers</h1>
    <p class="muted">Bambu Lab, Creality, Prusa, Snapmaker and Voron — one fleet.</p>
  </div>
  <div class="flex gap">
    <button class="btn btn-ghost btn-sm" onclick={() => load(false)}>Refresh</button>
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
    <button class="btn btn-ghost btn-sm" onclick={() => load()}>Retry</button>
  </div>
{:else if printers.length === 0}
  <div class="card card-pad empty">
    <div class="ic">🖨️</div>
    <h3>No printers yet</h3>
    <p class="muted">Your engine is live and ready. Add your first printer — Bambu, Klipper (Mainsail/Fluidd), Prusa, Snapmaker and more are supported out of the box.</p>
    <a class="btn btn-primary" href="/app/printers/add">+ Add your first printer</a>
  </div>
{:else}
  <div class="grid printers">
    {#each printers as p (p.id)}
      {@const st = statusOf(p)}
      <a class="card card-pad printer" href="/app/printers/{p.id}">
        <div class="flex between center">
          <h3>{p.name}</h3>
          <span class="chip {tone(st)}">{st}</span>
        </div>
        <div class="meta mono">
          {#if p.vendor}<span>{p.vendor}</span>{/if}
          {#if p.model}<span>{p.model}</span>{/if}
        </div>
        {#if p.live?.connected}
          <div class="temps mono">
            {#if t1(p.live.temperatures?.nozzle) != null}<span>◦ {t1(p.live.temperatures.nozzle)}°</span>{/if}
            {#if t1(p.live.temperatures?.bed) != null}<span>▱ {t1(p.live.temperatures.bed)}°</span>{/if}
            {#if /run|print/.test(st) && p.live.progress != null}<span class="prog">{Math.round(p.live.progress)}%</span>{/if}
          </div>
          {#if /run|print/.test(st) && p.live.progress != null}
            <div class="bar"><div class="fill" style="width:{Math.min(100, Math.max(0, p.live.progress))}%"></div></div>
          {/if}
        {/if}
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
  .temps { display: flex; gap: 0.9rem; margin-top: 0.6rem; color: var(--ophq-text-2); font-size: 0.85rem; }
  .temps .prog { color: var(--ophq-primary-2); margin-left: auto; }
  .bar { height: 6px; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; margin-top: 0.5rem; }
  .fill { height: 100%; background: linear-gradient(90deg, var(--ophq-primary), var(--ophq-primary-2)); transition: width 0.4s ease; }
  .chip.danger { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .chip.accent { color: var(--ophq-accent); border-color: rgba(255,176,32,0.3); background: rgba(255,176,32,0.08); }
  .chip.primary { color: var(--ophq-primary-2); border-color: rgba(124,108,255,0.35); background: var(--ophq-primary-dim); }
  .empty { text-align: center; padding: 2.6rem; }
  .empty .ic { font-size: 2rem; margin-bottom: 0.4rem; }
  .empty p { max-width: 48ch; margin: 0.6rem auto 1.4rem; }
</style>
