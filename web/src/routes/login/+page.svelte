<script>
  import { onMount } from 'svelte';
  import AuthCard from '$lib/components/AuthCard.svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import { branding } from '$lib/stores/appearance';
  // Single sign-on is the only way in. Use a FULL page load (not client-side
  // navigation) so the reverse proxy's forward-auth can redirect to the sign-in.
  onMount(() => { const t = setTimeout(() => { window.location.href = '/app'; }, 900); return () => clearTimeout(t); });
</script>

<PageTitle page="Sign in" />

<AuthCard title="Sign in">
  <p class="ah-secondary">{$branding.tagline}</p>
  <p class="ah-muted">Taking you to secure sign-in.</p>
  <a class="btn btn-primary full" href="/app" data-sveltekit-reload>Continue with single sign-on</a>
  <div class="links"><a href="/signup">Create an account</a></div>
</AuthCard>

<style>
  .full { width: 100%; }
  .links { display: flex; justify-content: space-between; font-size: 13px; }
</style>
