<script>
  // The farm's Cameras grid is gone: OpenPrintHQ has one Cameras page, the hub's (Farm, Cameras), and the
  // sidebar's Cameras goes there. Old links land on it, and old links to the Timelapses tab on the farm's
  // Timelapses page.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { base as basePath } from '$app/paths';

  onMount(() => {
    let timelapses = false;
    try { timelapses = new URLSearchParams(window.location.search).get('tab') === 'timelapses'; } catch { /* */ }
    if (timelapses) goto(basePath + '/app/timelapses', { replaceState: true });
    else window.location.replace('/farm?view=cameras');
  });
</script>

<p class="muted">Opening the hub's Cameras page.</p>
