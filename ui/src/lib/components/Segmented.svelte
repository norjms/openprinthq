<script>
  /** @type {{ options: { value: string, label: string }[], value: string, label: string, onchange?: (value: string) => void }} */
  let { options, value = $bindable(), label, onchange } = $props();
  function pick(v) { value = v; onchange?.(v); }
</script>

<div class="ah-seg" role="group" aria-label={label}>
  {#each options as o (o.value)}
    <button type="button" aria-pressed={o.value === value} onclick={() => pick(o.value)}>{o.label}</button>
  {/each}
</div>
