<script>
  // OpenPrintHQ - library settings: the vocabulary the library sorts by, plus
  // the two maintenance actions worth exposing.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // Deliberately NOT here: users, roles, sign-in, API keys, SMTP. The fork has
  // none of those any more. Identity comes from the edge and roles are
  // recomputed from groups on every request, so a screen offering to change
  // them here would be offering to change something that reverts.
  import { onMount } from 'svelte';
  import { library, objectKeyFor, deleteLibraryObject } from '$lib/library.js';

  let categories = $state([]);
  let tags = $state([]);
  let materials = $state([]);
  let loading = $state(true);
  let error = $state(null);
  let notice = $state(null);
  let busy = $state(false);
  let dupGroups = $state([]);

  let newCategory = $state('');
  let newCategoryColor = $state('#00d4ff');
  let newTag = $state('');
  let newMaterial = $state('');

  async function load() {
    loading = true;
    try {
      [categories, tags, materials] = await Promise.all([
        library.categories(), library.tags(), library.materials()
      ]);
      error = null;
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  async function act(fn, ok) {
    busy = true; notice = null;
    try {
      await fn();
      notice = ok;
      await load();
    } catch (e) {
      notice = e.message;
    } finally {
      busy = false;
    }
  }

  const addCategory = () => newCategory.trim() && act(
    () => library.settings.createCategory({ name: newCategory.trim(), color: newCategoryColor }),
    'Category added.'
  ).then(() => { newCategory = ''; });

  const addTag = () => newTag.trim() && act(
    () => library.settings.createTag({ name: newTag.trim() }), 'Tag added.'
  ).then(() => { newTag = ''; });

  const addMaterial = () => newMaterial.trim() && act(
    () => library.settings.createMaterial({ name: newMaterial.trim() }), 'Material added.'
  ).then(() => { newMaterial = ''; });

  async function rescan() {
    busy = true; notice = null;
    try {
      await library.scan();
      notice = 'Scan started. New files appear once it finishes.';
    } catch (e) {
      notice = e.message;
    } finally {
      busy = false;
    }
  }

  async function duplicates() {
    busy = true; notice = null;
    try {
      const d = await library.settings.duplicates();
      dupGroups = d.groups || [];
      // The count is the number of GROUPS, not of redundant files. What
      // matters when deciding whether to act is how many copies could go, so
      // say that instead.
      const redundant = dupGroups.reduce((n, g) => n + Math.max(0, (g.files || []).length - 1), 0);
      notice = dupGroups.length
        ? `${dupGroups.length} group${dupGroups.length === 1 ? '' : 's'} of identical files, ${redundant} redundant cop${redundant === 1 ? 'y' : 'ies'}.`
        : 'No duplicates found.';
    } catch (e) {
      notice = e.message;
    } finally {
      busy = false;
    }
  }

  // Identity is by SHA-256 of the contents, so any copy in a group is as good
  // as any other and deleting one is safe. The deletion goes to the object
  // store: the library's bucket mount is read-only and cannot unlink anything.
  async function dropCopy(group, file) {
    if (!confirm(`Delete this copy of ${file.original_name || file.filename}? The other ${(group.files || []).length - 1} stay.`)) return;
    busy = true; notice = null;
    try {
      await deleteLibraryObject(objectKeyFor({ library_path: file.library_path }));
      notice = 'Copy deleted.';
      await duplicates();
    } catch (e) {
      notice = `Could not delete that copy: ${e.message}`;
    } finally {
      busy = false;
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

{#if error}<p class="warn">{error}</p>{/if}
{#if notice}<p class="notice">{notice}</p>{/if}

<section>
  <h3>Maintenance</h3>
  <div class="row">
    <button class="act" onclick={rescan} disabled={busy}>Rescan library</button>
    <button class="act" onclick={duplicates} disabled={busy}>Find duplicates</button>
  </div>
  <p class="muted small">
    Files arrive in your storage from outside the library, so it indexes on a
    scan rather than on the write. A rescan is what makes a file that is already
    in the bucket show up as a model. Duplicates are matched on the contents of
    the file, not the name, so a renamed copy is still found.
  </p>

  {#if dupGroups.length > 0}
    <ul class="dups">
      {#each dupGroups as g (g.hash)}
        <li>
          <div class="dhead">
            <span class="muted">{fmtSize(g.size)} each</span>
            <span class="muted">{(g.files || []).length} copies</span>
          </div>
          {#each g.files || [] as f (f.id)}
            <div class="drow">
              <span class="dname" title={f.library_path}>{f.original_name || f.filename}</span>
              <span class="muted">{f.model_name || 'no model'}</span>
              <button class="act" onclick={() => dropCopy(g, f)} disabled={busy}>Delete this copy</button>
            </div>
          {/each}
        </li>
      {/each}
    </ul>
  {/if}
</section>

{#if loading}
  <p class="muted">Loading...</p>
{:else}
  <section>
    <h3>Categories</h3>
    <div class="row">
      <input placeholder="New category" bind:value={newCategory} />
      <input class="color" type="color" bind:value={newCategoryColor} aria-label="Category colour" />
      <button class="act" onclick={addCategory} disabled={busy || !newCategory.trim()}>Add</button>
    </div>
    <ul class="chips">
      {#each categories as c (c.id)}
        <li>
          <span class="dot" style={`background:${c.color}`}></span>
          {c.name}
          <span class="muted">{c.model_count}</span>
          <button class="x" onclick={() => act(() => library.settings.deleteCategory(c.id), 'Category removed.')} disabled={busy} aria-label={`Delete ${c.name}`}>x</button>
        </li>
      {/each}
    </ul>
  </section>

  <section>
    <h3>Tags</h3>
    <div class="row">
      <input placeholder="New tag" bind:value={newTag} />
      <button class="act" onclick={addTag} disabled={busy || !newTag.trim()}>Add</button>
    </div>
    {#if tags.length === 0}
      <p class="muted small">No tags yet.</p>
    {:else}
      <ul class="chips">
        {#each tags as t (t.id)}
          <li>
            {t.name}
            <button class="x" onclick={() => act(() => library.settings.deleteTag(t.id), 'Tag removed.')} disabled={busy} aria-label={`Delete ${t.name}`}>x</button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section>
    <h3>Materials</h3>
    <div class="row">
      <input placeholder="New material" bind:value={newMaterial} />
      <button class="act" onclick={addMaterial} disabled={busy || !newMaterial.trim()}>Add</button>
    </div>
    <ul class="chips">
      {#each materials as m (m.id)}
        <li>
          {m.name}
          {#if !m.is_preset}
            <button class="x" onclick={() => act(() => library.settings.deleteMaterial(m.id), 'Material removed.')} disabled={busy} aria-label={`Delete ${m.name}`}>x</button>
          {/if}
        </li>
      {/each}
    </ul>
  </section>
{/if}

<style>
  section { margin-bottom: 1.6rem; }
  h3 { margin: 0 0 0.5rem; font-size: 1rem; }
  .row { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; margin-bottom: 0.6rem; }
  .row input {
    padding: 0.45rem 0.7rem; border: 1px solid var(--ophq-border); border-radius: 8px;
    background: var(--ophq-surface); color: var(--ophq-text); flex: 0 1 14rem;
  }
  .color { flex: 0 0 3rem; padding: 0.15rem; height: 2.2rem; }
  .act {
    padding: 0.35rem 0.8rem; border: 1px solid var(--ophq-border); border-radius: 8px;
    background: var(--ophq-surface); color: var(--ophq-text); cursor: pointer; font: inherit; font-size: 0.85rem;
  }
  .act:disabled { opacity: 0.5; cursor: default; }
  .chips { list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 0.4rem; }
  .chips li {
    display: inline-flex; align-items: center; gap: 0.4rem;
    padding: 0.2rem 0.55rem;
    border: 1px solid var(--ophq-border); border-radius: 999px;
    font-size: 0.82rem;
  }
  .dot { width: 0.6rem; height: 0.6rem; border-radius: 50%; display: inline-block; }
  .x { background: none; border: 0; color: var(--ophq-muted); cursor: pointer; font: inherit; padding: 0; }
  .x:hover { color: var(--ophq-danger); }
  .small { font-size: 0.82rem; }
  .dups { list-style: none; padding: 0; margin: 0.8rem 0 0; display: grid; gap: 0.6rem; }
  .dups li { border: 1px solid var(--ophq-border); border-radius: 8px; padding: 0.5rem 0.7rem; }
  .dhead { display: flex; gap: 0.8rem; font-size: 0.78rem; margin-bottom: 0.35rem; }
  .drow { display: flex; gap: 0.7rem; align-items: center; padding: 0.25rem 0; font-size: 0.85rem; }
  .dname { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .notice { color: var(--ophq-text-2); }
  .muted { color: var(--ophq-muted); }
  .warn { color: var(--ophq-warn); }
</style>
