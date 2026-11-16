<script>
  // Get a printer ready for the jobs waiting on it. The "jobs waiting" alert (services/farm.py, ready_alerts) links
  // here: a look at the plate, then pass the printer's plate check or set it Ready.
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';
  import Page from '$lib/Page.svelte';

  const name = $derived(page.params.name);
  const enc = $derived(encodeURIComponent(page.params.name));
  let p = $state(null);
  let check = $state(null);
  let waiting = $state([]);
  let err = $state('');
  let busy = $state(false);
  let tick = $state(Date.now());

  async function load() {
    try {
      const [pr, ch, q] = await Promise.all([
        api.get(`/api/mobile/v1/printers/${enc}`),
        api.get('/api/mobile/v1/plate-checks'),
        api.get('/api/mobile/v1/queue'),
      ]);
      p = pr;
      check = (ch.waiting || []).find((c) => c.printer === name) || null;
      waiting = (q.jobs || []).filter((j) => ['queued', 'assigned'].includes(j.status) && j.printer === name);
      err = '';
    } catch (e) { err = e.message || String(e); }
  }
  let timer;
  onMount(() => { load(); timer = setInterval(() => { tick = Date.now(); load(); }, 10000); });
  onDestroy(() => clearInterval(timer));

  const ready = $derived(p?.ready === true);
  const printing = $derived(!!p?.busy);
  const online = $derived(p?.online !== false);
  const jobs = $derived(waiting.length ? `${waiting[0].name || 'A job'}${waiting.length > 1 ? ` and ${waiting.length - 1} more` : ''}` : '');

  async function go() {
    busy = true;
    try {
      if (check) await attempt(() => api.post(`/api/mobile/v1/plate-checks/${check.id}/pass`, { set_ready: true }), `${name} is ready`);
      else await attempt(() => api.post(`/api/mobile/v1/printers/${enc}/command`, { command: 'set_ready' }), `${name} is ready`);
      await load();
    } finally { busy = false; }
  }
</script>

<Page title={`Get ${name} ready`} crumb={name} description="Clear the plate, then set the printer ready so the waiting jobs can start.">
  {#if err}<div class="panel p-4 text-sm bad">{err}</div>{/if}
  {#if p}
    <div class="panel p-4 text-sm mb-4" data-testid="ready-state">
      {#if ready}<b>{name} is ready.</b> {jobs ? `${jobs} starts on its own.` : ''}
      {:else if printing}<b>{name} is printing.</b> It needs a clear plate once this print ends.
      {:else if !online}<b>{name} is offline.</b> Turn it on, then set it ready.
      {:else}<b>{jobs ? `${jobs} ${waiting.length > 1 ? 'are' : 'is'} waiting for ${name}.` : `${name} isn't ready.`}</b> Clear the plate, then set it ready.{/if}
    </div>
    <div class="grid gap-4" style="grid-template-columns: repeat(auto-fit, minmax(320px, 1fr))">
      {#if check?.photo}
        <figure class="panel p-3">
          <figcaption class="text-xs muted mb-2">After the last print{check.job_name ? `: ${check.job_name}` : ''}</figcaption>
          <img class="w-full rounded" style="aspect-ratio: 4 / 3; object-fit: cover" src={check.photo} alt={`Plate after the last print on ${name}`} />
        </figure>
      {/if}
      <figure class="panel p-3">
        <figcaption class="text-xs muted mb-2">The plate now</figcaption>
        <img class="w-full rounded" style="aspect-ratio: 4 / 3; object-fit: cover" src={`/api/mobile/v1/printers/${enc}/snapshot?t=${tick}`} alt={`Camera on ${name}`} />
      </figure>
      <div class="panel p-4 text-sm">
        <h2 class="font-semibold mb-2">Waiting for this printer</h2>
        {#if waiting.length}
          <ul class="flex flex-col gap-1">{#each waiting as j (j.id)}<li>{j.name || `Job ${j.id}`}{#if j.material} <span class="muted">· {j.material}</span>{/if}</li>{/each}</ul>
        {:else}<p class="muted">No jobs are queued for {name} right now.</p>{/if}
        {#if !ready && !printing && online && can('farm')}
          <button class="btn primary mt-4" disabled={busy} onclick={go}>{check ? 'Plate is clear: pass' : 'Plate is clear: set ready'}</button>
          <p class="text-xs muted mt-2">Set it ready only when the plate is empty: the next job starts on it.</p>
        {/if}
      </div>
    </div>
  {/if}
</Page>
