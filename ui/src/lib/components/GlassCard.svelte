<script>
  /** @type {{ as?: string, scroll?: boolean, class?: string, children?: import('svelte').Snippet, [key: string]: unknown }} */
  let { as = 'section', scroll = false, class: extra = '', children, ...rest } = $props();
</script>

<svelte:element this={as} class="ah-glass ah-card{scroll ? ' ah-scroll' : ''} {extra}" {...rest}>{@render children?.()}</svelte:element>
