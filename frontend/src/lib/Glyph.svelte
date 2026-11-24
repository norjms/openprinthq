<script>
  // Stroke icons the shared set (openprinthq-ui Icon) does not have yet, drawn the same way: 24 grid, stroke 1.7.
  import { Icon, ICONS } from 'openprinthq-ui';
  const MORE = {
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    'arrow-left': '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    'arrow-right': '<path d="M5 12h14M12 5l7 7-7 7"/>',
    'arrow-up': '<path d="M12 19V5M5 12l7-7 7 7"/>',
    'arrow-down': '<path d="M12 5v14M19 12l-7 7-7-7"/>',
    minus: '<path d="M5 12h14"/>',
    star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3 6.4 20.2l1.1-6.2L3 9.6l6.2-.9z"/>',
    wrench: '<path d="M14.7 6.3a4 4 0 0 0 5 5L21 13l-8 8-3-3 6.7-6.7a4 4 0 0 1-5-5L13 5z"/><path d="M3 21l6-6"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    phone: '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    collapse: '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>'
  };
  /** @type {{ name: string, size?: number, label?: string, fill?: boolean }} */
  let { name, size = 16, label, fill = false } = $props();
</script>

{#if ICONS[name]}<Icon {name} {size} {label} />{:else}
<svg width={size} height={size} viewBox="0 0 24 24" fill={fill ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="1.7"
  stroke-linecap="round" stroke-linejoin="round" role={label ? 'img' : undefined} aria-label={label}
  aria-hidden={label ? undefined : 'true'} style="flex:none;vertical-align:-0.15em">{@html MORE[name] || ''}</svg>{/if}
