<script>
  import { base as basePath } from '$app/paths';
  import { onMount } from 'svelte';
  onMount(async () => {
    // Clear the app session cookie, then end the Authentik SSO session via the
    // embedded outpost sign-out so the user is fully logged out (not just the app).
    try { await fetch(basePath + '/api/auth/logout', { method: 'POST', credentials: 'include' }); } catch {}
    window.location.href = basePath + '/outpost.goauthentik.io/sign_out?rd=' + encodeURIComponent(location.origin + '/');
  });
</script>
<div style="min-height:100vh;display:grid;place-items:center" class="muted">Signing out…</div>
