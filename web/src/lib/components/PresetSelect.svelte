<script>
  // OpenPrintHQ — searchable preset combobox.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  // Native <select> is unusable for OrcaSlicer's preset lists (the filament
  // list alone is ~1000 entries). This is a type-to-filter dropdown; options
  // whose label contains `priority` (e.g. the chosen printer's model+nozzle)
  // are floated to the top so the compatible ones surface first.
  let { options = [], value = $bindable(), placeholder = 'Search…', priority = '', id = undefined } = $props();

  let open = $state(false);
  let filter = $state('');
  let wrap;

  const selected = $derived(options.find((o) => o.value === value) || null);

  const shown = $derived.by(() => {
    const q = filter.trim().toLowerCase();
    let list = q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options.slice();
    const p = (priority || '').trim().toLowerCase();
    if (p) {
      list = list.slice().sort((a, b) => {
        const am = a.label.toLowerCase().includes(p) ? 0 : 1;
        const bm = b.label.toLowerCase().includes(p) ? 0 : 1;
        return am - bm;
      });
    }
    return list.slice(0, 200); // cap render for perf; filter to narrow further
  });

  function pick(o) { value = o.value; open = false; filter = ''; }
  function openMenu() { open = true; filter = ''; }
  function onWindowPointer(e) { if (wrap && !wrap.contains(e.target)) open = false; }
</script>

<svelte:window onclick={onWindowPointer} />

<div class="ps" bind:this={wrap}>
  <input
    {id}
    class="input"
    type="text"
    autocomplete="off"
    placeholder={placeholder}
    value={open ? filter : (selected?.label ?? '')}
    onfocus={openMenu}
    oninput={(e) => { open = true; filter = e.target.value; }}
  />
  <span class="caret" aria-hidden="true">▾</span>
  {#if open}
    <div class="menu">
      {#if shown.length === 0}
        <div class="none">No matches</div>
      {:else}
        {#each shown as o (o.value)}
          <button type="button" class="opt" class:sel={o.value === value} onmousedown={() => pick(o)}>{o.label}</button>
        {/each}
        {#if !filter && options.length > shown.length}
          <div class="more">Type to search all {options.length}…</div>
        {/if}
      {/if}
    </div>
  {/if}
</div>

<style>
  .ps { position: relative; }
  .ps .input { padding-right: 2rem; cursor: text; }
  .caret { position: absolute; right: 0.7rem; top: 50%; transform: translateY(-50%); color: var(--ophq-muted); font-size: 0.7rem; pointer-events: none; }
  .menu {
    position: absolute; z-index: 60; top: calc(100% + 4px); left: 0; right: 0;
    max-height: 260px; overflow-y: auto;
    background: var(--ophq-surface-2); border: 1px solid var(--ophq-border);
    border-radius: var(--radius-sm); box-shadow: var(--shadow); padding: 0.3rem;
  }
  .opt {
    display: block; width: 100%; text-align: left; background: transparent; border: 0;
    color: var(--ophq-text-2); padding: 0.45rem 0.6rem; border-radius: 6px;
    font-size: 0.9rem; cursor: pointer; font-family: var(--font-ui);
  }
  .opt:hover { background: var(--ophq-surface-3); color: var(--ophq-text); }
  .opt.sel { color: var(--ophq-primary-2); background: var(--ophq-primary-dim); }
  .none, .more { padding: 0.5rem 0.6rem; color: var(--ophq-muted); font-size: 0.83rem; }
</style>
