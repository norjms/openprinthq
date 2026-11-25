<script>
  /** @type {{ tone?: 'neutral'|'ok'|'warn'|'bad'|'live', children?: import('svelte').Snippet }} */
  let { tone = 'neutral', children } = $props();
</script>

<span class="ah-chip{tone === 'neutral' ? '' : ` ah-chip--${tone}`}">{@render children?.()}</span>
