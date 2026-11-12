<script>
  // An icon from artisanhub-ui's set or this app's few extras, same drawing rules.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { ICONS } from '$lib/icons.js';
  /** @type {{ name: string, size?: number, label?: string }} */
  let { name, size = 18, label } = $props();
</script>

<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
  stroke-linecap="round" stroke-linejoin="round" role={label ? 'img' : undefined} aria-label={label}
  aria-hidden={label ? undefined : 'true'} style="flex:none">{@html ICONS[name] || ''}</svg>
