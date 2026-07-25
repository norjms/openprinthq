<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let me = $state(null);
  let inst = $state(null);
  let err = $state(null);

  // electricity rate (engine setting energy_cost_per_kwh)
  let rate = $state('');
  let currency = $state('USD');
  let rateSaving = $state(false);
  let rateMsg = $state(null);
  const currencySymbol = $derived(({ USD: '$', EUR: '€', GBP: '£', CAD: '$', AUD: '$', JPY: '¥' })[currency] || (currency + ' '));

  onMount(async () => {
    try {
      me = await api.me();
      inst = await api.myInstance().catch(() => null);
      const s = await api.engineSettings().catch(() => null);
      if (s) {
        rate = (s.energy_cost_per_kwh ?? '').toString();
        currency = s.currency || 'USD';
      }
    } catch (e) { err = e.message; }
  });

  async function saveRate() {
    const v = parseFloat(rate);
    if (isNaN(v) || v < 0) { rateMsg = { kind: 'err', text: 'Enter a valid rate.' }; return; }
    rateSaving = true; rateMsg = null;
    try {
      await api.updateEngineSettings({ energy_cost_per_kwh: String(v) });
      rate = String(v);
      rateMsg = { kind: 'ok', text: 'Saved.' };
    } catch (e) {
      rateMsg = { kind: 'err', text: e.message || 'could not save' };
    } finally {
      rateSaving = false;
    }
  }

  function fmtDate(s) {
    if (!s) return '—';
    const d = new Date(s);
    return isNaN(d) ? s : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }
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
        <div><span>created</span>{fmtDate(inst.createdAt)}</div>
      </div>
    {:else}
      <p class="muted">No instance yet — provision one from the <a href="/app">overview</a>.</p>
    {/if}
  </div>
</div>

<div class="card card-pad rate-card">
  <span class="eyebrow">Electricity rate</span>
  <p class="muted">Cost per kWh used to calculate energy cost in <a href="/app/statistics">Statistics</a>.</p>
  <div class="rate-row">
    <span class="cur mono">{currencySymbol}</span>
    <input class="input rate-in" type="number" step="0.01" min="0" bind:value={rate} placeholder="0.15" />
    <span class="muted per">per kWh</span>
    <button class="btn btn-primary btn-sm" onclick={saveRate} disabled={rateSaving}>{rateSaving ? 'Saving…' : 'Save'}</button>
  </div>
  {#if rateMsg}<p class={rateMsg.kind === 'ok' ? 'ok-msg' : 'err'}>{rateMsg.text}</p>{/if}
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
  .rate-card { margin-top: 1.2rem; }
  .rate-card p { margin: 0.3rem 0 0.9rem; font-size: 0.9rem; }
  .rate-row { display: flex; align-items: center; gap: 0.6rem; }
  .rate-row .cur { color: var(--ophq-text-2); font-size: 1rem; }
  .rate-in { max-width: 130px; }
  .rate-row .per { font-size: 0.88rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; margin: 0.6rem 0 0; }
  .more { margin-top: 1.2rem; }
  .err { color: var(--ophq-danger); }
  @media (max-width: 820px) { .two { grid-template-columns: 1fr; } }
</style>
