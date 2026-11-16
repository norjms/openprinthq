<script>
  // Fix "the loaded filament doesn't match" (alert kind waiting_filament): the ways out, one at a time. Every path
  // ends by marking the alert fixed; the alert also closes on its own once the job stops waiting.
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';

  let { alert, ctx, onfixed } = $props();
  const job = ctx.job;
  const PATHS = [
    ['use', 'Use what is loaded', 'Pick a loaded filament for each of the model\'s filaments. Another material is resliced first.'],
    ['load', 'Load the right filament', 'See which printer and slot to load it into, then check again.'],
    ['wrong', 'The hub has it wrong', 'The right filament is loaded but the printer reports something else: correct the slot.'],
    ['file', 'Use a different file', 'Print another sliced file of the model, or slice it again.'],
    ['move', 'Run it on another printer', 'Send it to a printer that has the filament.'],
    ['order', 'Order filament', 'Put it on the shopping list; the job keeps waiting.'],
    ['cancel', 'Cancel the job', 'Take it out of the queue.'],
  ];
  let path = $state(null);
  let o = $state(null);
  let err = $state('');
  let busy = $state(false);
  let checked = $state(null);    // result of "check again"
  let slotForm = $state({ printer_id: null, gid: null, type: '', color: '#ffffff' });
  let orderedList = $state([...(alert.subject?.ordered || [])]);

  async function load() {
    try {
      o = await api.get(`/api/farm/jobs/${encodeURIComponent(job)}/filament?all_printers=true`);
      err = '';
      const p = o.printers[0];
      if (p && slotForm.printer_id == null) {
        const r = missing()[0] || o.requirements[0];
        slotForm = { printer_id: p.printer_id, gid: p.loaded[0]?.global_tray_id ?? null, type: r?.type || '', color: r?.color || '#ffffff' };
      }
    } catch (e) { err = e.message || String(e); }
  }
  onMount(load);

  const printerOf = (id) => o?.printers.find((p) => p.printer_id === id);
  const home = $derived(o?.printers.find((p) => p.name === ctx.printer) || o?.printers.find((p) => p.same_model) || o?.printers[0]);
  // The model's filaments no slot on the home printer fits at the job's level.
  function missing() {
    const p = home;
    if (!o || !p) return [];
    return o.requirements.filter((r) => {
      const s = p.slots.find((x) => x.slot_id === r.slot_id);
      return !s || s.suggested == null || ['different_material', 'different_colour', 'none'].includes(s.grade);
    });
  }
  // Where to load a filament on a printer: an empty slot, else the one with the least left.
  function loadInto(p) {
    const trays = p?.loaded || [];
    const empty = trays.find((t) => !t.type);
    if (empty) return `${empty.label} (empty)`;
    const low = [...trays].sort((a, b) => (a.remain ?? 101) - (b.remain ?? 101))[0];
    return low ? `${low.label} (now ${low.type} ${low.color || ''}${low.remain >= 0 ? `, ${low.remain}% left` : ''})` : 'a free slot';
  }

  async function fixed(how) {
    await api.post(`/api/alerts/${alert.id}/fixed`, { how });
    onfixed?.();
  }
  async function checkAgain() {
    busy = true;
    try {
      await load();
      const ok = o.printers.find((p) => p.match?.ok);
      checked = ok ? { ok: true, printer: ok.name } : { ok: false };
      if (ok) { toast(`${ok.name} has the filament now; the job starts on its own`); await fixed(`the loaded filament matches on ${ok.name}`); }
    } finally { busy = false; }
  }
  async function saveSlot() {
    busy = true;
    try {
      const p = printerOf(slotForm.printer_id);
      await attempt(() => api.post(`/api/farm/printers/${encodeURIComponent(p.name)}/slots/${slotForm.gid}/filament`,
        { type: slotForm.type, color: slotForm.color }), `${p.name} told: ${slotForm.type} ${slotForm.color}`);
      await new Promise((r) => setTimeout(r, 1500));
      await checkAgain();
    } finally { busy = false; }
  }
  async function move(p) {
    busy = true;
    try {
      await attempt(() => api.post(`/api/farm/jobs/${encodeURIComponent(job)}/move`, { printer_id: p.printer_id }), `Moved to ${p.name}`);
      await fixed(`moved to ${p.name}`);
    } finally { busy = false; }
  }
  async function want(r) {
    await attempt(() => api.post(`/api/alerts/${alert.id}/want-filament`, { material: r.type, color: r.color, grams: Math.ceil(r.used_grams || 0) || null }),
      `${r.type} ${r.color} is on the shopping list`);
    orderedList = [...orderedList, `${(r.type || '').toUpperCase()} ${(r.color || '').toUpperCase()}`];
  }
  async function cancel() {
    busy = true;
    try {
      await attempt(() => api.post(`/api/mobile/v1/queue/${encodeURIComponent(job)}/cancel`, { reason: 'Cancelled from its filament alert' }), 'Job cancelled');
      await fixed('the job was cancelled');
    } finally { busy = false; }
  }
  const ordered = $derived(new Set(orderedList));
</script>

<div class="grid gap-4" style="grid-template-columns: minmax(260px, 1fr) minmax(0, 2fr)" data-testid="filament-fix">
  <div class="flex flex-col gap-2" role="list" aria-label="Ways to fix it">
    {#each PATHS as [k, title, sub]}
      <button type="button" role="listitem" class="panel p-3 text-left path {path === k ? 'on' : ''}" onclick={() => { path = k; checked = null; }}>
        <span class="font-medium block">{title}</span><span class="text-xs muted block">{sub}</span>
      </button>
    {/each}
  </div>
  <div class="panel p-4 text-sm" data-testid="fix-panel">
    {#if err}<p class="bad">{err}</p>
    {:else if !path}<p class="muted">Pick a way to fix it on the left.</p>
    {:else if !o}<p class="muted">Loading</p>
    {:else if path === 'use'}
      <h3 class="font-semibold mb-2">Use what is loaded</h3>
      <p class="mb-3">Choose which loaded filament prints each of the model's filaments. Same material on the same printer model starts at once; another material is resliced, then starts.</p>
      <a class="btn primary" href={`/farm/jobs/${encodeURIComponent(job)}/filament?alert=${alert.id}`}>Choose the filament</a>
    {:else if path === 'load'}
      <h3 class="font-semibold mb-2">Load the right filament</h3>
      {#if !missing().length}<p>Every filament the model needs is loaded on {home?.name}. Check again to let it start.</p>{/if}
      <ul class="flex flex-col gap-2 mb-3">
        {#each missing() as r (r.slot_id)}
          <li class="flex items-center gap-2"><span class="w-4 h-4 rounded-full border inline-block" style={`background:${r.color}`}></span>
            Load <b>{r.type} {r.color}</b> into <b>{home?.name}</b>: {loadInto(home)}</li>
        {/each}
      </ul>
      <button class="btn primary" disabled={busy} onclick={checkAgain}>I loaded it: check again</button>
      {#if checked && !checked.ok}<p class="mt-2 warn">Still not a match. If it is loaded, the printer may report it differently: try "The hub has it wrong".</p>{/if}
    {:else if path === 'wrong'}
      <h3 class="font-semibold mb-2">The hub has it wrong</h3>
      <p class="mb-3">Tell the printer what is really in the slot (Bambu printers). The job is checked again right after.</p>
      <div class="grid gap-3" style="grid-template-columns: repeat(2, minmax(0, 1fr))">
        <label>Printer <select class="input" bind:value={slotForm.printer_id} onchange={() => (slotForm.gid = printerOf(slotForm.printer_id)?.loaded[0]?.global_tray_id ?? null)}>
          {#each o.printers as p (p.printer_id)}<option value={p.printer_id}>{p.name}</option>{/each}</select></label>
        <label>Slot <select class="input" bind:value={slotForm.gid}>
          {#each printerOf(slotForm.printer_id)?.loaded || [] as t (t.global_tray_id)}<option value={t.global_tray_id}>{t.label}: now {t.type || 'empty'} {t.color || ''}</option>{/each}</select></label>
        <label>Material <input class="input" bind:value={slotForm.type} placeholder="PETG" /></label>
        <label>Colour <span class="flex gap-2"><input type="color" bind:value={slotForm.color} aria-label="Colour" /><input class="input" bind:value={slotForm.color} /></span></label>
      </div>
      <button class="btn primary mt-3" disabled={busy || slotForm.gid == null || slotForm.gid >= 254 || !slotForm.type} onclick={saveSlot}>Save to the printer and check again</button>
      {#if slotForm.gid >= 254}<p class="mt-2 muted">The external spool is set on the printer itself.</p>{/if}
      {#if checked && !checked.ok}<p class="mt-2 warn">Saved, but it still doesn't match. Check the material and colour the model wants.</p>{/if}
    {:else if path === 'file'}
      <h3 class="font-semibold mb-2">Use a different file</h3>
      <p class="mb-3">To print with what is loaded, "Use what is loaded" reslices the plate for you. To pick or slice another file of the model, open the model.</p>
      {#if ctx.model_id}<a class="btn primary" href={`/models/${ctx.model_id}`}>Open the model</a>
      {:else}<p class="muted">This job's file isn't linked to a model in the library, so use "Use what is loaded" or slice it again in OrcaSlicer.</p>{/if}
    {:else if path === 'move'}
      <h3 class="font-semibold mb-2">Run it on another printer</h3>
      <ul class="flex flex-col gap-2">
        {#each o.printers as p (p.printer_id)}
          <li class="flex items-center gap-3">
            <span class="mr-auto"><b>{p.name}</b> <span class="chip {p.match?.ok ? 'ok' : 'warn'}">{p.match?.ok ? 'Has the filament' : 'Filament missing'}</span>{#if !p.same_model} <span class="muted text-xs">other model: reslices</span>{/if}</span>
            {#if p.name === ctx.printer}<span class="muted text-xs">its printer now</span>
            {:else}<button class="btn sm {p.match?.ok ? 'primary' : ''}" disabled={busy || !p.connected} onclick={() => move(p)}>Move here</button>{/if}
          </li>
        {/each}
      </ul>
    {:else if path === 'order'}
      <h3 class="font-semibold mb-2">Order filament</h3>
      <ul class="flex flex-col gap-2">
        {#each (missing().length ? missing() : o.requirements) as r (r.slot_id)}
          <li class="flex items-center gap-3"><span class="w-4 h-4 rounded-full border inline-block" style={`background:${r.color}`}></span>
            <span class="mr-auto">{r.type} {r.color}{#if r.used_grams} · {Math.round(r.used_grams)} g for this job{/if}</span>
            {#if ordered.has(`${(r.type || '').toUpperCase()} ${(r.color || '').toUpperCase()}`)}<span class="chip ok">On the shopping list</span>
            {:else}<button class="btn sm" onclick={() => want(r)}>Add to shopping list</button>{/if}</li>
        {/each}
      </ul>
      <p class="mt-3 muted">The job keeps waiting. <a class="underline" href="/planner">Open the shopping list</a></p>
    {:else if path === 'cancel'}
      <h3 class="font-semibold mb-2">Cancel the job</h3>
      <p class="mb-3">{ctx.job_name || 'The job'} leaves the queue. An order it belongs to will need it queued again.</p>
      <button class="btn danger" disabled={busy} onclick={cancel}>Cancel the job</button>
    {/if}
  </div>
</div>

<style>
  .path { border: 1px solid transparent; cursor: pointer; }
  .path.on { border-color: var(--ah-accent, #8fd3ff); }
</style>
