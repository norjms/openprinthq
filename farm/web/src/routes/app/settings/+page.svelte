<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import NotificationSettings from '$lib/components/NotificationSettings.svelte';
  import ObicoSettings from '$lib/components/ObicoSettings.svelte';
  import ApiKeys from '$lib/components/ApiKeys.svelte';
  import Connectors from '$lib/components/Connectors.svelte';
  import LookAndFeel from '$lib/components/LookAndFeel.svelte';

  // Top-level settings tab (General account/instance settings vs Look & Feel).
  let tab = $state('general');

  let me = $state(null);
  let inst = $state(null);
  let err = $state(null);

  // electricity rate (engine setting energy_cost_per_kwh)
  let rate = $state('');
  let currency = $state('USD');
  let rateSaving = $state(false);
  let rateMsg = $state(null);
  let curSaving = $state(false);
  const CURRENCIES = [
    ['USD', 'US Dollar ($)'], ['EUR', 'Euro (€)'], ['GBP', 'British Pound (£)'], ['CAD', 'Canadian Dollar ($)'],
    ['AUD', 'Australian Dollar ($)'], ['NZD', 'NZ Dollar ($)'], ['JPY', 'Japanese Yen (¥)'], ['CNY', 'Chinese Yuan (¥)'],
    ['CHF', 'Swiss Franc'], ['SEK', 'Swedish Krona'], ['NOK', 'Norwegian Krone'], ['DKK', 'Danish Krone'],
    ['PLN', 'Polish Złoty'], ['INR', 'Indian Rupee (₹)'], ['ZAR', 'South African Rand'], ['BRL', 'Brazilian Real'],
    ['MXN', 'Mexican Peso ($)']
  ];
  const CUR_SYM = { USD: '$', EUR: '€', GBP: '£', CAD: '$', AUD: '$', NZD: '$', JPY: '¥', CNY: '¥', CHF: 'CHF ', SEK: 'kr ', NOK: 'kr ', DKK: 'kr ', PLN: 'zł ', INR: '₹', ZAR: 'R ', BRL: 'R$ ', MXN: '$' };
  const currencySymbol = $derived(CUR_SYM[currency] || (currency + ' '));
  async function saveCurrency(v) {
    currency = v; curSaving = true; rateMsg = null;
    try { await api.updateEngineSettings({ currency: v }); rateMsg = { kind: 'ok', text: 'Currency saved.' }; }
    catch (e) { rateMsg = { kind: 'err', text: e.message || 'could not save currency' }; }
    finally { curSaving = false; }
  }

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
      cloud = await api.cloudStatus().catch(() => null);
      await loadToken();
    } catch (e) { err = e.message; }
  });

  let cloud = $state(null);

  // integrations (HA / Homepage / Prometheus)
  let intToken = $state(null);
  let intReveal = $state(false);
  let intBusy = $state(false);
  let intTab = $state('homepage');
  let copied = $state(null);
  const origin = $derived(typeof window !== 'undefined' ? window.location.origin : 'https://internal.example.com');
  const summaryUrl = $derived(intToken ? `${origin}/api/pub/summary?token=${intToken}` : '');
  const metricsUrl = $derived(intToken ? `${origin}/api/pub/metrics?token=${intToken}` : '');

  async function loadToken() {
    try { intToken = (await api.integrationToken())?.token || null; } catch { intToken = null; }
  }
  async function regenToken() {
    intBusy = true;
    try { intToken = (await api.regenIntegrationToken())?.token || null; }
    catch { /* ignore */ } finally { intBusy = false; }
  }
  async function copy(text, which) {
    try { await navigator.clipboard.writeText(text); copied = which; setTimeout(() => (copied = null), 1500); } catch { /* ignore */ }
  }
  const homepageSnippet = $derived(`- OpenPrintHQ:
    widget:
      type: customapi
      url: ${summaryUrl}
      refreshInterval: 10000
      mappings:
        - field: printers_online
          label: Online
        - field: active_jobs
          label: Printing
        - field: queued
          label: Queued`);
  const haSnippet = $derived(`# configuration.yaml
sensor:
  - platform: rest
    name: OpenPrintHQ
    resource: ${summaryUrl}
    value_template: "{{ value_json.printers_online }}"
    json_attributes:
      - printers_total
      - active_jobs
      - queued
      - printers
    scan_interval: 15`);
  const promSnippet = $derived(`# prometheus.yml
scrape_configs:
  - job_name: openprinthq
    scrape_interval: 30s
    metrics_path: /api/pub/metrics
    params:
      token: ["${intToken || 'YOUR_TOKEN'}"]
    scheme: https
    static_configs:
      - targets: ["${origin.replace(/^https?:\/\//, '')}"]`);

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

<PageTitle page="Settings" />

<h1>Settings</h1>
<p class="muted lead">Your account and instance.</p>

<div class="pagetabs" role="tablist" aria-label="Settings sections">
  <button role="tab" aria-selected={tab === 'general'} class:on={tab === 'general'} onclick={() => (tab = 'general')}>General</button>
  <button role="tab" aria-selected={tab === 'look'} class:on={tab === 'look'} onclick={() => (tab = 'look')}>Look &amp; Feel</button>
</div>

{#if tab === 'look'}
  <LookAndFeel />
{:else}
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
  <span class="eyebrow">Currency &amp; electricity rate</span>
  <p class="muted">Currency and per-kWh cost used for all cost figures in <a href="/app/statistics">Statistics</a> and filament stock value.</p>
  <div class="cur-row">
    <label for="cursel">Currency</label>
    <select id="cursel" class="input cursel" value={currency} onchange={(e) => saveCurrency(e.target.value)} disabled={curSaving}>
      {#each CURRENCIES as [code, label]}<option value={code}>{label}</option>{/each}
    </select>
  </div>
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

{#if cloud}
  <div class="card card-pad cloud-card">
    <span class="eyebrow">Cloud slicing presets</span>
    <p class="muted">Connect a Bambu / MakerWorld cloud account to pull your cloud filament &amp; process presets into the slicer.</p>
    <div class="kv mono">
      <div><span>status</span><span class="chip {cloud.is_authenticated ? 'ok' : 'accent'}">{cloud.is_authenticated ? 'connected' : 'not connected'}</span></div>
      {#if cloud.is_authenticated}
        <div><span>account</span>{cloud.email ?? '—'}</div>
        <div><span>region</span>{cloud.region ?? '—'}</div>
      {/if}
    </div>
    {#if !cloud.is_authenticated}<p class="muted tiny">Sign in from the slicer's preset picker to enable cloud presets.</p>{/if}
  </div>
{/if}

<NotificationSettings />

<ObicoSettings />

<div class="card card-pad int-card">
  <span class="eyebrow">Integrations — Home Assistant · Homepage · Prometheus</span>
  <p class="muted">A read-only access token lets external dashboards pull your fleet status without signing in. Keep it private — anyone with it can read your fleet summary.</p>
  <div class="tokrow">
    <input class="input mono tok" type={intReveal ? 'text' : 'password'} readonly value={intToken ?? ''} />
    <button class="btn btn-ghost btn-sm" onclick={() => (intReveal = !intReveal)}>{intReveal ? 'Hide' : 'Show'}</button>
    <button class="btn btn-ghost btn-sm" onclick={() => copy(intToken, 'token')} disabled={!intToken}>{copied === 'token' ? 'Copied' : 'Copy'}</button>
    <button class="btn btn-ghost btn-sm" onclick={regenToken} disabled={intBusy}>Regenerate</button>
  </div>
  <div class="urls">
    <div><span class="muted">Summary (JSON)</span><code>{summaryUrl}</code></div>
    <div><span class="muted">Metrics (Prometheus)</span><code>{metricsUrl}</code></div>
  </div>

  <div class="tabs">
    <button class:on={intTab === 'homepage'} onclick={() => (intTab = 'homepage')}>Homepage</button>
    <button class:on={intTab === 'ha'} onclick={() => (intTab = 'ha')}>Home Assistant</button>
    <button class:on={intTab === 'prom'} onclick={() => (intTab = 'prom')}>Prometheus</button>
  </div>
  {#if intTab === 'homepage'}
    <div class="snip"><button class="cbtn" onclick={() => copy(homepageSnippet, 'hp')}>{copied === 'hp' ? 'Copied' : 'Copy'}</button><pre>{homepageSnippet}</pre></div>
    <p class="muted tiny">Add to your Homepage <code>services.yaml</code> as a custom-API widget.</p>
  {:else if intTab === 'ha'}
    <div class="snip"><button class="cbtn" onclick={() => copy(haSnippet, 'ha')}>{copied === 'ha' ? 'Copied' : 'Copy'}</button><pre>{haSnippet}</pre></div>
    <p class="muted tiny">Add to Home Assistant <code>configuration.yaml</code> — a REST sensor with fleet attributes for cards &amp; automations.</p>
  {:else}
    <div class="snip"><button class="cbtn" onclick={() => copy(promSnippet, 'pr')}>{copied === 'pr' ? 'Copied' : 'Copy'}</button><pre>{promSnippet}</pre></div>
    <p class="muted tiny">Add to <code>prometheus.yml</code>; the metrics feed Grafana dashboards.</p>
  {/if}
</div>

<ApiKeys />

<Connectors />

<div class="card card-pad more">
  <span class="eyebrow">Coming soon</span>
  <p class="muted">Instance controls (restart, backup/restore) and SSO session management.</p>
</div>

{#if err}<p class="err">{err}</p>{/if}
{/if}

<style>
  .lead { margin: 0.3rem 0 1.4rem; }
  .pagetabs { display: flex; gap: 0.4rem; margin: 0 0 1.4rem; border-bottom: 1px solid var(--ophq-border-soft); }
  .pagetabs button { background: transparent; border: 0; border-bottom: 2px solid transparent; color: var(--ophq-text-2); padding: 0.5rem 0.9rem; font-size: 0.95rem; font-weight: 600; cursor: pointer; margin-bottom: -1px; }
  .pagetabs button.on { color: var(--ophq-text); border-bottom-color: var(--ophq-primary); }
  .pagetabs button:hover { color: var(--ophq-text); }
  .two { grid-template-columns: 1fr 1fr; }
  .kv { display: grid; gap: 0.5rem; margin: 0.8rem 0 1rem; font-size: 0.9rem; }
  .kv > div { display: flex; gap: 1rem; align-items: center; color: var(--ophq-text-2); }
  .kv span:first-child { color: var(--ophq-faint); width: 90px; display: inline-block; }
  .rate-card { margin-top: 1.2rem; }
  .cur-row { display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.9rem; }
  .cur-row label { font-size: 0.88rem; color: var(--ophq-text-2); }
  .cursel { max-width: 240px; }
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
  .cloud-card { margin-top: 1.2rem; }
  .cloud-card p { margin: 0.3rem 0 0.9rem; font-size: 0.9rem; }
  .cloud-card .tiny { font-size: 0.82rem; margin-top: 0.6rem; }
  .int-card { margin-top: 1.2rem; }
  .int-card p { margin: 0.3rem 0 0.9rem; font-size: 0.9rem; max-width: 72ch; }
  .tokrow { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
  .tok { flex: 1; min-width: 220px; font-size: 0.8rem; }
  .urls { display: flex; flex-direction: column; gap: 0.4rem; margin: 0.9rem 0; }
  .urls > div { display: flex; flex-direction: column; gap: 0.2rem; }
  .urls span { font-size: 0.75rem; }
  .urls code { font-size: 0.72rem; word-break: break-all; color: var(--ophq-text-2); background: var(--ophq-bg-2); padding: 0.35rem 0.5rem; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border-soft); }
  .tabs { display: flex; gap: 0.3rem; margin: 0.8rem 0 0.6rem; }
  .tabs button { background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); color: var(--ophq-text-2); padding: 0.35rem 0.8rem; border-radius: 999px; font-size: 0.82rem; cursor: pointer; }
  .tabs button.on { border-color: var(--ophq-primary); color: var(--ophq-text); background: var(--ophq-primary-dim); }
  .snip { position: relative; }
  .snip pre { background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.8rem 0.9rem; overflow-x: auto; font-size: 0.75rem; font-family: var(--font-mono); line-height: 1.5; margin: 0; }
  .cbtn { position: absolute; top: 0.5rem; right: 0.5rem; background: var(--ophq-surface); border: 1px solid var(--ophq-border); color: var(--ophq-text-2); border-radius: var(--radius-sm); padding: 0.15rem 0.5rem; font-size: 0.72rem; cursor: pointer; }
  .int-card .tiny { font-size: 0.8rem; margin-top: 0.5rem; }
  .more { margin-top: 1.2rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  @media (max-width: 820px) { .two { grid-template-columns: 1fr; } }
</style>
