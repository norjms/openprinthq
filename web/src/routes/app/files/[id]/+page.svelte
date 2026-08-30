<script>
  // OpenPrintHQ - a single model in the tenant's library.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // Files, downloads and metadata. The 3D preview is added on top of this in a
  // later step; the page is useful without it, so it ships first.
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import { library, libraryAsset } from '$lib/library.js';

  let model = $state(null);
  let error = $state(null);
  let loading = $state(true);

  const id = $derived($page.params.id);

  async function load() {
    loading = true;
    try {
      model = await library.model(id);
      error = null;
    } catch (e) {
      error = e.message;
      model = null;
    } finally {
      loading = false;
    }
  }

  function fmtSize(n) {
    if (!n && n !== 0) return '';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
  }

  onMount(load);
</script>

<PageTitle page={model ? model.name : 'Model'} />

<p class="back"><a href="/app/files">Back to library</a></p>

{#if loading}
  <p class="muted">Loading...</p>
{:else if error}
  <p class="warn">{error}</p>
{:else if model}
  <div class="head">
    {#if model.thumbnail}
      <img class="hero" src={libraryAsset(model.thumbnail)} alt="" />
    {/if}
    <div>
      <h2>{model.name}</h2>
      {#if model.category_name}<span class="chip">{model.category_name}</span>{/if}
      {#each model.tags || [] as t (t.id ?? t.name)}<span class="chip">{t.name ?? t}</span>{/each}
      {#if model.description}<p class="desc">{model.description}</p>{/if}
      {#if model.source_url}
        <p><a href={model.source_url} target="_blank" rel="noreferrer noopener">Source</a></p>
      {/if}
    </div>
  </div>

  {#if model.print_tips}
    <section>
      <h3>Print tips</h3>
      <p class="desc">{model.print_tips}</p>
    </section>
  {/if}

  <section>
    <h3>Files</h3>
    {#if (model.files || []).length === 0}
      <p class="muted">No files.</p>
    {:else}
      <ul class="files">
        {#each model.files as f (f.id)}
          <li>
            <span class="ftype">{f.file_type}</span>
            <span class="fname" title={f.filename}>{f.filename}</span>
            <span class="fsize muted">{fmtSize(f.file_size)}</span>
            <a class="dl" href={libraryAsset(f.url)} download={f.filename}>Download</a>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}

<style>
  .back { margin: 0 0 0.8rem; }
  .head { display: flex; gap: 1.2rem; align-items: flex-start; flex-wrap: wrap; margin-bottom: 1.4rem; }
  .hero {
    width: 14rem; height: 14rem; object-fit: cover;
    border: 1px solid var(--ophq-border); border-radius: 12px; background: var(--ophq-bg-2);
  }
  h2 { margin: 0 0 0.4rem; }
  h3 { margin: 0 0 0.5rem; font-size: 1rem; }
  section { margin-bottom: 1.4rem; }
  .desc { color: var(--ophq-text-2); white-space: pre-wrap; }
  .chip {
    display: inline-block; margin: 0 0.35rem 0.35rem 0; padding: 0.12rem 0.5rem;
    border: 1px solid var(--ophq-border); border-radius: 999px;
    font-size: 0.75rem; color: var(--ophq-text-2);
  }
  .files { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.35rem; }
  .files li {
    display: flex; gap: 0.7rem; align-items: center;
    padding: 0.5rem 0.7rem;
    border: 1px solid var(--ophq-border); border-radius: 8px; background: var(--ophq-surface);
  }
  .ftype {
    text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em;
    color: var(--ophq-primary); min-width: 3rem;
  }
  .fname { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fsize { font-size: 0.8rem; }
  .dl { white-space: nowrap; }
  .muted { color: var(--ophq-muted); }
  .warn { color: var(--ophq-warn); }
</style>
