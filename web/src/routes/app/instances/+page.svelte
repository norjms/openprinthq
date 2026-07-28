<script>
  // The instances admin console moved under Settings → Global Admin.
  // Keep this route so old links/bookmarks redirect instead of 404ing.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  onMount(() => goto('/app/settings'));
</script>

<p class="muted">Instance management moved to <a href="/app/settings">Settings → Global Admin</a>. Redirecting…</p>
