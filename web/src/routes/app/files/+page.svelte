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
  import LibraryCollections from '$lib/components/LibraryCollections.svelte';
  import LibrarySettings from '$lib/components/LibrarySettings.svelte';
  import LibraryUpload from '$lib/components/LibraryUpload.svelte';
  import { library, libraryAsset, previewFile, objectKeyFor, fileStates,
           addTagsTo, inBatches, copyLibraryObject, deleteLibraryObject,
           libraryIntegrity, integrityForModel, describeFindings } from '$lib/library.js';
  import { goto } from '$app/navigation';

  let state_ = $state('checking'); // checking | library | fallback
  let error = $state(null);
  // Two ways into the same objects: the library's index, and the bucket as the
  // person laid it out. Neither is a subset of the other, because a folder can
  // hold files the index has not scanned yet.
  let view = $state('models'); // models | folders | collections | settings
  let uploader;                // LibraryUpload, for the drop zone below
  let dragging = $state(false);
  // Multi-select. A slicer session is created once with a fixed environment, so
  // opening several models means naming them all before it starts rather than
  // handing them to a running desktop one at a time.
  let selected = $state(new Set());
  let selecting = $state(false);
  let sendError = $state(null);
  let bulkBusy = $state(false);
  let bulkNote = $state(null);
  let collections = $state([]);
  // Mesh integrity, computed by the engine and joined on the object key. A
  // separate request from the model list because it comes from a different
  // service, and because the grid must render whether or not it answers.
  let integrity = $state({ enabled: false, results: {} });

  function toggle(id) {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    selected = next;
  }
  function clearSelection() { selected = new Set(); }
  function selectAllOnPage() { selected = new Set(models.map((m) => m.id)); }

  const chosenIds = () => models.filter((m) => selected.has(m.id)).map((m) => m.id);

  /** The deepest folder every one of these keys sits under, '' if they share none. */
  function commonFolder(keys) {
    const parts = keys.map((k) => k.split('/').slice(0, -1));
    if (parts.length === 0) return '';
    const out = [];
    for (let i = 0; i < parts[0].length; i++) {
      const seg = parts[0][i];
      if (parts.every((p) => p[i] === seg)) out.push(seg); else break;
    }
    return out.join('/');
  }

  async function withBulk(label, fn) {
    bulkBusy = true; bulkNote = null;
    try {
      const r = await fn();
      bulkNote = r || label;
      clearSelection();
      await load();
    } catch (e) {
      bulkNote = `${label} failed: ${e.message}`;
    } finally {
      bulkBusy = false;
    }
  }

  const bulkTag = () => {
    const raw = prompt('Add tags (comma separated)');
    if (!raw) return;
    const ids = chosenIds();
    return withBulk('Tagging', async () => {
      await addTagsTo(ids, raw.split(','));
      return `Tagged ${ids.length} model${ids.length === 1 ? '' : 's'}.`;
    });
  };

  const bulkCategory = (value) => {
    if (value === '') return;
    const ids = chosenIds();
    return withBulk('Setting the category', async () => {
      await library.bulk.update(ids, { category_id: value === 'none' ? null : Number(value) });
      return `Moved ${ids.length} model${ids.length === 1 ? '' : 's'} into a category.`;
    });
  };

  const bulkCollection = (value) => {
    if (!value) return;
    const ids = chosenIds();
    return withBulk('Adding to the collection', async () => {
      await library.bulk.addToCollection(Number(value), ids);
      return `Added ${ids.length} model${ids.length === 1 ? '' : 's'} to the collection.`;
    });
  };

  // Moving and deleting act on the OBJECT STORE, not on the index. The
  // library's bucket mount is read-only, so its own bulk-move and bulk-delete
  // cannot touch a file; they would report success and change nothing on disk.
  async function bulkMove() {
    const folder = prompt('Move the files of the selected models into which folder?');
    if (folder === null) return;
    const dest = folder.replace(/^\/+|\/+$/g, '');
    const ids = chosenIds();
    return withBulk('Moving', async () => {
      const full = await inBatches(ids, (id) => library.model(id).catch(() => null));
      // Each model keeps its own folder under the destination, and any nesting
      // inside it. Flattening to basenames would have two models that both hold
      // a plate.gcode overwrite each other, silently and unrecoverably.
      const jobs = [];
      for (const m of full.filter(Boolean)) {
        const keys = (m.files || []).map(objectKeyFor).filter(Boolean);
        if (keys.length === 0) continue;
        const prefix = commonFolder(keys);
        const folder = prefix.split('/').filter(Boolean).pop() || String(m.id);
        for (const key of keys) {
          const rel = prefix && key.startsWith(prefix + '/') ? key.slice(prefix.length + 1) : key.split('/').pop();
          jobs.push({ key, to: [dest, folder, rel].filter(Boolean).join('/') });
        }
      }
      let moved = 0, failed = 0;
      await inBatches(jobs, async (j) => {
        if (j.key === j.to) { moved++; return; }
        try { await copyLibraryObject(j.key, j.to, { move: true }); moved++; } catch { failed++; }
      });
      return failed
        ? `Moved ${moved} file${moved === 1 ? '' : 's'}, ${failed} could not be moved.`
        : `Moved ${moved} file${moved === 1 ? '' : 's'}.`;
    });
  }

  async function bulkDelete() {
    const ids = chosenIds();
    if (!confirm(`Delete the files of ${ids.length} model${ids.length === 1 ? '' : 's'} from your storage? This cannot be undone.`)) return;
    return withBulk('Deleting', async () => {
      const full = await inBatches(ids, (id) => library.model(id).catch(() => null));
      const files = full.filter(Boolean).flatMap((m) => m.files || []);
      let gone = 0, failed = 0;
      await inBatches(files, async (f) => {
        try { await deleteLibraryObject(objectKeyFor(f)); gone++; } catch { failed++; }
      });
      // The index entries go too, or the models linger as empty shells until
      // the next scan notices their files are missing.
      await library.bulk.remove(ids, false).catch(() => {});
      return failed
        ? `Deleted ${gone} file${gone === 1 ? '' : 's'}, ${failed} could not be deleted.`
        : `Deleted ${gone} file${gone === 1 ? '' : 's'}.`;
    });
  }

  async function sendSelectedToSlicer() {
    sendError = null;
    const chosen = models.filter((m) => selected.has(m.id));
    if (chosen.length === 0) return;
    selecting = true;
    try {
      // The grid rows carry no file list, so each model has to be read for the
      // file worth slicing. Done in parallel: serially this is one round trip
      // per model and feels broken on a large selection.
      const full = await Promise.all(chosen.map((m) => library.model(m.id).catch(() => null)));
      // Prefer whatever was marked known good. If someone has said which plate
      // prints, opening a different one is the wrong answer even when it is a
      // better file format.
      const keys = full.map((m) => {
        if (!m) return '';
        const st = fileStates(m);
        const good = (m.files || []).find((f) => st[objectKeyFor(f)] === 'known-good');
        return objectKeyFor(good || previewFile(m));
      }).filter(Boolean);
      if (keys.length === 0) { sendError = 'None of those models has a file that can be opened.'; return; }
      const q = new URLSearchParams();
      for (const k of keys) q.append('keys', k);
      if (keys.length === 1) q.set('name', chosen[0].name);
      goto('/app/slicer?' + q.toString());
    } catch (e) {
      sendError = e.message;
    } finally {
      selecting = false;
    }
  }

  // After an upload the library has to scan before a file becomes a model, and
  // that is asynchronous. Reload once now and once shortly after, so the grid
  // catches up without the person having to work out that they should refresh.
  function afterUpload() {
    load();
    setTimeout(load, 4000);
  }

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
    library.projects.list().then((c) => { collections = c || []; }).catch(() => {});
    libraryIntegrity().then((r) => { integrity = r; });
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
    <button class:on={view === 'collections'} onclick={() => (view = 'collections')}>Collections</button>
    <button class:on={view === 'settings'} onclick={() => (view = 'settings')}>Settings</button>
  </div>

{#if view === 'folders'}
  <LibraryBrowse />
{:else if view === 'collections'}
  <LibraryCollections />
{:else if view === 'settings'}
  <LibrarySettings />
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
    <LibraryUpload bind:this={uploader} ondone={afterUpload} />
    <span class="count">{totalItems} model{totalItems === 1 ? '' : 's'}</span>
  </div>

  <div class="selbar" class:idle={selected.size === 0}>
    {#if selected.size === 0}
      <button class="act" onclick={selectAllOnPage} disabled={models.length === 0}>Select all on this page</button>
    {:else}
      <span>{selected.size} selected</span>
      <button class="act" onclick={sendSelectedToSlicer} disabled={selecting || bulkBusy}>
        {selecting ? 'Opening...' : 'Send to slicer'}
      </button>
      <button class="act" onclick={bulkTag} disabled={bulkBusy}>Add tags</button>
      <select disabled={bulkBusy} onchange={(e) => { bulkCategory(e.currentTarget.value); e.currentTarget.value = ''; }}>
        <option value="">Set category...</option>
        <option value="none">No category</option>
        {#each categories as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
      </select>
      {#if collections.length > 0}
        <select disabled={bulkBusy} onchange={(e) => { bulkCollection(e.currentTarget.value); e.currentTarget.value = ''; }}>
          <option value="">Add to collection...</option>
          {#each collections as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
        </select>
      {/if}
      <button class="act" onclick={bulkMove} disabled={bulkBusy}>Move files</button>
      <button class="act danger" onclick={bulkDelete} disabled={bulkBusy}>Delete</button>
      <button class="act" onclick={clearSelection} disabled={bulkBusy}>Clear</button>
    {/if}
    {#if bulkBusy}<span class="muted">Working...</span>{/if}
    {#if bulkNote}<span class="muted">{bulkNote}</span>{/if}
    {#if sendError}<span class="warn">{sendError}</span>{/if}
  </div>

  {#if error}<p class="warn">{error}</p>{/if}

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="dropzone"
  class:over={dragging}
  ondragover={(e) => { e.preventDefault(); dragging = true; }}
  ondragleave={() => (dragging = false)}
  ondrop={(e) => { e.preventDefault(); dragging = false; uploader?.send(e.dataTransfer?.files); }}
>
  {#if loading && models.length === 0}
    <p class="muted">Loading...</p>
  {:else if models.length === 0}
    <p class="muted">
      {search || category
        ? 'Nothing matches that.'
        : 'This library is empty. Drop files here, or use Upload above. They appear as models once the library scans them.'}
    </p>
  {:else}
    <div class="grid" class:dim={loading}>
      {#each models as m (m.id)}
        <div class="card" class:picked={selected.has(m.id)}>
        <label class="pick">
          <input type="checkbox" checked={selected.has(m.id)} onchange={() => toggle(m.id)} />
        </label>
        <a href={`/app/files/${m.id}`}>
          <div class="thumb">
            {#if m.thumbnail}
              <img src={libraryAsset(m.thumbnail)} alt="" loading="lazy" />
            {:else}
              <span class="noimg">{(m.file_types || []).join(' ') || 'no preview'}</span>
            {/if}
          </div>
          <div class="meta">
            <span class="name" title={m.name}>{m.name}</span>
            {#if integrityForModel(integrity.results, m)?.status === 'problems'}
              {@const bad = integrityForModel(integrity.results, m)}
              <span
                class="mesh bad"
                title={`Mesh problem: ${describeFindings(bad.first?.findings)}. A mesh like this often fails in a way that looks like a printer fault.`}
              >
                Mesh problem{bad.problems > 1 ? ` (${bad.problems} files)` : ''}
              </span>
            {/if}
            <span class="sub">
              {m.file_count} file{m.file_count === 1 ? '' : 's'}
              {#if m.print_count}&middot; {m.print_count} print{m.print_count === 1 ? '' : 's'}{/if}
              {#if m.category_name}&middot; {m.category_name}{/if}
            </span>
          </div>
        </a>
        </div>
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
</div>
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

  /* Only a problem is badged. A clean result is the expected case and a badge
     on every card teaches people to stop reading badges. */
  .mesh {
    align-self: flex-start;
    font-size: 0.72rem;
    padding: 0.1rem 0.45rem;
    border-radius: 999px;
    border: 1px solid var(--ophq-border);
    color: var(--ophq-text-2);
  }
  .mesh.bad { border-color: var(--ophq-danger, #c0392b); color: var(--ophq-danger, #c0392b); }

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

  .dropzone { border: 1px dashed transparent; border-radius: 12px; padding: 0.4rem; min-height: 6rem; }
  .dropzone.over { border-color: var(--ophq-primary); background: var(--ophq-bg-2); }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
    gap: 0.9rem;
  }
  /* Dimmed rather than replaced while refetching: swapping the grid for a
     spinner on every keystroke makes the page flicker and loses scroll. */
  .dim { opacity: 0.55; }

  .card {
    position: relative;
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
  .card.picked { border-color: var(--ophq-primary); box-shadow: 0 0 0 1px var(--ophq-primary) inset; }
  .card > a { display: block; color: inherit; text-decoration: none; }
  .pick {
    position: absolute; top: 0.4rem; left: 0.4rem; z-index: 2;
    background: var(--ophq-surface); border: 1px solid var(--ophq-border);
    border-radius: 6px; padding: 0.15rem 0.25rem; line-height: 0;
  }
  .selbar {
    display: flex; gap: 0.6rem; align-items: center; flex-wrap: wrap;
    margin-bottom: 0.8rem; padding: 0.5rem 0.7rem;
    border: 1px solid var(--ophq-primary); border-radius: 8px; background: var(--ophq-bg-2);
  }
  .selbar.idle { border-color: var(--ophq-border); background: transparent; padding: 0.25rem 0; }
  .selbar select {
    padding: 0.3rem 0.6rem; border: 1px solid var(--ophq-border); border-radius: 8px;
    background: var(--ophq-surface); color: var(--ophq-text); font: inherit; font-size: 0.85rem;
  }
  .act.danger { color: var(--ophq-danger); border-color: color-mix(in srgb, var(--ophq-danger) 40%, var(--ophq-border)); }
  .act {
    padding: 0.3rem 0.75rem; border: 1px solid var(--ophq-border); border-radius: 8px;
    background: var(--ophq-surface); color: var(--ophq-text); cursor: pointer; font: inherit; font-size: 0.85rem;
  }
  .act:disabled { opacity: 0.5; cursor: default; }

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
