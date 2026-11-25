<script>
  import { base as basePath } from '$app/paths';
  // "Search or jump to": a small palette over the navigation and the printers.
  // Opens from the sidebar's search button or with Ctrl K.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { goto } from '$app/navigation';
  import Icon from '$lib/components/AppIcon.svelte';
  import { api } from '$lib/api';

  /** @type {{ open: boolean, items: { href: string, label: string, icon: string, external?: boolean, reload?: boolean }[], onclose: () => void }} */
  let { open, items, onclose } = $props();

  let q = $state('');
  let printers = $state([]);
  let input = $state(null);
  let sel = $state(0);

  $effect(() => {
    if (!open) return;
    q = ''; sel = 0;
    queueMicrotask(() => input?.focus());
    api.printers()
      .then((d) => { printers = (Array.isArray(d) ? d : d?.printers || d?.items || []).map((p) => ({ href: `${basePath}/app/printers/${p.id}`, label: p.name || `Printer ${p.id}`, icon: 'printer', sub: p.model || '' })); })
      .catch(() => { printers = []; });
  });

  const results = $derived.by(() => {
    const all = [...items.map((i) => ({ ...i, sub: i.group || '' })), ...printers];
    const t = q.trim().toLowerCase();
    return (t ? all.filter((i) => i.label.toLowerCase().includes(t) || (i.sub || '').toLowerCase().includes(t)) : all).slice(0, 12);
  });

  function go(r) {
    onclose();
    if (r.external) window.open(r.href, '_blank', 'noopener');
    else if (r.reload) window.location.href = r.href;   // a hub page, outside the farm
    else goto(r.href);
  }
  function onkey(e) {
    if (e.key === 'Escape') { e.preventDefault(); onclose(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, results.length - 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); }
    else if (e.key === 'Enter' && results[sel]) { e.preventDefault(); go(results[sel]); }
  }
</script>

{#if open}
  <div class="jt-backdrop" role="presentation" onclick={onclose}></div>
  <div class="jt ah-glass" role="dialog" aria-modal="true" aria-label="Search or jump to">
    <div class="jt-search">
      <Icon name="search" size={18} />
      <label class="sr-only" for="jt-q">Find a page or a printer</label>
      <input id="jt-q" bind:this={input} bind:value={q} onkeydown={onkey} placeholder="Find a page or a printer" autocomplete="off" />
    </div>
    <ul class="jt-list" role="listbox" aria-label="Results">
      {#each results as r, i (r.href)}
        <li role="option" aria-selected={i === sel}>
          <button type="button" class:on={i === sel} onclick={() => go(r)} onmouseenter={() => (sel = i)}>
            <Icon name={r.icon} size={17} />
            <span class="jt-label">{r.label}</span>
            {#if r.sub}<span class="ah-muted">{r.sub}</span>{/if}
          </button>
        </li>
      {:else}
        <li class="jt-empty ah-muted">Nothing matches. Try another word.</li>
      {/each}
    </ul>
  </div>
{/if}

<style>
  .jt-backdrop { position: fixed; inset: 0; z-index: 90; background: rgba(12, 20, 26, 0.45); }
  .jt { position: fixed; z-index: 91; top: 12vh; left: 50%; transform: translateX(-50%); width: min(560px, calc(100vw - 32px)); max-height: 70vh; display: flex; flex-direction: column; overflow: hidden; background: var(--ah-solid-panel); }
  .jt-search { display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-bottom: 1px solid var(--ah-row-line); color: var(--ah-text-2); }
  .jt-search input { flex: 1; height: 40px; border: 0; background: transparent; color: var(--ah-text); font: 16px var(--ah-font-sans); outline: none; }
  .jt-list { list-style: none; margin: 0; padding: 8px; overflow: auto; }
  .jt-list button { width: 100%; display: flex; align-items: center; gap: 10px; padding: 8px 12px; border: 0; border-radius: 12px; background: transparent; color: var(--ah-text); font: 14px var(--ah-font-sans); cursor: pointer; text-align: left; }
  .jt-list button.on { background: var(--ah-selected); color: var(--ah-text-on-selected); }
  .jt-list button.on .ah-muted { color: var(--ah-text-on-selected); }
  .jt-label { flex: 1; min-width: 0; }
  .jt-empty { padding: 12px; }
</style>
