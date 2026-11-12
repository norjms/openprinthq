<script>
  /** @type {{ id: string, label: string, help?: string, error?: string, children?: import('svelte').Snippet }} */
  let { id, label, help = '', error = '', children } = $props();
</script>

<div class="ah-field">
  <label for={id}>{label}</label>
  {@render children?.()}
  {#if error}<span class="ah-field-error" id="{id}-error">{error}</span>{:else if help}<span class="ah-field-help" id="{id}-help">{help}</span>{/if}
</div>
