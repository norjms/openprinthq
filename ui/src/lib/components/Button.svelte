<script>
  /** @type {{ variant?: 'primary'|'secondary'|'ghost'|'danger', size?: 'md'|'sm', href?: string, type?: 'button'|'submit', disabled?: boolean, onclick?: (e: MouseEvent) => void, children?: import('svelte').Snippet, [key: string]: unknown }} */
  let { variant = 'secondary', size = 'md', href, type = 'button', disabled = false, onclick, children, ...rest } = $props();
  const cls = $derived(`ah-btn ah-btn--${variant}${size === 'sm' ? ' ah-btn--sm' : ''}`);
</script>

{#if href}
  <a class={cls} {href} {...rest}>{@render children?.()}</a>
{:else}
  <button class={cls} {type} {disabled} {onclick} {...rest}>{@render children?.()}</button>
{/if}
