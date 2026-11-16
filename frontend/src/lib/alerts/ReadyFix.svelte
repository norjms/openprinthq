<script>
  // Fix "jobs are waiting on a printer that isn't ready" (alert kind waiting_ready): clear the plate and pass it,
  // say what is wrong with the last print, or send the waiting jobs elsewhere.
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';

  let { alert, ctx, onfixed } = $props();
  const name = ctx.printer;
  const enc = encodeURIComponent(name);
  const check = ctx.check;
  const PATHS = [
    ['clear', 'The plate is clear', check ? 'Pass the plate check: the printer is set ready and the next job starts.' : 'Set the printer ready: the next job starts.'],
    ['dirty', 'Something is wrong with the last print', 'Fail its plate check with a reason, and print it again if you like.'],
    ['move', 'Use another printer', 'Send the waiting jobs to a printer that is ready.'],
  ];
  let path = $state(null);
  let printers = $state([]);
  let reasons = $state({});
  let fail = $state({ reason: 'spaghetti', note: '', reprint: true });
  let busy = $state(false);
  let tick = $state(Date.now());

  onMount(async () => {
    try { printers = (await api.get('/api/mobile/v1/printers')).printers || []; } catch {}
    try { reasons = (await api.get('/api/mobile/v1/plate-checks')).reasons || {}; } catch {}
    const t = setInterval(() => (tick = Date.now()), 10000);
    return () => clearInterval(t);
  });
  async function fixed(how) { await api.post(`/api/alerts/${alert.id}/fixed`, { how }); onfixed?.(); }

  async function clear() {
    busy = true;
    try {
      if (check) await attempt(() => api.post(`/api/mobile/v1/plate-checks/${check.id}/pass`, { set_ready: true }), `${name} is ready`);
      else await attempt(() => api.post(`/api/mobile/v1/printers/${enc}/command`, { command: 'set_ready' }), `${name} is ready`);
      await fixed(check ? 'plate check passed' : 'set ready');
    } finally { busy = false; }
  }
  async function failCheck() {
    busy = true;
    try {
      await attempt(() => api.post(`/api/mobile/v1/plate-checks/${check.id}/fail`, { ...fail, set_ready: true }),
        fail.reprint ? 'Failed; it is queued again' : 'Plate check failed');
      await fixed(`last print failed: ${reasons[fail.reason] || fail.reason}`);
    } finally { busy = false; }
  }
  async function moveAll(p) {
    busy = true;
    try {
      for (const j of ctx.waiting) {
        await attempt(() => api.post(`/api/farm/jobs/${encodeURIComponent(j.external_id)}/move`, { printer_id: p.id }));
      }
      await fixed(`waiting jobs moved to ${p.name}`);
    } finally { busy = false; }
  }
  const others = $derived(printers.filter((p) => p.name !== name && p.online));
</script>

<div class="grid gap-4" style="grid-template-columns: minmax(260px, 1fr) minmax(0, 2fr)" data-testid="ready-fix">
  <div class="flex flex-col gap-2" role="list" aria-label="Ways to fix it">
    {#each PATHS as [k, title, sub]}
      <button type="button" role="listitem" class="panel p-3 text-left path {path === k ? 'on' : ''}" onclick={() => (path = k)}>
        <span class="font-medium block">{title}</span><span class="text-xs muted block">{sub}</span>
      </button>
    {/each}
  </div>
  <div class="panel p-4 text-sm" data-testid="fix-panel">
    {#if !path}<p class="muted">Pick a way to fix it on the left.</p>
    {:else if path === 'clear'}
      <h3 class="font-semibold mb-2">The plate is clear</h3>
      <div class="grid gap-3 mb-3" style="grid-template-columns: repeat(2, minmax(0, 1fr))">
        {#if check?.photo}<figure><figcaption class="text-xs muted mb-1">After the last print{check.job_name ? `: ${check.job_name}` : ''}</figcaption>
          <img class="w-full rounded" style="aspect-ratio: 4 / 3; object-fit: cover" src={check.photo} alt={`Plate after the last print on ${name}`} /></figure>{/if}
        <figure><figcaption class="text-xs muted mb-1">The plate now</figcaption>
          <img class="w-full rounded" style="aspect-ratio: 4 / 3; object-fit: cover" src={`/api/mobile/v1/printers/${enc}/snapshot?t=${tick}`} alt={`Camera on ${name}`} /></figure>
      </div>
      <button class="btn primary" disabled={busy} onclick={clear}>{check ? 'Plate is clear: pass' : 'Plate is clear: set ready'}</button>
      <p class="text-xs muted mt-2">Only when the plate is empty: the next job starts on it.</p>
    {:else if path === 'dirty'}
      <h3 class="font-semibold mb-2">Something is wrong with the last print</h3>
      {#if !check}<p>There is no plate check open for {name}. Clear the plate, then use "The plate is clear".</p>
      {:else}
        <div class="grid gap-3" style="grid-template-columns: repeat(2, minmax(0, 1fr))">
          <label>What went wrong <select class="input" bind:value={fail.reason}>{#each Object.entries(reasons) as [k, l]}<option value={k}>{l}</option>{/each}</select></label>
          <label>Note <input class="input" bind:value={fail.note} placeholder="Optional" /></label>
        </div>
        <label class="flex items-center gap-2 mt-3"><input type="checkbox" bind:checked={fail.reprint} /> Print it again</label>
        <button class="btn primary mt-3" disabled={busy} onclick={failCheck}>Fail the plate check{fail.reprint ? ' and reprint' : ''}</button>
        <p class="text-xs muted mt-2">Clear the plate first: the printer is set ready so the next job can start.</p>
      {/if}
    {:else if path === 'move'}
      <h3 class="font-semibold mb-2">Use another printer</h3>
      <p class="mb-2">{ctx.waiting.length} job{ctx.waiting.length === 1 ? '' : 's'} waiting: {ctx.waiting.map((j) => j.name).join(', ') || 'none'}.</p>
      <ul class="flex flex-col gap-2">
        {#each others as p (p.id)}
          <li class="flex items-center gap-3"><span class="mr-auto"><b>{p.name}</b> <span class="chip {p.ready ? 'ok' : 'warn'}">{p.ready ? 'Ready' : p.busy ? 'Printing' : 'Not ready'}</span></span>
            <button class="btn sm {p.ready ? 'primary' : ''}" disabled={busy || !ctx.waiting.length} onclick={() => moveAll(p)}>Move the jobs here</button></li>
        {:else}<li class="muted">No other printer is online.</li>{/each}
      </ul>
      <p class="text-xs muted mt-2">Each job's filament is checked again on the new printer.</p>
    {/if}
  </div>
</div>

<style>
  .path { border: 1px solid transparent; cursor: pointer; }
  .path.on { border-color: var(--ah-accent, #8fd3ff); }
</style>
