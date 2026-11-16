<script>
  // One alert: what happened, then Fix it (a guided fix for the kinds that have one, else the place to deal with
  // it) or Dismiss. Notifications and emails link here.
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api, ago } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import Page from '$lib/Page.svelte';
  import FilamentFix from '$lib/alerts/FilamentFix.svelte';
  import ReadyFix from '$lib/alerts/ReadyFix.svelte';
  import GuidedFix from '$lib/alerts/GuidedFix.svelte';

  const id = $derived(page.params.id);
  let a = $state(null);
  let err = $state('');
  let fixing = $state(false);

  async function load() {
    try { a = await api.get(`/api/alerts/${id}`); err = ''; } catch (e) { err = e.message || String(e); }
  }
  onMount(load);
  async function dismiss() { a = { ...a, ...(await attempt(() => api.post(`/api/alerts/${id}/dismiss`), 'Dismissed')) }; }
  async function done() { fixing = false; await load(); }
  const STATE = { open: 'Open', fixed: 'Fixed', dismissed: 'Dismissed', resolved: 'Cleared up' };
</script>

<Page title={a?.title || 'Alert'} crumb={a ? 'Alert' : null} description={a?.message || ''}>
  {#if err}<div class="panel p-4 text-sm bad">{err}</div>{/if}
  {#if a}
    <div class="panel p-4 mb-4 flex flex-wrap items-center gap-3 text-sm" data-testid="alert-head">
      <span class="chip {a.state === 'open' ? (a.urgent ? 'bad' : 'warn') : 'ok'}">{a.state === 'open' && a.urgent ? 'Urgent' : STATE[a.state] || a.state}</span>
      <span class="muted">Raised {ago(a.created_at)}{a.count > 1 ? `, ${a.count} times, last ${ago(a.updated_at)}` : ''}</span>
      {#if a.state !== 'open'}<span class="muted">· {STATE[a.state]} {ago(a.closed_at)}{a.closed_by && a.closed_by !== 'hub' ? ` by ${a.closed_by}` : ''}{a.fix ? `: ${a.fix}` : ''}</span>{/if}
      <span class="mr-auto"></span>
      {#if a.state === 'open'}
        {#if a.fixable}<button class="btn primary" onclick={() => (fixing = true)} disabled={fixing}>Fix it</button>
        {:else if a.link}<a class="btn primary" href={a.link}>Fix it</a>{/if}
        <button class="btn" onclick={dismiss}>Dismiss</button>
      {/if}
      <a class="btn" href="/alerts">All alerts</a>
    </div>
    {#if a.state === 'open' && a.fixable}
      {#if !fixing}
        <div class="panel p-4 text-sm muted">Fix it walks you through the ways to sort this out.</div>
      {:else if a.context?.kind === 'filament'}
        {#if a.context.job_status === 'gone'}<div class="panel p-4 text-sm">The job is no longer in the queue.</div>
        {:else}<FilamentFix alert={a} ctx={a.context} onfixed={done} />{/if}
      {:else if a.context?.kind === 'ready'}
        <ReadyFix alert={a} ctx={a.context} onfixed={done} />
      {:else if a.context?.kind === 'guided'}
        <GuidedFix alert={a} ctx={a.context} onchanged={(x) => { a = x; if (x.state !== 'open') fixing = false; }} />
      {/if}
    {/if}
  {/if}
</Page>
