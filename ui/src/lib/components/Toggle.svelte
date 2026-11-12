<script>
  /** @type {{ checked: boolean, label: string, disabled?: boolean, onchange?: (checked: boolean) => void }} */
  let { checked = $bindable(false), label, disabled = false, onchange } = $props();
  function flip() { checked = !checked; onchange?.(checked); }
</script>

<button type="button" class="ah-toggle" role="switch" aria-checked={checked} aria-label={label} {disabled} onclick={flip}></button>
