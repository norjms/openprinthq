<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let me = $state(null);
  let inst = $state(null);
  let err = $state(null);

  onMount(async () => {
    try {
      me = await api.me();
      inst = await api.myInstance().catch(() => null);
    } catch (e) { err = e.message; }
  });
</script>

<svelte:head><title>Settings · OpenPrintHQ</title></svelte:head>

<h1>Settings</h1>
<p class="muted lead">Your account and instance.</p>

<div class="grid two">
  <div class="card card-pad">
    <span class="eyebrow">Account</span>
    <div class="kv mono">
      <div><span>email</span>{me?.email ?? '…'}</div>
      <div><span>name</span>{me?.displayName ?? '—'}</div>
      <div><span>auth</span>Authentik SSO</div>
    </div>
    <a href="/logout" class="btn btn-ghost btn-sm">Sign out</a>
  </div>

  <div class="card card-pad">
    <span class="eyebrow">Your instance</span>
    {#if inst}
      <div class="kv mono">
        <div><span>status</span><span class="chip {inst.status === 'running' ? 'ok' : 'accent'}">{inst.status}</span></div>
        <div><span>subdomain</span>{inst.subdomain}</div>
        <div><span>engine</span>{inst.engineVersion}</div>
        <div><span>created</span>{inst.createdAt}</div>
      </div>
    {:else}
      <p class="muted">No instance yet — provision one from the <a href="/app">overview</a>.</p>
    {/if}
  </div>
</div>

<div class="card card-pad more">
  <span class="eyebrow">Coming soon</span>
  <p class="muted">Instance controls (restart, backup/restore), notification channels, API keys &amp; webhooks, and SSO session management.</p>
</div>

{#if err}<p class="err">{err}</p>{/if}

<style>
  .lead { margin: 0.3rem 0 1.4rem; }
  .two { grid-template-columns: 1fr 1fr; }
  .kv { display: grid; gap: 0.5rem; margin: 0.8rem 0 1rem; font-size: 0.9rem; }
  .kv > div { display: flex; gap: 1rem; align-items: center; color: var(--ophq-text-2); }
  .kv span:first-child { color: var(--ophq-faint); width: 90px; display: inline-block; }
  .more { margin-top: 1.2rem; }
  .err { color: var(--ophq-danger); }
  @media (max-width: 820px) { .two { grid-template-columns: 1fr; } }
</style>
