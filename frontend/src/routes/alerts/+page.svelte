<script>
  // The alerts inbox: everything the hub raised, open first. Each alert opens on its own page with Fix it and
  // Dismiss. "What I get" is this person's own choice of what reaches their phone and email.
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { api, ago } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import Page from '$lib/Page.svelte';

  const TABS = [['open', 'Open'], ['closed', 'Closed'], ['choices', 'What I get']];
  let tab = $state(page.url.searchParams.get('tab') || 'open');
  let list = $state(null);
  let choices = $state(null);
  let err = $state('');

  async function load() {
    err = '';
    try {
      if (tab === 'choices') choices = await api.get('/api/alerts/choices');
      else list = await api.get(`/api/alerts?state=${tab}`);
    } catch (e) { err = e.message || String(e); }
  }
  function pick(t) { tab = t; goto(t === 'open' ? '/alerts' : `/alerts?tab=${t}`, { replaceState: true, noScroll: true, keepFocus: true }); load(); }
  onMount(load);

  async function toggle(k, on) { choices = await attempt(() => api.put('/api/alerts/choices', { on: { [k]: on } })); }
  async function group(g, on) { choices = await attempt(() => api.put('/api/alerts/choices', { on: Object.fromEntries(g.kinds.map((k) => [k.kind, on])) })); }
  const STATE = { open: ['Open', 'warn'], fixed: ['Fixed', 'ok'], dismissed: ['Dismissed', ''], resolved: ['Cleared up', 'ok'] };
</script>

<Page title="Alerts" description="What the hub has raised. Open one to fix it or dismiss it.">
  <div class="ah-seg mb-4" role="tablist" aria-label="Alerts">
    {#each TABS as [k, l]}<button type="button" role="tab" aria-selected={tab === k} aria-pressed={tab === k} onclick={() => pick(k)}>{l}{#if k === 'open' && list?.open} ({list.open}){/if}</button>{/each}
  </div>
  {#if err}<div class="panel p-4 text-sm bad">{err}</div>{/if}

  {#if tab === 'choices'}
    {#if choices}
      <p class="text-sm muted mb-3">Choose what reaches your phone and your email. The list here keeps everything either way, and the hub owner sets which channels each alert can use under Farm, Settings, Alerts.</p>
      <div class="grid gap-4" style="grid-template-columns: repeat(auto-fit, minmax(300px, 1fr))" data-testid="alert-choices">
        {#each choices.groups as g (g.id)}
          <div class="panel p-4">
            <div class="flex items-center gap-2 mb-2">
              <h2 class="font-semibold mr-auto">{g.label}</h2>
              <button class="btn sm" onclick={() => group(g, true)}>All on</button>
              <button class="btn sm" onclick={() => group(g, false)}>All off</button>
            </div>
            <ul class="flex flex-col gap-2 text-sm">
              {#each g.kinds as k (k.kind)}
                <li><label class="flex items-center gap-2"><input type="checkbox" checked={k.on} onchange={(e) => toggle(k.kind, e.target.checked)} />
                  <span class="mr-auto">{k.label}</span>{#if k.urgent}<span class="chip bad">urgent</span>{/if}</label></li>
              {/each}
            </ul>
          </div>
        {/each}
      </div>
    {/if}
  {:else if list}
    {#if !list.alerts.length}
      <div class="panel p-6 text-sm muted">{tab === 'open' ? 'Nothing needs you right now.' : 'No closed alerts yet.'}</div>
    {:else}
      <div class="panel" data-testid="alert-list">
        {#each list.alerts as a (a.id)}
          <a class="flex items-center gap-3 px-4 py-3 border-b alert-row" href={`/alerts/${a.id}`}>
            <span class="chip {a.urgent && a.state === 'open' ? 'bad' : STATE[a.state]?.[1] || ''}">{a.urgent && a.state === 'open' ? 'Urgent' : STATE[a.state]?.[0] || a.state}</span>
            <span class="mr-auto min-w-0">
              <span class="font-medium block truncate">{a.title}</span>
              {#if a.message}<span class="text-xs muted block truncate">{a.message}</span>{/if}
            </span>
            {#if a.fixable && a.state === 'open'}<span class="chip ok">Fix it</span>{/if}
            {#if a.count > 1}<span class="text-xs muted">{a.count} times</span>{/if}
            <span class="text-xs muted whitespace-nowrap">{ago(a.updated_at)}</span>
          </a>
        {/each}
      </div>
    {/if}
  {/if}
</Page>

<style>
  .alert-row:hover { background: var(--ah-row-hover, rgba(255,255,255,0.04)); }
  .alert-row:last-child { border-bottom: none; }
</style>
