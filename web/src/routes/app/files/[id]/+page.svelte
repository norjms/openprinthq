<script>
  // OpenPrintHQ - a single model in the tenant's library.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // Preview, files, downloads and send-to-queue. Meshes go to ModelViewer
  // (three, loaded with this route); g-code reuses GcodeViewer, the canvas
  // parser the queue already uses, rather than a second implementation.
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import ModelViewer from '$lib/components/ModelViewer.svelte';
  import GcodeViewer from '$lib/components/GcodeViewer.svelte';
  import { library, libraryAsset, previewFile, PREVIEWABLE,
           objectKeyFor, deleteLibraryObject } from '$lib/library.js';
  import { goto } from '$app/navigation';

  let materials = $state([]);
  let collections = $state([]);
  let addingPrint = $state(false);
  let printMaterial = $state('');
  let printOk = $state(true);
  let printNotes = $state('');
  let addTo = $state('');

  let model = $state(null);
  let error = $state(null);
  let loading = $state(true);
  let selected = $state(null);   // the file being previewed
  let sending = $state(null);    // file id currently being queued
  let notice = $state(null);
  let busy = $state(null);

  const id = $derived($page.params.id);
  const isGcode = $derived(selected && (selected.file_type === 'gcode' || selected.file_type === 'bgcode'));

  async function load() {
    loading = true;
    try {
      model = await library.model(id);
      selected = previewFile(model);
      error = null;
      // Side lists for the print form and the collection picker. A failure in
      // either must not stop the model rendering, which is the point of the
      // page.
      library.materials().then((m) => { materials = m || []; }).catch(() => {});
      library.projects.list().then((c) => { collections = c || []; }).catch(() => {});
    } catch (e) {
      error = e.message;
      model = null;
    } finally {
      loading = false;
    }
  }

  async function send(f) {
    sending = f.id;
    notice = null;
    try {
      const r = await library.files.sendToPrinter(f.id);
      // print_started reflects whether the job actually STARTED, which it does
      // not: the queue stages it. Saying "printing" here would be a lie the
      // person acts on by walking to the printer.
      notice = r?.print_started ? `${f.filename} started printing.` : `${f.filename} added to the queue.`;
    } catch (e) {
      notice = `Could not queue ${f.filename}: ${e.message}`;
    } finally {
      sending = null;
    }
  }

  function fmtSize(n) {
    if (!n && n !== 0) return '';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
  }

  const queueable = (f) => ['gcode', 'bgcode', '3mf'].includes(f.file_type);
  const sliceable = (f) => ['stl', '3mf', 'step', 'obj', '3ds', 'amf'].includes(f.file_type);

  // A running session cannot be handed a file: the container environment is
  // fixed at creation, so the slicer page has to start the session itself.
  function openInSlicer(f) {
    const key = objectKeyFor(f);
    if (!key) { notice = 'That file has no object key, so it cannot be opened in the slicer.'; return; }
    goto('/app/slicer?' + new URLSearchParams({ key, name: f.filename }).toString());
  }

  async function removeFile(f) {
    if (!confirm(`Delete ${f.filename} from your storage? This cannot be undone.`)) return;
    busy = f.id;
    try {
      await deleteLibraryObject(objectKeyFor(f));
      notice = `Deleted ${f.filename}.`;
      await load();
    } catch (e) {
      notice = `Could not delete ${f.filename}: ${e.message}`;
    } finally {
      busy = null;
    }
  }

  async function rename() {
    const name = prompt('Model name', model.name);
    if (!name || name === model.name) return;
    try {
      await library.updateModel(model.id, { name });
      await load();
    } catch (e) {
      notice = `Could not rename: ${e.message}`;
    }
  }

  async function removeModel() {
    if (!confirm(`Remove "${model.name}" from the library index? The files stay in your storage.`)) return;
    try {
      // deleteDisk is deliberately false: the library's bucket mount is
      // read-only, so asking it to unlink fails, and the object store is where
      // a real delete has to happen. This removes the index entry only.
      await library.deleteModel(model.id, false);
      goto('/app/files');
    } catch (e) {
      notice = `Could not remove the model: ${e.message}`;
    }
  }

  async function addPrint() {
    try {
      await library.prints.add(model.id, {
        material_id: printMaterial ? Number(printMaterial) : null,
        successful: printOk,
        notes: printNotes
      });
      addingPrint = false; printNotes = ''; printMaterial = '';
      await load();
    } catch (e) {
      notice = `Could not record the print: ${e.message}`;
    }
  }

  async function removePrint(pid) {
    try {
      await library.prints.remove(pid);
      await load();
    } catch (e) {
      notice = `Could not remove that entry: ${e.message}`;
    }
  }

  async function addToCollection() {
    if (!addTo) return;
    try {
      await library.projects.addModel(Number(addTo), model.id);
      const c = collections.find((x) => String(x.id) === String(addTo));
      notice = `Added to ${c ? c.name : 'the collection'}.`;
      addTo = '';
    } catch (e) {
      notice = `Could not add to that collection: ${e.message}`;
    }
  }

  function fmtDate(v) {
    if (!v) return '';
    const d = new Date(v);
    return isNaN(d) ? String(v) : d.toLocaleString();
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
  <div class="layout">
    <div class="preview">
      {#if selected && PREVIEWABLE.has(selected.file_type)}
        {#if isGcode}
          {#key selected.id}
            <GcodeViewer url={libraryAsset(selected.url)} name={selected.filename} />
          {/key}
        {:else}
          {#key selected.id}
            <ModelViewer
              url={libraryAsset(selected.url)}
              fileType={selected.file_type}
              fallbackThumb={libraryAsset(model.thumbnail)}
            />
          {/key}
        {/if}
        <p class="previewing muted">Previewing {selected.filename}</p>
      {:else if model.thumbnail}
        <img class="hero" src={libraryAsset(model.thumbnail)} alt="" />
      {:else}
        <div class="nopreview muted">No previewable file.</div>
      {/if}
    </div>

    <div class="info">
      <h2>{model.name}</h2>
      <div class="chips">
        {#if model.category_name}<span class="chip">{model.category_name}</span>{/if}
        {#each model.tags || [] as t (t.id ?? t.name)}<span class="chip">{t.name ?? t}</span>{/each}
      </div>
      {#if model.description}<p class="desc">{model.description}</p>{/if}
      {#if model.source_url}
        <p><a href={model.source_url} target="_blank" rel="noreferrer noopener">Source</a></p>
      {/if}
      {#if model.print_tips}
        <h3>Print tips</h3>
        <p class="desc">{model.print_tips}</p>
      {/if}
      <div class="modelActions">
        <button class="act" onclick={rename}>Rename</button>
        <button class="act danger" onclick={removeModel}>Remove from library</button>
      </div>
    </div>
  </div>

  {#if notice}<p class="notice">{notice}</p>{/if}

  <section>
    <h3>Files</h3>
    {#if (model.files || []).length === 0}
      <p class="muted">No files.</p>
    {:else}
      <ul class="files">
        {#each model.files as f (f.id)}
          <li class:active={selected?.id === f.id}>
            <span class="ftype">{f.file_type}</span>
            {#if PREVIEWABLE.has(f.file_type)}
              <button class="fname link" onclick={() => (selected = f)} title="Preview">{f.filename}</button>
            {:else}
              <span class="fname" title={f.filename}>{f.filename}</span>
            {/if}
            <span class="fsize muted">{fmtSize(f.file_size)}</span>
            {#if sliceable(f)}
              <button class="act" onclick={() => openInSlicer(f)}>Open in slicer</button>
            {/if}
            {#if queueable(f)}
              <button class="act" onclick={() => send(f)} disabled={sending === f.id}>
                {sending === f.id ? 'Queueing...' : 'Send to queue'}
              </button>
            {/if}
            <a class="act" href={libraryAsset(f.url)} download={f.filename}>Download</a>
            <button class="act danger" onclick={() => removeFile(f)} disabled={busy === f.id}>
              {busy === f.id ? 'Deleting...' : 'Delete'}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  {#if collections.length > 0}
    <section>
      <h3>Collections</h3>
      <div class="inline">
        <select bind:value={addTo}>
          <option value="">Choose a collection</option>
          {#each collections as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
        </select>
        <button class="act" onclick={addToCollection} disabled={!addTo}>Add this model</button>
      </div>
    </section>
  {/if}

  <section>
    <h3>Print log</h3>
    {#if addingPrint}
      <div class="inline">
        <select bind:value={printMaterial}>
          <option value="">No material</option>
          {#each materials as m (m.id)}<option value={m.id}>{m.name}</option>{/each}
        </select>
        <label class="chk"><input type="checkbox" bind:checked={printOk} /> Successful</label>
        <input class="notes" placeholder="Notes (optional)" bind:value={printNotes} />
        <button class="act" onclick={addPrint}>Save</button>
        <button class="act" onclick={() => (addingPrint = false)}>Cancel</button>
      </div>
    {:else}
      <button class="act" onclick={() => (addingPrint = true)}>Record a print</button>
    {/if}

    {#if (model.prints || []).length === 0}
      <p class="muted">Nothing recorded yet.</p>
    {:else}
      <ul class="prints">
        {#each model.prints as p (p.id)}
          <li>
            <span class="pstate" class:bad={!p.successful}>{p.successful ? 'ok' : 'failed'}</span>
            <span>{fmtDate(p.printed_at)}</span>
            {#if p.material_name}<span class="muted">{p.material_name}</span>{/if}
            <span class="pnotes muted">{p.notes ?? ''}</span>
            <button class="act" onclick={() => removePrint(p.id)}>Remove</button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}

<style>
  .back { margin: 0 0 0.8rem; }
  .layout { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 1.4rem; margin-bottom: 1.4rem; }
  @media (max-width: 780px) { .layout { grid-template-columns: 1fr; } }

  .previewing { font-size: 0.78rem; margin: 0.4rem 0 0; }
  .hero, .nopreview {
    width: 100%; aspect-ratio: 4 / 3; object-fit: contain;
    border: 1px solid var(--ophq-border); border-radius: 12px; background: var(--ophq-bg-2);
  }
  .nopreview { display: grid; place-items: center; font-size: 0.85rem; }

  h2 { margin: 0 0 0.5rem; }
  h3 { margin: 1rem 0 0.5rem; font-size: 1rem; }
  section { margin-bottom: 1.4rem; }
  .desc { color: var(--ophq-text-2); white-space: pre-wrap; }
  .chips { margin-bottom: 0.5rem; }
  .chip {
    display: inline-block; margin: 0 0.35rem 0.35rem 0; padding: 0.12rem 0.5rem;
    border: 1px solid var(--ophq-border); border-radius: 999px;
    font-size: 0.75rem; color: var(--ophq-text-2);
  }

  .files, .prints { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.35rem; }
  .files li, .prints li {
    display: flex; gap: 0.7rem; align-items: center;
    padding: 0.5rem 0.7rem;
    border: 1px solid var(--ophq-border); border-radius: 8px; background: var(--ophq-surface);
  }
  .files li.active { border-color: var(--ophq-primary); }
  .ftype {
    text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em;
    color: var(--ophq-primary); min-width: 3.2rem;
  }
  .fname { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left; }
  .link { background: none; border: 0; color: inherit; cursor: pointer; padding: 0; font: inherit; text-decoration: underline; }
  .fsize { font-size: 0.8rem; }
  .act {
    white-space: nowrap; font-size: 0.8rem;
    padding: 0.25rem 0.6rem; border: 1px solid var(--ophq-border); border-radius: 6px;
    background: var(--ophq-surface); color: var(--ophq-text); cursor: pointer; text-decoration: none;
  }
  .act:disabled { opacity: 0.5; cursor: default; }
  .act.danger { color: var(--ophq-danger); border-color: color-mix(in srgb, var(--ophq-danger) 40%, var(--ophq-border)); }
  .modelActions { display: flex; gap: 0.5rem; margin-top: 0.8rem; flex-wrap: wrap; }
  .inline { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; margin-bottom: 0.6rem; }
  .inline select, .notes {
    padding: 0.35rem 0.6rem; border: 1px solid var(--ophq-border); border-radius: 8px;
    background: var(--ophq-surface); color: var(--ophq-text); font: inherit; font-size: 0.85rem;
  }
  .notes { flex: 1 1 12rem; }
  .chk { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.85rem; color: var(--ophq-text-2); }
  .pstate {
    text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em;
    color: var(--ophq-success); min-width: 3.2rem;
  }
  .pstate.bad { color: var(--ophq-danger); }
  .pnotes { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .notice { color: var(--ophq-text-2); }
  .muted { color: var(--ophq-muted); }
  .warn { color: var(--ophq-warn); }
</style>
