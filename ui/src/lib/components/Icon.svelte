<script>
  import { ICONS } from '../icons.js';
  /** @type {{ name: keyof typeof ICONS, size?: number, label?: string }} */
  let { name, size = 18, label } = $props();
</script>

<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
  stroke-linecap="round" stroke-linejoin="round" role={label ? 'img' : undefined} aria-label={label}
  aria-hidden={label ? undefined : 'true'}>{@html ICONS[name]}</svg>
