<script>
  // A guided fix the hub describes (services/alert_fixes.py): what the alert is about now, then the ways to fix it.
  // Each way may ask for a few values; its button runs it on the hub. The app shows the same ways.
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';

  let { alert, ctx, onchanged } = $props();
  let path = $state(null);
  let values = $state({});
  let busy = $state(false);
  let tick = $state(Date.now());
  const ways = $derived(ctx.paths || []);
  const cur = $derived(ways.find((p) => p.id === path) || null);

  onMount(() => {
    const t = setInterval(() => (tick = Date.now()), 10000);
    return () => clearInterval(t);
  });
  function pick(p) {
    path = p.id;
    values = Object.fromEntries((p.fields || []).map((f) => [f.name, f.value ?? (f.type === 'switch' ? false : '')]));
  }
  async function go() {
    const p = cur;
    if (!p || busy) return;
    if (p.confirm && !confirm(p.confirm)) return;
    busy = true;
    try {
      const r = await attempt(() => api.post(`/api/alerts/${alert.id}/do/${p.id}`, { values }));
      if (r) { toast(r.message); onchanged?.(r.alert); }
    } catch { /* shown */ } finally { busy = false; }
  }
  const missing = $derived(cur ? (cur.fields || []).some((f) => f.required && !String(values[f.name] || '').trim()) : false);
</script>

<div class="grid gap-4" style="grid-template-columns: minmax(260px, 1fr) minmax(0, 2fr)" data-testid="guided-fix">
  <div class="flex flex-col gap-2" role="list" aria-label="Ways to fix it">
    {#if ctx.note}<div class="panel p-3 text-sm">{ctx.note}</div>{/if}
    {#each ways as p (p.id)}
      <button type="button" role="listitem" class="panel p-3 text-left path {path === p.id ? 'on' : ''}" onclick={() => pick(p)}>
        <span class="font-medium block">{p.title}</span><span class="text-xs muted block">{p.off || p.detail}</span>
      </button>
    {/each}
  </div>
  <div class="panel p-4 text-sm" data-testid="fix-panel">
    {#if ctx.summary?.length}
      <dl class="kv mb-3">{#each ctx.summary as [k, v]}<dt class="muted">{k}</dt><dd>{v}</dd>{/each}</dl>
    {/if}
    {#if ctx.photos?.length || ctx.camera}
      <div class="grid gap-3 mb-3" style="grid-template-columns: repeat(2, minmax(0, 320px))">
        {#each ctx.photos || [] as ph}<figure><figcaption class="text-xs muted mb-1">{ph.label}</figcaption>
          <img class="w-full rounded" style="aspect-ratio: 4 / 3; object-fit: cover" src={ph.url} alt={ph.label} /></figure>{/each}
        {#if ctx.camera}<figure><figcaption class="text-xs muted mb-1">{ctx.camera} now</figcaption>
          <img class="w-full rounded" style="aspect-ratio: 4 / 3; object-fit: cover" src={`/api/mobile/v1/printers/${encodeURIComponent(ctx.camera)}/snapshot?t=${tick}`} alt={`Camera on ${ctx.camera}`} /></figure>{/if}
      </div>
    {/if}
    {#if !cur}<p class="muted">Pick a way to fix it on the left.</p>
    {:else}
      <h3 class="font-semibold mb-1">{cur.title}</h3>
      <p class="mb-3">{cur.detail}</p>
      {#if cur.off}<p class="bad mb-2">{cur.off}</p>{/if}
      {#if (cur.fields || []).length}
        <div class="grid gap-3 mb-3" style="grid-template-columns: repeat(2, minmax(0, 1fr))">
          {#each cur.fields as f (f.name)}
            {#if f.type === 'choice'}
              <label>{f.label} <select class="input" bind:value={values[f.name]}>{#each f.options as [v, l]}<option value={v}>{l}</option>{/each}</select></label>
            {:else if f.type === 'text'}
              <label>{f.label} <input class="input" bind:value={values[f.name]} placeholder={f.hint || ''} /></label>
            {:else if f.type === 'switch'}
              <label class="flex items-center gap-2 self-end"><input type="checkbox" bind:checked={values[f.name]} /> {f.label}</label>
            {/if}
          {/each}
        </div>
      {/if}
      {#if cur.open}<a class="btn primary" href={cur.open.web}>{cur.button}</a>
      {:else}<button class="btn {cur.icon === 'stop' ? 'danger' : 'primary'}" disabled={busy || !!cur.off || missing} onclick={go}>{cur.button}</button>{/if}
      {#if !cur.open && !cur.closes}<p class="text-xs muted mt-2">The alert stays open for the next step.</p>{/if}
    {/if}
  </div>
</div>

<style>
  .path { border: 1px solid transparent; cursor: pointer; }
  .path.on { border-color: var(--ah-accent, #8fd3ff); }
  .kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; }
  .kv dd { margin: 0; }
</style>
