<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import Logo from '$lib/components/Logo.svelte';
  // SSO (Authentik) is the only sign-in path; /app triggers the login flow.
  onMount(() => { const t = setTimeout(() => goto('/app'), 1200); return () => clearTimeout(t); });
</script>

<svelte:head><title>Sign in · OpenPrintHQ</title></svelte:head>

<div class="auth">
  <div class="card card-pad box">
    <div class="lg"><Logo size={34} /></div>
    <h2>Sign in</h2>
    <p class="muted">Redirecting you to secure sign-in…</p>
    <a class="btn btn-primary full" href="/app">Continue with SSO</a>
  </div>
</div>

<style>
  .auth { min-height: 100vh; display: grid; place-items: center; padding: 2rem; }
  .box { width: 100%; max-width: 380px; text-align: center; }
  .lg { margin-bottom: 1rem; display: flex; justify-content: center; }
  .full { width: 100%; justify-content: center; margin-top: 1rem; }
</style>
