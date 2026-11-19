<script>
  import Page from '$lib/Page.svelte';
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import { Icon } from 'openprinthq-ui';
  let jobs = $state([]);
  let state = $state('');
  let open = $state(null);
  async function load() { jobs = await api.get('/api/jobs' + (state ? `?state=${state}` : '')); }
  onMount(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); });
  async function run(name, force = false) { await attempt(() => api.post(`/api/actions/${name}${force ? '?force=true' : ''}`), 'Queued'); load(); }
  async function retry(j) { await attempt(() => api.post(`/api/jobs/${j.id}/retry`), 'Retrying'); load(); }
  const RUN = [['scan-library', 'Scan the DeltaPrints library'], ['scan-inbox', 'Scan the inbox'], ['refresh-parts', 'Refresh InvenTree'],
    ['refresh-spools', 'Refresh Spoolman'], ['sync-stock', 'Push stock to WooCommerce'], ['poll-orders', 'Pull WooCommerce orders'],
    ['manyfold-rescan', 'Rescan Manyfold'], ['manyfold-link', 'Link Manyfold models'],
    ['setup', 'Set up connected apps (farm webhook, starter categories)']];
</script>

<Page title="Sync health" description="Every push, import and sync is a job. Failures retry automatically with backoff; you can retry them here.">
{#snippet actions()}
  <div class="ah-seg" role="group" aria-label="Which jobs">
    {#each [['', 'Recent'], ['failed', 'Failed'], ['queued', 'Queued'], ['running', 'Running'], ['done', 'Done']] as [v, l]}<button type="button" aria-pressed={state === v} onclick={() => { state = v; load(); }}>{l}</button>{/each}
  </div>
{/snippet}
{#snippet widgets()}
  <section class="ah-glass ah-card">
    <div class="flex items-center gap-[10px]"><div class="ah-icon-tile"><Icon name="sync" /></div><div style="flex:1"><h3 class="ah-widget-title">Run now</h3><div class="ah-muted">Each one queues a job</div></div></div>
    <div class="flex flex-col gap-2">
      {#each RUN as [k, l]}<button class="btn sm" style="justify-content:flex-start" onclick={() => run(k)}>{l}</button>{/each}
      <button class="btn sm" style="justify-content:flex-start" onclick={() => run('scan-library', true)} title="Re-import every folder even if unchanged">Full re-import</button>
    </div>
  </section>
{/snippet}
<section class="ah-glass ah-card ah-scroll" style="flex:1">
  <table class="grid">
    <thead><tr><th>#</th><th>Job</th><th>Ref</th><th>State</th><th>Tries</th><th>When</th><th>By</th><th></th></tr></thead>
    <tbody>
      {#each jobs as j (j.id)}
        <tr class="cursor-pointer" onclick={() => (open = open === j.id ? null : j.id)}>
          <td class="muted text-xs">{j.id}</td><td class="font-medium">{j.kind}</td><td class="text-xs muted">{j.ref}</td>
          <td><span class="chip {j.state === 'failed' ? 'bad' : j.state === 'done' ? 'ok' : j.state === 'running' ? 'warn' : ''}">{j.state}</span></td>
          <td>{j.attempts}</td><td class="text-xs muted">{ago(j.finished_at || j.created_at)}</td><td class="text-xs muted">{j.created_by || ''}</td>
          <td>{#if j.state === 'failed'}<button class="btn sm" onclick={(e) => { e.stopPropagation(); retry(j); }}>Retry</button>{/if}</td>
        </tr>
        {#if open === j.id}
          <tr><td colspan="8"><pre class="text-xs whitespace-pre-wrap max-h-80 overflow-auto">{j.error || JSON.stringify(j.result, null, 2)}</pre></td></tr>
        {/if}
      {/each}
    </tbody>
  </table>
</section>
</Page>
