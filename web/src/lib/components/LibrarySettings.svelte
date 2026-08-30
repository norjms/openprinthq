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
  import { library } from '$lib/library.js';

  let categories = $state([]);
  let tags = $state([]);
  let materials = $state([]);
  let loading = $state(true);
  let error = $state(null);
  let notice = $state(null);
  let busy = $state(false);

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
      notice = d.duplicatesCount
        ? `${d.duplicatesCount} duplicate file${d.duplicatesCount === 1 ? '' : 's'} across ${d.groups.length} group${d.groups.length === 1 ? '' : 's'}.`
        : 'No duplicates found.';
    } catch (e) {
      notice = e.message;
    } finally {
      busy = false;
    }
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
    in the bucket show up as a model.
  </p>
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
  .notice { color: var(--ophq-text-2); }
  .muted { color: var(--ophq-muted); }
  .warn { color: var(--ophq-warn); }
</style>
