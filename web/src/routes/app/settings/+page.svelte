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

  // power circuits (for temperature-staggered batch printing)
  let circPrinters = $state([]);   // [{id, name, circuit}]
  let circSaving = $state(false);
  let circMsg = $state(null);

  onMount(async () => {
    try {
      me = await api.me();
      inst = await api.myInstance().catch(() => null);
      const s = await api.engineSettings().catch(() => null);
      if (s) {
        rate = (s.energy_cost_per_kwh ?? '').toString();
        currency = s.currency || 'USD';
      }
      await loadCircuits();
    } catch (e) { err = e.message; }
  });

  async function loadCircuits() {
    try {
      const [pl, map] = await Promise.all([api.printers().catch(() => []), api.circuits().catch(() => ({}))]);
      const arr = Array.isArray(pl) ? pl : (pl?.printers || pl?.items || []);
      circPrinters = arr.map((p) => ({
        id: p.id ?? p.printer_id,
        name: p.name || p.model || ('Printer ' + (p.id ?? '')),
        circuit: map[p.id ?? p.printer_id] || ''
      }));
    } catch { /* no instance yet */ }
  }

  async function saveCircuits() {
    circSaving = true; circMsg = null;
    try {
      const map = {};
      for (const p of circPrinters) map[p.id] = (p.circuit || '').trim();
      await api.saveCircuits(map);
      circMsg = { kind: 'ok', text: 'Circuits saved.' };
    } catch (e) {
      circMsg = { kind: 'err', text: e.message || 'could not save' };
    } finally { circSaving = false; }
  }

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
        <div><span>instance</span>{inst.subdomain}</div>
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

<div class="card card-pad circ-card">
  <span class="eyebrow">Power circuits</span>
  <p class="muted">Group printers by the breaker circuit they're plugged into. <a href="/app/queue">Temperature-staggered batch printing</a> serialises heat-up within a circuit so machines on the same breaker never surge together — printers on different circuits preheat in parallel. Leave blank for a printer that shares no circuit.</p>
  {#if circPrinters.length === 0}
    <p class="muted">No printers yet — add printers to assign circuits.</p>
  {:else}
    <div class="circ-list">
      {#each circPrinters as p (p.id)}
        <div class="circ-row">
          <span class="cn">{p.name}</span>
          <input class="input circ-in" type="text" placeholder="e.g. Circuit A" bind:value={p.circuit} list="circuit-suggest" />
        </div>
      {/each}
    </div>
    <datalist id="circuit-suggest">
      {#each [...new Set(circPrinters.map((p) => p.circuit).filter(Boolean))] as c}<option value={c}></option>{/each}
    </datalist>
    <div class="flex gap center circ-actions">
      <button class="btn btn-primary btn-sm" onclick={saveCircuits} disabled={circSaving}>{circSaving ? 'Saving…' : 'Save circuits'}</button>
      {#if circMsg}<span class={circMsg.kind === 'ok' ? 'ok-msg' : 'err'}>{circMsg.text}</span>{/if}
    </div>
  {/if}
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
  .circ-card { margin-top: 1.2rem; }
  .circ-card p { margin: 0.3rem 0 1rem; font-size: 0.9rem; max-width: 70ch; }
  .circ-list { display: flex; flex-direction: column; gap: 0.5rem; }
  .circ-row { display: flex; align-items: center; gap: 1rem; }
  .circ-row .cn { flex: 1; font-size: 0.92rem; }
  .circ-in { max-width: 200px; }
  .circ-actions { margin-top: 1rem; }
  .more { margin-top: 1.2rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  @media (max-width: 820px) { .two { grid-template-columns: 1fr; } }
</style>
