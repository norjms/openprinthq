<script>
  // The title row of an app page: the page title, one line about it, and the
  // page's own controls on the right.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  /** @type {{ title: string, description?: string, eyebrow?: string, actions?: import('svelte').Snippet, children?: import('svelte').Snippet }} */
  let { title, description = '', eyebrow = '', actions, children } = $props();
</script>

<div class="ah-titlerow page-head">
  <div class="ph-text">
    {#if eyebrow}<div class="eyebrow">{eyebrow}</div>{/if}
    <h1 class="ah-title">{title}</h1>
    {#if description || children}<p class="ah-secondary ph-desc">{description}{@render children?.()}</p>{/if}
  </div>
  {#if actions}<div class="ph-actions">{@render actions()}</div>{/if}
</div>

<style>
  .page-head { flex: none; flex-wrap: wrap; row-gap: 10px; }
  .ph-text { flex: 1; min-width: 0; }
  .ph-text h1 { margin: 0; }
  .ph-desc { margin: 4px 0 0; font-size: 14px; }
  .ph-actions { display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; align-items: center; }
</style>
