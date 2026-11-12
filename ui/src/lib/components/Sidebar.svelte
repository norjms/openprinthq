<script>
  import Icon from './Icon.svelte';
  /** Nav groups: [{ label, items: [{ label, href, icon, badge?, current? }] }]. One list feeds the hub, the Farm and any app sharing the sidebar. */
  /** @type {{ product: string, homeHref?: string, groups: { label: string, items: { label: string, href: string, icon: string, badge?: string|number, current?: boolean }[] }[], onsearch?: () => void, children?: import('svelte').Snippet }} */
  let { product, homeHref = '/', groups, onsearch, children } = $props();
</script>

<aside class="ah-glass ah-sidebar">
  <a class="ah-sidebar__brand" href={homeHref}><span class="ah-icon-tile" style="background:var(--ah-accent);color:var(--ah-on-accent)"><Icon name="cube" /></span>{product}</a>
  <button type="button" class="ah-input" style="display:flex;align-items:center;gap:8px;color:var(--ah-text-3);font-size:13px;cursor:pointer;text-align:left" onclick={onsearch}>
    <Icon name="search" size={16} />Search or jump to<span class="ah-muted" style="margin-left:auto">Ctrl K</span>
  </button>
  <nav class="ah-sidebar__nav" aria-label="{product} navigation">
    {#each groups as g (g.label)}
      <div class="ah-group-label">{g.label}</div>
      {#each g.items as it (it.href)}
        <a class="ah-nav" href={it.href} aria-current={it.current ? 'page' : undefined}><Icon name={it.icon} size={17} />{it.label}{#if it.badge != null}<span class="ah-badge">{it.badge}</span>{/if}</a>
      {/each}
    {/each}
  </nav>
  {@render children?.()}
</aside>
