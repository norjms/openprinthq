<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  onMount(async () => {
    try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }); } catch {}
    goto('/');
  });
</script>
<div style="min-height:100vh;display:grid;place-items:center" class="muted">Signing out…</div>
