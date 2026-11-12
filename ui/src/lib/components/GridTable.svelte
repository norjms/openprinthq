<script>
  /** Columns: { key, label, width } where width is a CSS grid track such as "minmax(0,1fr)" or "96px". */
  /** @type {{ columns: { key: string, label: string, width?: string }[], rows: Record<string, unknown>[], label: string, cell?: import('svelte').Snippet<[Record<string, unknown>, string]> }} */
  let { columns, rows, label, cell } = $props();
  const tracks = $derived(columns.map((c) => c.width ?? 'minmax(0,1fr)').join(' '));
</script>

<div class="ah-table" role="table" aria-label={label} style="grid-template-columns:{tracks}">
  <div role="row">{#each columns as c (c.key)}<div role="columnheader">{c.label}</div>{/each}</div>
  {#each rows as r, i (i)}
    <div role="row">{#each columns as c (c.key)}<div role="cell">{#if cell}{@render cell(r, c.key)}{:else}{r[c.key] ?? ''}{/if}</div>{/each}</div>
  {/each}
</div>
