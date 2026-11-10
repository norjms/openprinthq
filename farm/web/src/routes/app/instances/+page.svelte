<script>
  import { base as basePath } from '$app/paths';
  // The instances admin console moved under Settings → Global Admin.
  // Keep this route so old links/bookmarks redirect instead of 404ing.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  onMount(() => goto(basePath + '/app/settings'));
</script>

<p class="muted">Instance management moved to <a href="{basePath}/app/settings">Settings → Global Admin</a>. Redirecting…</p>
