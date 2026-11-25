<script>
  /** The desktop layout: 20% sidebar, 80% main with header, title row and content, and an optional widget column. */
  /** @type {{ sidebar: import('svelte').Snippet, header?: import('svelte').Snippet, title: string, description?: string, filters?: import('svelte').Snippet, widgets?: import('svelte').Snippet, children?: import('svelte').Snippet }} */
  let { sidebar, header, title, description = '', filters, widgets, children } = $props();
</script>

<div class="ah-root ah-page ah-shell">
  <div class="ah-shell__side">{@render sidebar()}</div>
  <main class="ah-shell__main">
    {#if header}<header class="ah-header">{@render header()}</header>{/if}
    <div class="ah-titlerow">
      <div style="flex:1;min-width:0"><h1 class="ah-title">{title}</h1>{#if description}<p class="ah-secondary" style="margin:4px 0 0">{description}</p>{/if}</div>
      {#if filters}<div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end">{@render filters()}</div>{/if}
    </div>
    <div class="ah-shell__body{widgets ? ' ah-shell__body--widgets' : ''}">
      <div class="ah-shell__content">{@render children?.()}</div>
      {#if widgets}<div class="ah-shell__widgets">{@render widgets()}</div>{/if}
    </div>
  </main>
</div>
