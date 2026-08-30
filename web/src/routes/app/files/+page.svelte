<script>
  // OpenPrintHQ - the tenant's model library.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // Native pages, not a frame. The library is our fork of GyroidVault, one
  // container per tenant, reached through the control-plane on this origin, so
  // there is no second application, no second session and no iframe.
  //
  // Falls back to the engine-backed file list when no library is configured,
  // which is any deployment without the image. A missing library degrades to
  // the previous behaviour rather than to a broken tab.
  import { onMount } from 'svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import EngineFiles from '$lib/components/EngineFiles.svelte';
  import LibraryBrowse from '$lib/components/LibraryBrowse.svelte';
  import { library, libraryAsset } from '$lib/library.js';

  let state_ = $state('checking'); // checking | library | fallback
  let error = $state(null);
  // Two ways into the same objects: the library's index, and the bucket as the
  // person laid it out. Neither is a subset of the other, because a folder can
  // hold files the index has not scanned yet.
  let view = $state('models'); // models | folders

  let models = $state([]);
  let categories = $state([]);
  let totalItems = $state(0);
  let totalPages = $state(1);
  let loading = $state(false);

  let search = $state('');
  let category = $state('');
  let sort = $state('updated');
  let page_ = $state(1);

  // Debounced so typing does not fire a request per keystroke. Kept small: the
  // library is a container on the same host, so the round trip is cheap and a
  // long delay just feels broken.
  let searchTimer;
  function onSearchInput() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { page_ = 1; load(); }, 250);
  }

  async function load() {
    loading = true;
    try {
      const d = await library.models({ search, category, sort, page: page_, limit: 24 });
      models = d.models || [];
      totalItems = d.totalItems || 0;
      totalPages = d.totalPages || 1;
      error = null;
    } catch (e) {
      error = e.message;
      models = [];
    } finally {
      loading = false;
    }
  }

  function setSort(v) { sort = v; page_ = 1; load(); }
  function setCategory(v) { category = v; page_ = 1; load(); }
  function go(p) { page_ = Math.min(Math.max(1, p), totalPages); load(); }

  onMount(async () => {
    const s = await library.status();
    if (!s.available) { state_ = 'fallback'; return; }
    state_ = 'library';
    // Categories are a filter, not the page: a failure here must not stop the
    // grid rendering.
    library.categories().then((c) => { categories = c || []; }).catch(() => {});
    await load();
  });
</script>

<PageTitle page="Files" />

{#if state_ === 'checking'}
  <p class="muted">Opening your library...</p>
{:else if state_ === 'fallback'}
  <EngineFiles />
{:else}
  <div class="tabs">
    <button class:on={view === 'models'} onclick={() => (view = 'models')}>Models</button>
    <button class:on={view === 'folders'} onclick={() => (view = 'folders')}>Folders</button>
  </div>

{#if view === 'folders'}
  <LibraryBrowse />
{:else}
  <div class="bar">
    <input
      class="search"
      type="search"
      placeholder="Search models"
      bind:value={search}
      oninput={onSearchInput}
    />
    <select value={category} onchange={(e) => setCategory(e.currentTarget.value)}>
      <option value="">All categories</option>
      {#each categories as c (c.id)}
        <option value={c.id}>{c.name}</option>
      {/each}
    </select>
    <select value={sort} onchange={(e) => setSort(e.currentTarget.value)}>
      <option value="updated">Recently updated</option>
      <option value="created">Newest</option>
      <option value="name">Name</option>
      <option value="files">Most files</option>
      <option value="prints">Most printed</option>
    </select>
    <span class="count">{totalItems} model{totalItems === 1 ? '' : 's'}</span>
  </div>

  {#if error}<p class="warn">{error}</p>{/if}

  {#if loading && models.length === 0}
    <p class="muted">Loading...</p>
  {:else if models.length === 0}
    <p class="muted">
      {search || category ? 'Nothing matches that.' : 'This library is empty. Files added to your storage appear here after a scan.'}
    </p>
  {:else}
    <div class="grid" class:dim={loading}>
      {#each models as m (m.id)}
        <a class="card" href={`/app/files/${m.id}`}>
          <div class="thumb">
            {#if m.thumbnail}
              <img src={libraryAsset(m.thumbnail)} alt="" loading="lazy" />
            {:else}
              <span class="noimg">{(m.file_types || []).join(' ') || 'no preview'}</span>
            {/if}
          </div>
          <div class="meta">
            <span class="name" title={m.name}>{m.name}</span>
            <span class="sub">
              {m.file_count} file{m.file_count === 1 ? '' : 's'}
              {#if m.print_count}&middot; {m.print_count} print{m.print_count === 1 ? '' : 's'}{/if}
              {#if m.category_name}&middot; {m.category_name}{/if}
            </span>
          </div>
        </a>
      {/each}
    </div>

    {#if totalPages > 1}
      <div class="pager">
        <button onclick={() => go(page_ - 1)} disabled={page_ <= 1}>Previous</button>
        <span class="muted">Page {page_} of {totalPages}</span>
        <button onclick={() => go(page_ + 1)} disabled={page_ >= totalPages}>Next</button>
      </div>
    {/if}
  {/if}
{/if}
{/if}

<style>
  .tabs { display: flex; gap: 0.4rem; margin-bottom: 0.9rem; }
  .tabs button {
    padding: 0.35rem 0.9rem;
    border: 1px solid var(--ophq-border); border-radius: 999px;
    background: var(--ophq-surface); color: var(--ophq-text-2);
    cursor: pointer; font: inherit;
  }
  .tabs button.on { border-color: var(--ophq-primary); color: var(--ophq-text); }

  .bar {
    display: flex;
    gap: 0.6rem;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 1rem;
  }
  .search { flex: 1 1 16rem; min-width: 12rem; }
  .search, select {
    padding: 0.5rem 0.7rem;
    border: 1px solid var(--ophq-border);
    border-radius: 8px;
    background: var(--ophq-surface);
    color: var(--ophq-text);
  }
  .count { color: var(--ophq-muted); font-size: 0.85rem; margin-left: auto; }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
    gap: 0.9rem;
  }
  /* Dimmed rather than replaced while refetching: swapping the grid for a
     spinner on every keystroke makes the page flicker and loses scroll. */
  .dim { opacity: 0.55; }

  .card {
    display: block;
    border: 1px solid var(--ophq-border);
    border-radius: 12px;
    overflow: hidden;
    background: var(--ophq-surface);
    color: inherit;
    text-decoration: none;
    transition: border-color 0.15s ease;
  }
  .card:hover { border-color: var(--ophq-primary); }

  .thumb {
    aspect-ratio: 1 / 1;
    display: grid;
    place-items: center;
    background: var(--ophq-bg-2);
    overflow: hidden;
  }
  .thumb img { width: 100%; height: 100%; object-fit: cover; }
  .noimg { color: var(--ophq-muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }

  .meta { padding: 0.55rem 0.65rem 0.7rem; display: grid; gap: 0.15rem; }
  .name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sub { color: var(--ophq-muted); font-size: 0.78rem; }

  .pager { display: flex; gap: 0.8rem; align-items: center; justify-content: center; margin-top: 1.2rem; }
  .pager button {
    padding: 0.4rem 0.9rem;
    border: 1px solid var(--ophq-border);
    border-radius: 8px;
    background: var(--ophq-surface);
    color: var(--ophq-text);
    cursor: pointer;
  }
  .pager button:disabled { opacity: 0.4; cursor: default; }

  .muted { color: var(--ophq-muted); }
  .warn { color: var(--ophq-warn); }
</style>
