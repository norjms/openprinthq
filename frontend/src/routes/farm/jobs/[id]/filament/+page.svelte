<script>
  // Map a waiting job's filament: the model's filaments on the left, a picker of the printer's loaded filament for
  // each. The alert for an unmatched job opens this page. Same material on the same printer model starts at once;
  // another material or another printer model is resliced first (services/filament_mapping.py).
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';
  import Page from '$lib/Page.svelte';

  const id = $derived(page.params.id);
  let o = $state(null);
  let err = $state('');
  let pid = $state(null);
  let choice = $state({});
  let remember = $state(true);
  let others = $state(false);
  let busy = $state(false);
  let done = $state(null);

  const LEVEL = { exact: 'Exact', close: 'Close', family: 'Colour family', any: 'Any colour' };
  const GRADE = { same: ['Same', 'ok'], close: ['Close colour', 'ok'], family: ['Same colour family', 'warn'],
    material: ['Same material', 'warn'], different_colour: ['Different colour', 'warn'],
    different_material: ['Different material: reslice', 'bad'], none: ['Nothing loaded', 'bad'] };

  async function load() {
    err = '';
    try {
      o = await api.get(`/api/farm/jobs/${id}/filament?all_printers=${others}`);
      if (!o.printers.some((p) => p.printer_id === pid)) pick(o.printers[0]?.printer_id ?? null);
    } catch (e) { err = e.message || String(e); }
  }
  function pick(next) {
    pid = next;
    const p = o?.printers.find((x) => x.printer_id === next);
    choice = Object.fromEntries((p?.slots || []).map((s) => [s.slot_id, s.suggested ?? -1]));
  }
  onMount(load);

  const printer = $derived(o?.printers.find((p) => p.printer_id === pid));
  const trays = $derived(printer?.loaded || []);
  const group = (t) => (t || '').toUpperCase().replace(/\s+(BASIC|HF|MATTE|SILK|\+)$/g, '').replace('+', '').trim();
  function rowFor(r) {
    const t = trays.find((x) => x.global_tray_id === choice[r.slot_id]);
    const s = printer?.slots.find((x) => x.slot_id === r.slot_id);
    const suggested = s && s.suggested === choice[r.slot_id];
    let grade = suggested ? s.grade : t ? (group(t.type) === group(r.type) ? (t.color?.toUpperCase() === r.color?.toUpperCase() ? 'same' : 'different_colour') : 'different_material') : 'none';
    return { t, grade, de: suggested ? s.delta_e : null };
  }
  const rows = $derived((o?.requirements || []).map((r) => ({ r, ...rowFor(r) })));
  const dupes = $derived(new Set(Object.values(choice).filter((v, i, a) => v >= 0 && a.indexOf(v) !== i)));
  const reslice = $derived(!!printer && (!printer.same_model || rows.some((x) => x.grade === 'different_material')));
  const ready = $derived(!!printer && rows.length > 0 && rows.every((x) => x.t) && dupes.size === 0);

  async function go() {
    busy = true;
    try {
      const slots = rows.map((x) => ({ slot_id: x.r.slot_id, global_tray_id: choice[x.r.slot_id] }));
      const r = await attempt(() => api.post(`/api/farm/jobs/${id}/filament`, { printer_id: pid, slots, remember }));
      if (r) {
        done = r;
        toast(r.reslice ? `Reslicing for ${r.printer}; it starts when the slice is ready` : `Starting on ${r.printer}`);
      }
    } finally { busy = false; }
  }
  const swatch = (c) => `background:${c || 'transparent'}`;
</script>

<Page title="Map filament" crumb={o?.job_name || `Job ${id}`}
  description="Choose which loaded filament prints each of the model's filaments.">
  {#if err}<div class="panel p-4 text-sm text-red-600">{err}</div>
  {:else if !o}<div class="muted text-sm">Loading...</div>
  {:else if done}
    <div class="panel p-6 flex flex-col gap-3">
      <div class="text-lg font-semibold">{done.reslice ? `Reslicing ${o.job_name} for ${done.printer}` : `${o.job_name} is starting on ${done.printer}`}</div>
      {#if done.reslice}<div class="muted text-sm">Why: {done.why.join('; ')}. It starts by itself when the slice is ready; a slice with warnings waits in Slicing for review, and you'll get an alert.</div>{/if}
      <div><a class="btn" href="/farm">Back to the farm</a></div>
    </div>
  {:else}
    <div class="flex flex-col gap-4">
      {#if o.unmatched?.printers?.length}
        <div class="panel p-4 text-sm"><b>Why it's waiting:</b> {o.unmatched.printers.map((p) => `${p.printer_name}: ${p.summary}`).join('; ')}
          <span class="muted"> · match level {LEVEL[o.level] || o.level}</span></div>
      {/if}
      {#if o.reslice?.state === 'review' || o.reslice?.state === 'failed'}
        <div class="panel p-4 text-sm"><span class="chip bad">{o.reslice.state}</span> {o.reslice.msg}</div>
      {/if}
      <div class="flex flex-wrap items-center gap-3">
        <span class="text-sm muted">Printer</span>
        {#each o.printers as p (p.printer_id)}
          <button class="chip {p.printer_id === pid ? 'live' : ''}" onclick={() => pick(p.printer_id)}>
            {p.name}{#if !p.same_model}&nbsp;({p.model}){/if}{#if p.match?.ok}&nbsp;· matches{/if}
          </button>
        {/each}
        <label class="flex items-center gap-2 text-sm ml-auto"><input type="checkbox" bind:checked={others} onchange={load} /> Show every printer</label>
      </div>
      {#if !printer}<div class="panel p-6 text-center muted">No printer you can use can take this job.</div>
      {:else if !printer.reports_filament}<div class="panel p-6 text-center muted">{printer.name} doesn't report what's loaded.</div>
      {:else}
        <div class="panel overflow-x-auto">
          <table class="w-full text-sm">
            <thead><tr class="text-left muted text-xs"><th class="px-4 py-2 font-medium">Model filament</th><th class="px-4 py-2 font-medium">Prints with, on {printer.name}</th><th class="px-4 py-2 font-medium">Match</th></tr></thead>
            <tbody>
              {#each rows as x (x.r.slot_id)}
                <tr class="border-t">
                  <td class="px-4 py-2"><span class="inline-flex items-center gap-2"><span class="w-4 h-4 rounded-full border inline-block" style={swatch(x.r.color)}></span>
                    {x.r.slot_id}. {x.r.type} <span class="font-mono muted">{x.r.color}</span>{#if x.r.used_grams}<span class="muted">· {Math.round(x.r.used_grams)} g</span>{/if}</span></td>
                  <td class="px-4 py-2"><span class="inline-flex items-center gap-2">
                    <span class="w-4 h-4 rounded-full border inline-block shrink-0" style={swatch(trays.find((t) => t.global_tray_id === choice[x.r.slot_id])?.color)}></span>
                    <select class="input" style="min-width: 18rem; max-width: 28rem" bind:value={choice[x.r.slot_id]}>
                      {#each trays as t (t.global_tray_id)}
                        <option value={t.global_tray_id}>{t.label}: {t.type} {t.color}{t.remain >= 0 ? ` · ${t.remain}% left` : ''}</option>
                      {/each}
                    </select></span>
                  </td>
                  <td class="px-4 py-2">
                    {#if dupes.has(choice[x.r.slot_id])}<span class="chip bad">Used twice</span>
                    {:else}<span class="chip {GRADE[x.grade]?.[1] || ''}">{GRADE[x.grade]?.[0] || x.grade}</span>{#if x.de != null}<span class="muted text-xs ml-2">ΔE {x.de}</span>{/if}{/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if reslice}
          <div class="panel p-4 text-sm">{o.can_reslice ? `This needs the file sliced again for ${printer.name} with the loaded filament. The hub does that, then starts it.` : `This needs the file sliced again, but ${o.reslice_unavailable}. Pick the same material, or load it.`}</div>
        {/if}
        <div class="flex flex-wrap items-center gap-3">
          <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={remember} /> Remember this mapping for the same filaments</label>
          <a class="btn ml-auto" href="/farm">Later</a>
          {#if can('farm')}
            <button class="btn primary" disabled={!ready || busy || (reslice && !o.can_reslice)} onclick={go}>{reslice ? 'Reslice and start' : 'Start'}</button>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</Page>
