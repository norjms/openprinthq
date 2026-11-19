<script>
  import Icon from './Icon.svelte';
  /** The OpenPrintHQ mark (a hexagon holding stacked print layers) on an accent tile, with the product name beside it
      unless wordmark is false. An installation that renamed itself passes its own name. */
  /** @type {{ size?: number, wordmark?: boolean, name?: string }} */
  let { size = 36, wordmark = true, name = 'OpenPrintHQ' } = $props();
</script>

<span class="ah-mark">
  <span class="ah-icon-tile ah-mark__tile" style="width:{size}px;height:{size}px" aria-hidden={wordmark ? 'true' : undefined}
    role={wordmark ? undefined : 'img'} aria-label={wordmark ? undefined : name}>
    <Icon name="mark" size={Math.round(size * 0.56)} />
  </span>
  {#if wordmark}<span class="ah-mark__name">{name}</span>{/if}
</span>
