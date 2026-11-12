<script>
  import { base as basePath } from '$app/paths';
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import PageHead from '$lib/components/PageHead.svelte';
  import { WidgetCard, ProgressBar } from 'artisanhub-ui';
  import Icon from '$lib/components/AppIcon.svelte';
  import { branding } from '$lib/stores/appearance';
  import { printerLabel } from '$lib/models.js';
  import { markSeen } from '$lib/online.js';
  import { displayState, jobOf } from '$lib/printerState.js';

  let loading = $state(true);
  let instance = $state(null);
  let stats = $state(null);
  let error = $state(null);
  let provisioning = $state(false);
  let needAccount = $state(false);
  let needsInvite = $state(true);
  let inviteCode = $state('');
  let claiming = $state(false);
  let claimErr = $state(null);
  let host = $state('');
  let fleet = $state([]);
  let connectors = $state([]);

  async function loadFleet() {
    try {
      const d = await api.printers();
      const list = Array.isArray(d) ? d : (d?.printers || d?.items || []);
      const live = await Promise.all(list.map((p) => api.printerStatus(p.id).catch(() => null)));
      list.forEach((p, i) => markSeen(p.id, live[i]?.connected));
      fleet = list.map((p, i) => ({ ...p, live: live[i], disp: displayState(p.id, live[i]), job: jobOf(live[i]) }));
    } catch { fleet = []; }
    api.connectors().then((r) => { connectors = Array.isArray(r) ? r : (r?.connectors || []); }).catch(() => {});
  }

  async function load() {
    loading = true; error = null;
    try {
      const me = await api.me().catch(() => null);
      if (me && me.hasAccount === false) {
        needAccount = true; needsInvite = me.needsInvite !== false; loading = false; return;
      }
      needAccount = false;
      instance = await api.myInstance();
      if (instance?.status === 'running') {
        stats = await api.stats().catch(() => null);
        loadFleet();
      }
    } catch (e) {
      error = e.status === 404 ? 'no-instance' : (e.status === 403 ? 'no-account' : (e.message || 'unreachable'));
    } finally {
      loading = false;
    }
  }

  async function claim() {
    claiming = true; claimErr = null;
    try { await api.claim(inviteCode.trim()); needAccount = false; await load(); }
    catch (e) { claimErr = e.message || 'Could not create your account.'; }
    finally { claiming = false; }
  }

  async function provision() {
    provisioning = true;
    try { instance = await api.provision(); await load(); }
    catch (e) { error = e.message; }
    finally { provisioning = false; }
  }

  onMount(() => {
    host = window.location.host; load();
    const t = setInterval(() => { if (instance?.status === 'running') loadFleet(); }, 10000);
    return () => clearInterval(t);
  });

  const active = $derived(fleet.filter((p) => ['printing', 'paused', 'error'].includes(p.disp.key)));
  const needs = $derived(fleet.filter((p) => ['paused', 'error', 'clear', 'offline'].includes(p.disp.key)));
  const tiles = $derived([
    { label: 'Printers online', value: stats?.printersOnline ?? '', sub: stats ? `of ${stats.printersTotal ?? 0}` : '', ok: true, href: basePath + '/app/printers' },
    { label: 'Active jobs', value: stats?.activeJobs ?? '', sub: active.length ? `${active.filter((p) => p.disp.key === 'paused').length} paused` : '', href: basePath + '/app/queue' },
    { label: 'Queued', value: stats?.queued ?? '', sub: 'jobs waiting', href: basePath + '/app/queue' },
    { label: 'Success rate', value: stats && stats.successRate != null ? stats.successRate + '%' : '', sub: 'all prints', href: basePath + '/app/statistics' }
  ]);
  function needsText(p) {
    if (p.disp.key === 'offline') return 'Not reachable. Check its power and network.';
    if (p.disp.key === 'clear') return 'Clear the plate to take the next job.';
    if (p.disp.key === 'error') return 'Stopped with an error.';
    return p.job.name ? `Paused: ${p.job.name}` : 'Paused.';
  }
  const noPrinters = $derived(instance?.status === 'running' && (stats?.printersTotal ?? 0) === 0);

  function fmtDate(s) {
    if (!s) return '';
    const d = new Date(s);
    return isNaN(d) ? s : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }
</script>

<PageTitle page="Overview" />

<PageHead title="Overview" description="Your {$branding.siteName} command center.">
  {#snippet actions()}
    <a href="{basePath}/app/printers/add" class="btn btn-primary"><Icon name="plus" size={16} />Add printer</a>
  {/snippet}
</PageHead>

{#if loading}
  <div class="card card-pad muted">Loading your instance</div>
{:else if needAccount || error === 'no-account'}
  <div class="card card-pad provision">
    <span class="eyebrow">Almost there</span>
    <h2>Enter your invite code</h2>
    <p>You are signed in. ArtisanHub Farm is invite only: enter the code you were given to create your private HQ.{#if !needsInvite} You are the first user, so no code is needed. Just continue.{/if}</p>
    {#if needsInvite}
      <label class="sr-only" for="claim-code">Invite code</label>
      <input id="claim-code" class="input claim-input" placeholder="Invite code" bind:value={inviteCode} onkeydown={(e) => { if (e.key === 'Enter') claim(); }} />
    {/if}
    {#if claimErr}<p class="err">{claimErr}</p>{/if}
    <button class="btn btn-primary" onclick={claim} disabled={claiming || (needsInvite && !inviteCode.trim())}>{claiming ? 'Creating your HQ' : 'Create my HQ'}</button>
  </div>
{:else if error === 'no-instance' || instance?.status === 'not_provisioned'}
  <div class="card card-pad provision">
    <span class="eyebrow">Welcome</span>
    <h2>Set up your private HQ</h2>
    <p>You do not have an instance yet. ArtisanHub Farm provisions an isolated one, with its own database and workspace, just for you.</p>
    <button class="btn btn-primary" onclick={provision} disabled={provisioning}>
      {provisioning ? 'Provisioning' : 'Provision my instance'}
    </button>
  </div>
{:else if error}
  <div class="card card-pad">
    <h3>Control plane unreachable</h3>
    <p class="muted">The control plane API did not answer ({error}). It may still be starting; this page fills in once it is online.</p>
    <button class="btn btn-ghost btn-sm" onclick={load}>Retry</button>
  </div>
{:else}
  <div class="ov fill">
    <div class="ov-main">
      {#if noPrinters}
        <div class="card card-pad getstarted">
          <span class="eyebrow">Get started</span>
          <h2>Your HQ is live. Add your first printer.</h2>
          <div class="steps">
            <a class="step" href="{basePath}/app/printers/add"><b>1</b><span>Add a printer</span><small>Bambu, Creality, Prusa, Snapmaker, Voron and more</small></a>
            <a class="step" href="{basePath}/app/files"><b>2</b><span>Upload a model</span><small>STL or 3MF to your private library</small></a>
            <a class="step" href="{basePath}/app/files"><b>3</b><span>Slice and queue</span><small>OrcaSlicer built in, then send to print</small></a>
          </div>
        </div>
      {:else}
        <div class="tiles">
          {#each tiles as t (t.label)}
            <a class="card tile" href={t.href}>
              <span class="ah-muted">{t.label}</span>
              <span class="big" class:ok={t.ok}>{t.value}</span>
              <span class="ah-muted">{t.sub}</span>
            </a>
          {/each}
        </div>
      {/if}

      <div class="quick">
        <a class="card qa" href="{basePath}/app/printers/add"><span class="qi"><Icon name="plus" size={20} /></span><span><b>Add printer</b><small>Connect a machine</small></span></a>
        <a class="card qa" href="{basePath}/app/files"><span class="qi"><Icon name="layers" size={20} /></span><span><b>Upload and slice</b><small>STL or 3MF to G-code</small></span></a>
        <a class="card qa" href="{basePath}/app/queue"><span class="qi"><Icon name="list-queue" size={20} /></span><span><b>Print queue</b><small>Manage the fleet queue</small></span></a>
      </div>

      {#if fleet.length}
        <section class="card now" aria-label="Printing now">
          <div class="now-head">
            <div>
              <h2 class="ah-card-title">Printing now</h2>
              <div class="ah-muted">{active.filter((p) => p.disp.key === 'printing').length} printing, {active.filter((p) => p.disp.key === 'paused').length} paused</div>
            </div>
            <a class="btn btn-sm" href="{basePath}/app/printers">All printers</a>
          </div>
          <div class="now-list">
            {#each fleet as p (p.id)}
              <a class="now-row" href="{basePath}/app/printers/{p.id}">
                <span class="nr-name"><b>{p.name}</b><span class="ah-muted">{printerLabel(p.connection_type, p.model)}</span></span>
                <span class="nr-job">
                  <span class="nr-file">{p.job.name || (p.disp.key === 'ready' ? 'Ready for the next job' : p.disp.label)}</span>
                  <ProgressBar value={['printing', 'paused'].includes(p.disp.key) ? p.job.progress : 0} label="{p.name} progress" attention={p.disp.key === 'paused'} />
                </span>
                <span class="nr-state"><span class="chip {p.disp.tone}">{['printing'].includes(p.disp.key) ? Math.round(p.job.progress) + '%' : p.disp.label}</span></span>
              </a>
            {/each}
          </div>
        </section>
      {/if}
    </div>

    <aside class="ov-side" aria-label="Widgets">
      {#if needs.length}
        <WidgetCard icon="alert" title="Needs you" subtitle="{needs.length} {needs.length === 1 ? 'printer' : 'printers'}">
          {#each needs as p (p.id)}
            <div class="wrow">
              <div class="wtext"><b>{p.name}, {p.disp.label.toLowerCase()}</b><span class="warn-text">{needsText(p)}</span></div>
              <a class="btn btn-sm" href="{basePath}/app/printers/{p.id}">Open</a>
            </div>
          {/each}
        </WidgetCard>
      {/if}
      {#if connectors.length}
        <WidgetCard icon="plug" title="Connected clients" subtitle="OpenPrintHQ client apps paired to this instance" href="{basePath}/app/printers">
          {#each connectors as c (c.id)}
            <div class="wrow">
              <div class="wtext"><b>{c.name}</b><span class="ah-muted">{c.client?.hostname || ''}{c.client?.version ? `, client ${c.client.version}` : ''}</span></div>
              <span class="chip {c.online ? 'ok' : ''}">{c.online ? 'Online' : 'Offline'}</span>
            </div>
          {/each}
        </WidgetCard>
      {/if}
      <WidgetCard icon="cog" title="Instance" subtitle={instance?.status ? `Status: ${instance.status}` : ''} href="{basePath}/app/settings">
        <div class="kv">
          <div><span class="ah-muted">Instance</span><span class="mono">{instance?.subdomain ?? ''}</span></div>
          <div><span class="ah-muted">Access</span><span class="mono">{host}</span></div>
          <div><span class="ah-muted">Engine</span><span class="mono">{instance?.engineVersion ?? 'openprinthq-engine'}</span></div>
          <div><span class="ah-muted">Created</span><span class="mono">{fmtDate(instance?.createdAt)}</span></div>
        </div>
      </WidgetCard>
    </aside>
  </div>
{/if}

<style>
  .provision { text-align: center; padding: 40px; align-items: center; }
  .provision p { max-width: 52ch; margin: 12px auto 20px; }
  .claim-input { max-width: 320px; margin: 0 auto 14px; text-align: center; display: block; }
  .err { color: var(--ophq-danger); }

  .ov { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 0.6fr); gap: 14px; flex: 1; min-height: 0; }
  .ov-main { display: flex; flex-direction: column; gap: 14px; min-height: 0; min-width: 0; }
  .ov-side { display: flex; flex-direction: column; gap: 14px; min-height: 0; overflow: auto; }

  .tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; flex: none; }
  .tile { display: flex; flex-direction: column; gap: 2px; padding: var(--ah-card-pad) 18px; color: var(--ah-text); text-decoration: none; }
  .tile:hover { border-color: rgba(255, 255, 255, 0.3); color: var(--ah-text); }
  .tile .big.ok { color: var(--ah-success-2); }

  .quick { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; flex: none; }
  .qa { display: flex; align-items: center; gap: 12px; padding: var(--ah-card-pad) 18px; color: var(--ah-text); text-decoration: none; }
  .qa:hover { border-color: rgba(255, 255, 255, 0.3); color: var(--ah-text); }
  .qi { width: 40px; height: 40px; flex: none; border-radius: 12px; display: grid; place-items: center; background: var(--ah-chip-live-bg); border: 1px solid var(--ah-chip-live-line); color: var(--ah-accent); }
  .qa span:last-child { display: flex; flex-direction: column; }
  .qa b { font-size: 15px; }
  .qa small { color: var(--ah-text-3); font-size: 12px; }

  .now { display: flex; flex-direction: column; gap: 10px; padding: var(--ah-card-pad) 18px; flex: 1 1 auto; min-height: 0; }
  .now-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; flex: none; }
  .now-list { overflow: auto; min-height: 0; }
  .now-row { display: grid; grid-template-columns: 160px minmax(0, 1fr) 110px; gap: 12px; align-items: center; padding: 9px 0; border-top: 1px solid var(--ah-row-line); color: var(--ah-text); text-decoration: none; }
  .now-row:hover { color: var(--ah-text); background: var(--ah-segment); }
  .nr-name { display: flex; flex-direction: column; min-width: 0; }
  .nr-job { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
  .nr-file { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .nr-state { text-align: right; }

  .wrow { display: flex; align-items: center; gap: 10px; padding: 7px 0; border-top: 1px solid var(--ah-row-line); }
  .wtext { flex: 1; min-width: 0; display: flex; flex-direction: column; font-size: 13px; }
  .warn-text { font-size: 12px; color: var(--ah-chip-warn-text); }
  .kv { display: flex; flex-direction: column; }
  .kv div { display: flex; gap: 12px; padding: 6px 0; border-top: 1px solid var(--ah-row-line); font-size: 13px; }
  .kv div > span:first-child { width: 72px; flex: none; }

  .getstarted { padding: 22px; flex: none; }
  .getstarted h2 { margin: 6px 0 16px; }
  .steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
  .step { display: flex; flex-direction: column; gap: 4px; padding: 16px; border-radius: var(--ah-radius-choice); background: var(--ah-panel); border: 1px solid var(--ah-panel-line); color: var(--ah-text); }
  .step b { font-family: var(--ah-font-display); color: var(--ah-accent); font-size: 18px; }
  .step span { font-weight: 600; }
  .step small { color: var(--ah-text-3); font-size: 12px; }

  @media (max-width: 1099px) {
    .ov { grid-template-columns: 1fr; }
    .tiles { grid-template-columns: repeat(2, 1fr); }
    .quick, .steps { grid-template-columns: 1fr; }
  }
</style>
