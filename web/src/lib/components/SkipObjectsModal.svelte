<script>
  // Skip individual objects on the current plate. Irreversible mid-print — the
  // printer will not come back to a skipped object — so it is confirm-gated and
  // says so in plain words.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import ModalShell from '$lib/components/ModalShell.svelte';

  let { printerId, onclose = () => {}, onskipped = () => {} } = $props();

  let loading = $state(true);
  let err = $state(null);
  let objects = $state([]);       // [{ id, name, skipped }]
  let picked = $state(new Set());
  let confirming = $state(false);
  let busy = $state(false);

  async function load(reload = false) {
    loading = true; err = null;
    try {
      const r = await api.printObjects(printerId, reload);
      const list = Array.isArray(r) ? r : (r?.objects || r?.printable_objects || []);
      objects = list.map((o, i) => ({
        id: Number(o.id ?? o.object_id ?? i),
        name: o.name || o.label || `Object ${o.id ?? i}`,
        skipped: !!(o.skipped ?? o.is_skipped)
      }));
      picked = new Set();
    } catch (e) {
      err = e?.message || 'Could not read the objects on this plate.';
      objects = [];
    } finally { loading = false; }
  }
  onMount(() => load(false));

  function toggle(id) {
    const next = new Set(picked);
    next.has(id) ? next.delete(id) : next.add(id);
    picked = next;
  }

  const selectable = $derived(objects.filter((o) => !o.skipped));
  const chosen = $derived(objects.filter((o) => picked.has(o.id)));

  async function skip() {
    busy = true; err = null;
    try {
      // The engine wants a bare array of ids here.
      await api.skipObjects(printerId, [...picked]);
      onskipped();
      await load(false);
      confirming = false;
    } catch (e) {
      err = e?.message || 'The printer refused the skip.';
      confirming = false;
    } finally { busy = false; }
  }
</script>

<ModalShell title="Skip objects" subtitle="Abandon individual parts on this plate" width="520px"
            busy={busy} {onclose}>
  {#if loading}
    <p class="muted">Reading the plate…</p>
  {:else if !objects.length}
    <p class="muted">
      No per-object data for this job. That usually means the plate was sliced
      without object labels, or the printer hasn't reported them yet.
    </p>
    <button class="btn btn-ghost btn-sm reload" onclick={() => load(true)}>
      Re-read from the printer
    </button>
  {:else}
    <p class="muted tiny lead">
      A skipped object is abandoned for the rest of the print — the printer will
      not return to it. Everything else carries on.
    </p>
    <ul class="objs">
      {#each objects as o (o.id)}
        <li class="obj" class:gone={o.skipped} class:on={picked.has(o.id)}>
          <label class="row">
            <input type="checkbox" checked={picked.has(o.id)} disabled={o.skipped || busy}
                   onchange={() => toggle(o.id)} aria-label={`Skip ${o.name}`} />
            <span class="nm">{o.name}</span>
            <span class="mono id">#{o.id}</span>
            {#if o.skipped}<span class="chip">skipped</span>{/if}
          </label>
        </li>
      {/each}
    </ul>
    {#if !selectable.length}
      <p class="muted tiny">Every object on this plate has already been skipped.</p>
    {/if}
    {#if confirming}
      <p class="cq">
        Skip {chosen.length === 1 ? chosen[0].name : `${chosen.length} objects`}? This can't be undone.
      </p>
    {/if}
  {/if}

  {#if err}<p class="err">{err}</p>{/if}

  {#snippet footer()}
    {#if confirming}
      <button class="btn btn-ghost btn-sm" onclick={() => (confirming = false)} disabled={busy}>Cancel</button>
      <button class="btn btn-danger btn-sm" onclick={skip} disabled={busy}>
        {busy ? 'Skipping…' : 'Confirm skip'}
      </button>
    {:else}
      <button class="btn btn-ghost btn-sm" onclick={() => onclose()}>Close</button>
      <button class="btn btn-primary btn-sm" onclick={() => (confirming = true)} disabled={!picked.size || busy}>
        Skip {picked.size || ''} selected
      </button>
    {/if}
  {/snippet}
</ModalShell>

<style>
  .lead { margin: 0 0 0.8rem; max-width: 58ch; }
  .objs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.35rem; }
  .obj { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-bg-2); padding: 0.45rem 0.65rem; }
  .obj.on { border-color: color-mix(in srgb, var(--ophq-danger) 45%, var(--ophq-border)); }
  .obj.gone { opacity: 0.5; }
  .row { display: flex; align-items: center; gap: 0.6rem; cursor: pointer; }
  .obj.gone .row { cursor: default; }
  .row input { width: 16px; height: 16px; accent-color: var(--ophq-danger); flex: 0 0 auto; }
  .nm { flex: 1; min-width: 0; font-size: 0.9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .id { font-size: 0.78rem; color: var(--ophq-muted); }
  .chip { font-size: 0.7rem; padding: 0.1rem 0.45rem; border-radius: 999px; border: 1px solid var(--ophq-border); color: var(--ophq-muted); }
  .reload { margin-top: 0.8rem; }
  .cq { margin: 0.9rem 0 0; font-size: 0.9rem; }
  .tiny { font-size: 0.8rem; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0.8rem 0 0; }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .btn-danger:hover { background: #ff7280; color: #fff; }
</style>
