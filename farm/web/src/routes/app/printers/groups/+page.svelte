<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import { prettyModel } from '$lib/models.js';

  let loading = $state(true);
  let error = $state(null);
  let groups = $state([]);
  let printers = $state([]);
  let busy = $state(false);
  let notice = $state(null);

  // Create form
  let showCreate = $state(false);
  let newName = $state('');
  let newDesc = $state('');
  let newColor = $state('#4f8a6d');
  let newMembers = $state([]);

  // Inline edit
  let editingId = $state(null);
  let editName = $state('');
  let editDesc = $state('');
  let editColor = $state('#4f8a6d');
  let editMembers = $state([]);

  function asArray(r) {
    return Array.isArray(r) ? r : (r?.items || r?.results || []);
  }

  async function load(initial = true) {
    if (initial) { loading = true; error = null; }
    try {
      const [g, p] = await Promise.all([api.printerGroups(), api.printers()]);
      groups = asArray(g);
      printers = asArray(p);
      error = null;
    } catch (e) {
      error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable');
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function toggle(list, id) {
    return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
  }

  function resetCreate() {
    showCreate = false;
    newName = '';
    newDesc = '';
    newColor = '#4f8a6d';
    newMembers = [];
  }

  async function create() {
    if (!newName.trim()) return;
    busy = true; notice = null;
    try {
      await api.createPrinterGroup({
        name: newName.trim(),
        description: newDesc.trim() || null,
        color: newColor,
        printer_ids: newMembers
      });
      resetCreate();
      await load(false);
    } catch (e) {
      notice = e.body?.detail || e.message || 'Could not create the group';
    } finally {
      busy = false;
    }
  }

  function startEdit(g) {
    editingId = g.id;
    editName = g.name;
    editDesc = g.description || '';
    editColor = g.color || '#4f8a6d';
    editMembers = (g.printers || []).map((p) => p.id);
    notice = null;
  }

  async function saveEdit() {
    busy = true; notice = null;
    try {
      await api.updatePrinterGroup(editingId, {
        name: editName.trim(),
        description: editDesc.trim(),
        color: editColor,
        printer_ids: editMembers
      });
      editingId = null;
      await load(false);
    } catch (e) {
      notice = e.body?.detail || e.message || 'Could not save the group';
    } finally {
      busy = false;
    }
  }

  async function remove(g) {
    if (!confirm(`Delete the group "${g.name}"? Printers themselves are not affected.`)) return;
    busy = true; notice = null;
    try {
      await api.deletePrinterGroup(g.id);
      await load(false);
    } catch (e) {
      // The engine refuses with 409 while pending queue items still target it.
      notice = e.body?.detail || e.message || 'Could not delete the group';
    } finally {
      busy = false;
    }
  }

  function label(p) {
    const m = prettyModel(p.model) || p.model || '';
    return m ? `${p.name} · ${m}` : p.name;
  }
</script>

<PageTitle page="Printer groups" />

<div class="head">
  <div>
    <h1>Printer groups</h1>
    <p class="muted">
      A group is a queue target. Send a job to a group and it runs on whichever member frees
      up first, so a farm keeps working without anyone assigning machines by hand.
    </p>
  </div>
  <div class="flex gap">
    <a class="btn btn-ghost btn-sm" href="/app/printers">Back to printers</a>
    <button class="btn btn-primary btn-sm" onclick={() => (showCreate = !showCreate)}>
      {showCreate ? 'Cancel' : '+ New group'}
    </button>
  </div>
</div>

{#if notice}
  <div class="card card-pad notice">{notice}</div>
{/if}

{#if showCreate}
  <div class="card card-pad form">
    <div class="row">
      <label class="fld">
        <span>Name</span>
        <input type="text" bind:value={newName} placeholder="PLA farm" />
      </label>
      <label class="fld grow">
        <span>Description</span>
        <input type="text" bind:value={newDesc} placeholder="Optional" />
      </label>
      <label class="fld color">
        <span>Colour</span>
        <input type="color" bind:value={newColor} />
      </label>
    </div>
    <div class="members">
      <span class="mlabel">Members</span>
      {#if printers.length === 0}
        <p class="muted">No printers yet. Add a printer first.</p>
      {:else}
        <div class="chips">
          {#each printers as p (p.id)}
            <button
              type="button"
              class="chip {newMembers.includes(p.id) ? 'on' : ''}"
              onclick={() => (newMembers = toggle(newMembers, p.id))}
            >{label(p)}</button>
          {/each}
        </div>
      {/if}
    </div>
    <div class="flex gap">
      <button class="btn btn-primary btn-sm" onclick={create} disabled={busy || !newName.trim()}>
        Create group
      </button>
      <button class="btn btn-ghost btn-sm" onclick={resetCreate} disabled={busy}>Cancel</button>
    </div>
  </div>
{/if}

{#if loading}
  <div class="card card-pad muted">Connecting to your engine…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad">
    <p>Your instance is still being provisioned. Groups appear once the engine is up.</p>
  </div>
{:else if error}
  <div class="card card-pad">
    <p>Could not reach the engine: {error}</p>
    <button class="btn btn-ghost btn-sm" onclick={() => load()}>Retry</button>
  </div>
{:else if groups.length === 0}
  <div class="card card-pad empty">
    <p>No groups yet.</p>
    <p class="muted">
      Groups are most useful when several printers can run the same job, for example every
      machine loaded with PLA, or everything in one room.
    </p>
  </div>
{:else}
  <div class="glist">
    {#each groups as g (g.id)}
      <div class="card card-pad group">
        {#if editingId === g.id}
          <div class="row">
            <label class="fld">
              <span>Name</span>
              <input type="text" bind:value={editName} />
            </label>
            <label class="fld grow">
              <span>Description</span>
              <input type="text" bind:value={editDesc} />
            </label>
            <label class="fld color">
              <span>Colour</span>
              <input type="color" bind:value={editColor} />
            </label>
          </div>
          <div class="members">
            <span class="mlabel">Members</span>
            <div class="chips">
              {#each printers as p (p.id)}
                <button
                  type="button"
                  class="chip {editMembers.includes(p.id) ? 'on' : ''}"
                  onclick={() => (editMembers = toggle(editMembers, p.id))}
                >{label(p)}</button>
              {/each}
            </div>
          </div>
          <div class="flex gap">
            <button class="btn btn-primary btn-sm" onclick={saveEdit} disabled={busy || !editName.trim()}>Save</button>
            <button class="btn btn-ghost btn-sm" onclick={() => (editingId = null)} disabled={busy}>Cancel</button>
          </div>
        {:else}
          <div class="ghead">
            <span class="swatch" style="background: {g.color || 'var(--ophq-primary)'}"></span>
            <h3>{g.name}</h3>
            <span class="count muted">{g.printer_count} printer{g.printer_count === 1 ? '' : 's'}</span>
            <span class="spacer"></span>
            <button class="btn btn-ghost btn-xs" onclick={() => startEdit(g)}>Edit</button>
            <button class="btn btn-danger btn-xs" onclick={() => remove(g)} disabled={busy}>Delete</button>
          </div>
          {#if g.description}<p class="muted desc">{g.description}</p>{/if}
          {#if g.printers?.length}
            <div class="chips">
              {#each g.printers as p (p.id)}
                <a class="chip static {p.is_active ? '' : 'off'}" href="/app/printers/{p.id}">
                  {label(p)}{p.is_active ? '' : ' · inactive'}
                </a>
              {/each}
            </div>
          {:else}
            <p class="muted">No printers in this group yet. Jobs aimed at it will not start.</p>
          {/if}
        {/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; gap: 1rem; }
  .head h1 { margin: 0; }
  .head p { margin: 0.35rem 0 0; max-width: 62ch; }
  .notice { margin-bottom: 1rem; border-color: var(--ophq-danger); }
  .form { margin-bottom: 1.2rem; display: flex; flex-direction: column; gap: 0.9rem; }
  .row { display: flex; gap: 0.8rem; flex-wrap: wrap; align-items: flex-end; }
  .fld { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.85rem; }
  .fld.grow { flex: 1 1 16rem; }
  .fld.color input { width: 3rem; padding: 0.1rem; height: 2.1rem; }
  .fld input[type='text'] { padding: 0.4rem 0.55rem; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); background: var(--ophq-bg-2); color: var(--ophq-text); }
  .members { display: flex; flex-direction: column; gap: 0.4rem; }
  .mlabel { font-size: 0.85rem; font-weight: 600; }
  .chips { display: flex; flex-wrap: wrap; gap: 0.4rem; }
  .chip { font-size: 0.8rem; padding: 0.2rem 0.6rem; border-radius: 999px; border: 1px solid var(--ophq-border); background: var(--ophq-bg-2); color: var(--ophq-text); cursor: pointer; text-decoration: none; }
  .chip.on { border-color: var(--ophq-primary); background: rgba(79, 138, 109, 0.14); }
  .chip.static { cursor: default; }
  .chip.off { opacity: 0.55; }
  .glist { display: flex; flex-direction: column; gap: 0.9rem; }
  .group { display: flex; flex-direction: column; gap: 0.6rem; }
  .ghead { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
  .ghead h3 { margin: 0; font-size: 1.05rem; }
  .swatch { width: 0.75rem; height: 0.75rem; border-radius: 999px; display: inline-block; }
  .spacer { margin-left: auto; }
  .desc { margin: 0; }
  .empty { text-align: center; }
</style>
