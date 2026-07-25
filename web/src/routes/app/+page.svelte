<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let instance = $state(null);
  let stats = $state(null);
  let error = $state(null);
  let provisioning = $state(false);

  async function load() {
    loading = true; error = null;
    try {
      instance = await api.myInstance();
      if (instance?.status === 'running') {
        stats = await api.stats().catch(() => null);
      }
    } catch (e) {
      error = e.status === 404 ? 'no-instance' : (e.message || 'unreachable');
    } finally {
      loading = false;
    }
  }

  async function provision() {
    provisioning = true;
    try { instance = await api.provision(); await load(); }
    catch (e) { error = e.message; }
    finally { provisioning = false; }
  }

  onMount(load);

  const tiles = $derived([
    { label: 'Printers online', value: stats?.printersOnline ?? '—', accent: 'ok' },
    { label: 'Active jobs', value: stats?.activeJobs ?? '—' },
    { label: 'Queued', value: stats?.queued ?? '—' },
    { label: 'Success rate', value: stats ? (stats.successRate ?? '—') + '%' : '—' }
  ]);
</script>

<svelte:head><title>Overview · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div>
    <h1>Overview</h1>
    <p class="muted">Your OpenPrintHQ command center.</p>
  </div>
  <a href="/app/printers" class="btn btn-primary btn-sm">+ Add printer</a>
</div>

{#if loading}
  <div class="card card-pad muted">Loading your instance…</div>
{:else if error === 'no-instance' || instance?.status === 'not_provisioned'}
  <div class="card card-pad provision glow">
    <span class="eyebrow">Welcome</span>
    <h2>Let's spin up your private HQ</h2>
    <p>You don't have an instance yet. We'll provision an isolated OpenPrintHQ — your own database and workspace — just for you.</p>
    <button class="btn btn-primary" onclick={provision} disabled={provisioning}>
      {provisioning ? 'Provisioning…' : 'Provision my instance →'}
    </button>
  </div>
{:else if error}
  <div class="card card-pad">
    <h3>Control-plane unreachable</h3>
    <p class="muted">Couldn't reach the control-plane API ({error}). It may still be starting. This panel will populate once the backend is online.</p>
    <button class="btn btn-ghost btn-sm" onclick={load}>Retry</button>
  </div>
{:else}
  <div class="tiles">
    {#each tiles as t}
      <div class="card card-pad tile">
        <span class="muted">{t.label}</span>
        <b class:ok={t.accent === 'ok'}>{t.value}</b>
      </div>
    {/each}
  </div>

  <div class="card card-pad">
    <div class="flex between">
      <h3>Instance</h3>
      <span class="chip {instance?.status === 'running' ? 'ok' : 'accent'}">{instance?.status ?? 'unknown'}</span>
    </div>
    <div class="kv mono">
      <div><span>subdomain</span>{instance?.subdomain ?? '—'}.internal.example.com</div>
      <div><span>engine</span>{instance?.engineVersion ?? 'openprinthq-engine'}</div>
      <div><span>created</span>{instance?.createdAt ?? '—'}</div>
    </div>
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; }
  .head h1 { margin: 0; }
  .provision { text-align: center; padding: 2.5rem; }
  .provision p { max-width: 52ch; margin: 0.8rem auto 1.4rem; }
  .tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 1.2rem; }
  .tile { display: flex; flex-direction: column; gap: 0.3rem; }
  .tile b { font-size: 1.9rem; font-family: var(--font-mono); }
  .tile b.ok { color: var(--ophq-success); }
  .kv { margin-top: 1rem; display: grid; gap: 0.5rem; font-size: 0.9rem; }
  .kv div { display: flex; gap: 1rem; color: var(--ophq-text-2); }
  .kv span { color: var(--ophq-faint); width: 90px; display: inline-block; }
  @media (max-width: 820px) { .tiles { grid-template-columns: repeat(2, 1fr); } }
</style>
