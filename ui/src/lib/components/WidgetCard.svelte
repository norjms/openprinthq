<script>
  import Icon from './Icon.svelte';
  /** @type {{ icon: string, title: string, subtitle?: string, href?: string, children?: import('svelte').Snippet }} */
  let { icon, title, subtitle = '', href, children } = $props();
</script>

<section class="ah-glass ah-card">
  <div style="display:flex;align-items:center;gap:10px;min-width:0">
    <div class="ah-icon-tile"><Icon name={icon} /></div>
    <div style="min-width:0">
      <h3 class="ah-widget-title">{title}</h3>
      {#if subtitle}<div class="ah-muted">{subtitle}</div>{/if}
    </div>
    {#if href}<a class="ah-round-btn" {href} aria-label="Open {title}"><Icon name="chevron-right" size={14} /></a>{/if}
  </div>
  {@render children?.()}
</section>
