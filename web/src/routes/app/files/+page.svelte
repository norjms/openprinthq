<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let files = $state([]);

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.files || d?.items || d?.results || []);
    return arr.map((f) => ({
      id: f.id ?? f.file_id,
      name: f.name ?? f.filename ?? f.display_name ?? 'file',
      size: f.size ?? f.file_size ?? null,
      kind: (f.name ?? f.filename ?? '').split('.').pop()?.toUpperCase() ?? ''
    }));
  }
  function human(n) {
    if (!n && n !== 0) return '';
    const u = ['B', 'KB', 'MB', 'GB']; let i = 0; let v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(v < 10 && i > 0 ? 1 : 0) + ' ' + u[i];
  }
  async function load() {
    loading = true; error = null;
    try { files = norm(await api.files()); }
    catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(load);
</script>

<svelte:head><title>Files · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div><h1>Files</h1><p class="muted">Your private 3MF / STL / G-code library.</p></div>
  <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
</div>

{#if loading}
  <div class="card card-pad muted">Loading library…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else if files.length === 0}
  <div class="card card-pad empty">
    <div class="ic">🗀</div>
    <h3>No files yet</h3>
    <p class="muted">Upload 3MF, STL or G-code to your private library — they'll be sliceable and printable across your fleet.</p>
  </div>
{:else}
  <div class="grid files">
    {#each files as f}
      <div class="card card-pad file">
        <div class="kind mono">{f.kind}</div>
        <div class="fname">{f.name}</div>
        {#if f.size}<div class="muted mono sz">{human(f.size)}</div>{/if}
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
  .files { grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }
  .file { display: flex; flex-direction: column; gap: 0.4rem; }
  .kind { font-size: 0.72rem; color: var(--ophq-primary-2); }
  .fname { font-weight: 500; word-break: break-word; font-size: 0.92rem; }
  .sz { font-size: 0.8rem; }
</style>
