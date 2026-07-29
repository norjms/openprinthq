<script>
  // Timelapses moved under Cameras. Redirect old links to the Cameras tab.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  onMount(() => { goto('/app/cameras?tab=timelapses', { replaceState: true }); });
</script>

<div class="card card-pad muted">Redirecting to Cameras → Timelapses…</div>
