<script>
  // OpenPrintHQ - folder view of the tenant's library.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // The model grid is the library as the library sees it; this is the bucket as
  // the person laid it out. Both read the same objects, so a file can be found
  // either way round.
  import { onMount } from 'svelte';
  import { library, libraryAsset, uploadToLibrary } from '$lib/library.js';

  let path = $state('');
  let folders = $state([]);
  let files = $state([]);
  let parentPath = $state(null);
  let loading = $state(false);
  let error = $state(null);
  let notice = $state(null);

  let uploading = $state(null);   // { name, percent }
  let dragging = $state(false);
  let fileInput;

  const crumbs = $derived(
    path ? path.split('/').filter(Boolean).map((name, i, all) => ({
      name, path: all.slice(0, i + 1).join('/')
    })) : []
  );

  async function load(p = path) {
    loading = true;
    try {
      const d = await library.browse(p);
      path = d.currentPath ?? p;
      parentPath = d.parentPath;
      folders = d.folders || [];
      files = d.files || [];
      error = null;
    } catch (e) {
      error = e.message;
      folders = []; files = [];
    } finally {
      loading = false;
    }
  }

  async function upload(list) {
    const chosen = Array.from(list || []);
    if (chosen.length === 0) return;
    notice = null;
    for (const f of chosen) {
      uploading = { name: f.name, percent: 0 };
      try {
        await uploadToLibrary(f, path, (percent) => { uploading = { name: f.name, percent }; });
      } catch (e) {
        notice = `Could not upload ${f.name}: ${e.message}`;
        uploading = null;
        return;
      }
    }
    uploading = null;
    notice = chosen.length === 1 ? `Uploaded ${chosen[0].name}.` : `Uploaded ${chosen.length} files.`;
    // The scan is asynchronous on the library's side, so a reload straight
    // after the PUT can still miss the new row. Reload anyway: it is right for
    // the folder listing, which reads the bucket rather than the index.
    await load();
  }

  async function newFolder() {
    const name = prompt('New folder name');
    if (!name) return;
    try {
      await library.mkdir(path, name);
      await load();
    } catch (e) {
      notice = `Could not create the folder: ${e.message}`;
    }
  }

  function onDrop(e) {
    e.preventDefault();
    dragging = false;
    upload(e.dataTransfer?.files);
  }

  function fmtSize(n) {
    if (!n && n !== 0) return '';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
  }

  onMount(() => load(''));
</script>

<div class="crumbs">
  <button class="crumb" onclick={() => load('')} disabled={!path}>Library</button>
  {#each crumbs as c (c.path)}
    <span class="sep">/</span>
    <button class="crumb" onclick={() => load(c.path)} disabled={c.path === path}>{c.name}</button>
  {/each}
  <span class="spacer"></span>
  <button class="act" onclick={newFolder}>New folder</button>
  <button class="act" onclick={() => fileInput?.click()}>Upload</button>
  <input
    class="hiddenInput"
    type="file"
    multiple
    bind:this={fileInput}
    onchange={(e) => { upload(e.currentTarget.files); e.currentTarget.value = ''; }}
  />
</div>

{#if uploading}
  <p class="notice">Uploading {uploading.name}... {uploading.percent}%</p>
{/if}
{#if notice}<p class="notice">{notice}</p>{/if}
{#if error}<p class="warn">{error}</p>{/if}

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="drop"
  class:over={dragging}
  ondragover={(e) => { e.preventDefault(); dragging = true; }}
  ondragleave={() => (dragging = false)}
  ondrop={onDrop}
>
  {#if loading && folders.length === 0 && files.length === 0}
    <p class="muted">Loading...</p>
  {:else if folders.length === 0 && files.length === 0}
    <p class="muted">This folder is empty. Drop files here to upload.</p>
  {:else}
    {#if parentPath !== null && path}
      <button class="row up" onclick={() => load(parentPath)}>
        <span class="ftype">up</span>
        <span class="fname">..</span>
      </button>
    {/if}

    {#each folders as f (f.path)}
      <button class="row" onclick={() => load(f.path)}>
        <span class="ftype">dir</span>
        <span class="fname">{f.name}</span>
        <span class="fsize muted">{f.itemCount} item{f.itemCount === 1 ? '' : 's'}</span>
      </button>
    {/each}

    {#each files as f (f.url)}
      <div class="row">
        <span class="ftype">{f.ext || f.type}</span>
        {#if f.model_id}
          <a class="fname" href={`/app/files/${f.model_id}`}>{f.name}</a>
        {:else}
          <span class="fname">{f.name}</span>
        {/if}
        <span class="fsize muted">{fmtSize(f.size)}</span>
        <a class="act" href={libraryAsset(f.url)} download={f.name}>Download</a>
      </div>
    {/each}
  {/if}
</div>

<style>
  .crumbs { display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap; margin-bottom: 0.8rem; }
  .crumb { background: none; border: 0; color: var(--ophq-primary); cursor: pointer; padding: 0.1rem 0.2rem; font: inherit; }
  .crumb:disabled { color: var(--ophq-text); cursor: default; }
  .sep { color: var(--ophq-muted); }
  .spacer { flex: 1 1 auto; }
  .hiddenInput { display: none; }

  .drop {
    display: grid; gap: 0.35rem;
    padding: 0.6rem;
    border: 1px dashed var(--ophq-border);
    border-radius: 12px;
    min-height: 8rem;
  }
  .drop.over { border-color: var(--ophq-primary); background: var(--ophq-bg-2); }

  .row {
    display: flex; gap: 0.7rem; align-items: center; width: 100%;
    padding: 0.5rem 0.7rem; text-align: left;
    border: 1px solid var(--ophq-border); border-radius: 8px;
    background: var(--ophq-surface); color: var(--ophq-text);
    font: inherit; cursor: pointer;
  }
  .row:not(button) { cursor: default; }
  .up { color: var(--ophq-muted); }
  .ftype {
    text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em;
    color: var(--ophq-primary); min-width: 3.2rem;
  }
  .fname { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fsize { font-size: 0.8rem; }
  .act {
    white-space: nowrap; font-size: 0.8rem;
    padding: 0.25rem 0.6rem; border: 1px solid var(--ophq-border); border-radius: 6px;
    background: var(--ophq-surface); color: var(--ophq-text); cursor: pointer; text-decoration: none;
  }
  .notice { color: var(--ophq-text-2); }
  .muted { color: var(--ophq-muted); }
  .warn { color: var(--ophq-warn); }
</style>
