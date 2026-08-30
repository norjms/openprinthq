<script>
  // OpenPrintHQ - collections in the tenant's library.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // The library calls these projects. They are named collections here because
  // OpenPrintHQ already has a Projects tab meaning something else, and two
  // things called the same word in one application is the confusion this whole
  // rewrite exists to remove.
  import { onMount } from 'svelte';
  import { library, libraryAsset } from '$lib/library.js';

  let collections = $state([]);
  let open = $state(null);      // the collection being viewed, with its models
  let loading = $state(true);
  let error = $state(null);
  let creating = $state(false);
  let name = $state('');
  let description = $state('');

  async function load() {
    loading = true;
    try {
      collections = await library.projects.list();
      error = null;
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  async function view(id) {
    try {
      open = await library.projects.get(id);
    } catch (e) {
      error = e.message;
    }
  }

  async function create() {
    if (!name.trim()) return;
    try {
      await library.projects.create({ name: name.trim(), description: description.trim() });
      name = ''; description = ''; creating = false;
      await load();
    } catch (e) {
      error = e.message;
    }
  }

  async function remove(c) {
    if (!confirm(`Delete the collection "${c.name}"? The models stay in the library.`)) return;
    try {
      await library.projects.remove(c.id);
      if (open?.id === c.id) open = null;
      await load();
    } catch (e) {
      error = e.message;
    }
  }

  async function removeModel(modelId) {
    try {
      await library.projects.removeModel(open.id, modelId);
      await view(open.id);
    } catch (e) {
      error = e.message;
    }
  }

  onMount(load);
</script>

{#if error}<p class="warn">{error}</p>{/if}

{#if open}
  <p class="back"><button class="link" onclick={() => (open = null)}>All collections</button></p>
  <h3>{open.name}</h3>
  {#if open.description}<p class="desc">{open.description}</p>{/if}
  {#if (open.models || []).length === 0}
    <p class="muted">Nothing in this collection yet. Add models from a model page.</p>
  {:else}
    <div class="grid">
      {#each open.models as m (m.id)}
        <div class="card">
          <a href={`/app/files/${m.id}`}>
            <div class="thumb">
              {#if m.thumbnail}<img src={libraryAsset(m.thumbnail)} alt="" loading="lazy" />{:else}<span class="noimg">no preview</span>{/if}
            </div>
            <span class="name" title={m.name}>{m.name}</span>
          </a>
          <button class="act" onclick={() => removeModel(m.id)}>Remove</button>
        </div>
      {/each}
    </div>
  {/if}
{:else}
  <div class="bar">
    <button class="act" onclick={() => (creating = !creating)}>{creating ? 'Cancel' : 'New collection'}</button>
  </div>

  {#if creating}
    <div class="form">
      <input placeholder="Name" bind:value={name} />
      <input placeholder="Description (optional)" bind:value={description} />
      <button class="act" onclick={create} disabled={!name.trim()}>Create</button>
    </div>
  {/if}

  {#if loading}
    <p class="muted">Loading...</p>
  {:else if collections.length === 0}
    <p class="muted">No collections yet.</p>
  {:else}
    <ul class="list">
      {#each collections as c (c.id)}
        <li>
          <button class="link name" onclick={() => view(c.id)}>{c.name}</button>
          <span class="muted">{c.model_count} model{c.model_count === 1 ? '' : 's'}</span>
          <button class="act" onclick={() => remove(c)}>Delete</button>
        </li>
      {/each}
    </ul>
  {/if}
{/if}

<style>
  .bar { margin-bottom: 0.8rem; }
  .back { margin: 0 0 0.6rem; }
  h3 { margin: 0 0 0.4rem; }
  .desc { color: var(--ophq-text-2); margin-top: 0; }
  .form { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1rem; }
  .form input {
    padding: 0.45rem 0.7rem; border: 1px solid var(--ophq-border); border-radius: 8px;
    background: var(--ophq-surface); color: var(--ophq-text); flex: 1 1 12rem;
  }
  .list { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.35rem; }
  .list li {
    display: flex; gap: 0.7rem; align-items: center;
    padding: 0.5rem 0.7rem;
    border: 1px solid var(--ophq-border); border-radius: 8px; background: var(--ophq-surface);
  }
  .link { background: none; border: 0; color: var(--ophq-primary); cursor: pointer; padding: 0; font: inherit; }
  .name { flex: 1 1 auto; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: 0.9rem; }
  .card {
    border: 1px solid var(--ophq-border); border-radius: 12px; overflow: hidden;
    background: var(--ophq-surface); display: grid; gap: 0.4rem; padding-bottom: 0.5rem;
  }
  .card a { color: inherit; text-decoration: none; display: grid; gap: 0.3rem; }
  .thumb { aspect-ratio: 1 / 1; display: grid; place-items: center; background: var(--ophq-bg-2); }
  .thumb img { width: 100%; height: 100%; object-fit: cover; }
  .noimg { color: var(--ophq-muted); font-size: 0.72rem; }
  .card .name { padding: 0 0.5rem; font-weight: 600; }
  .act {
    justify-self: start; margin: 0 0.5rem; white-space: nowrap; font-size: 0.8rem;
    padding: 0.25rem 0.6rem; border: 1px solid var(--ophq-border); border-radius: 6px;
    background: var(--ophq-surface); color: var(--ophq-text); cursor: pointer;
  }
  .act:disabled { opacity: 0.5; cursor: default; }
  .muted { color: var(--ophq-muted); }
  .warn { color: var(--ophq-warn); }
</style>
