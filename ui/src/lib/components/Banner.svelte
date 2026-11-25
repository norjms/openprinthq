<script>
  /** @type {{ tone?: 'attention'|'bad'|'live', children?: import('svelte').Snippet }} */
  let { tone = 'attention', children } = $props();
</script>

<div class="ah-banner{tone === 'attention' ? '' : ` ah-banner--${tone}`}" role={tone === 'bad' ? 'alert' : 'status'}>{@render children?.()}</div>
