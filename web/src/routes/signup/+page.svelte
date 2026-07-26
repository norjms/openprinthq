<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import Logo from '$lib/components/Logo.svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';
  onMount(() => { const t = setTimeout(() => goto('/app'), 1200); return () => clearTimeout(t); });
</script>

<PageTitle page="Launch your HQ" />

<div class="auth">
  <div class="card card-pad box">
    <div class="lg"><Logo size={34} /></div>
    <h2>Launch your HQ</h2>
    <p class="muted">Sign in with SSO and we provision your private instance automatically.</p>
    <a class="btn btn-primary full" href="/app">Continue with SSO</a>
    <ul class="perks">
      <li>✓ Your own isolated instance &amp; database</li>
      <li>✓ Bambu · Creality · Prusa · Snapmaker · Voron</li>
      <li>✓ Open source, AGPL-3.0</li>
    </ul>
  </div>
</div>

<style>
  .auth { min-height: 100vh; display: grid; place-items: center; padding: 2rem; }
  .box { width: 100%; max-width: 400px; text-align: center; }
  .lg { margin-bottom: 1rem; display: flex; justify-content: center; }
  .full { width: 100%; justify-content: center; margin-top: 0.6rem; }
  .perks { list-style: none; padding: 0; margin: 1.4rem 0 0; text-align: left; display: grid; gap: 0.4rem; color: var(--ophq-text-2); font-size: 0.9rem; }
</style>
