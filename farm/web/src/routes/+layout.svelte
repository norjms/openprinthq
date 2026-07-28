<script>
  import '../app.css';
  import { onMount } from 'svelte';
  import { loadAppearance, loadPublicBranding } from '$lib/stores/appearance';

  let { children } = $props();

  // Pull the user's saved Look & Feel from the control-plane and apply it. The
  // pre-paint cookie (app.html) already set the theme with no flash; this refreshes
  // from the source of truth and no-ops if not signed in.
  onMount(async () => {
    // Signed-in users get their own saved Look & Feel; logged-out visitors fall
    // back to the host owner's public branding so the configured logo still shows.
    await loadAppearance();
    await loadPublicBranding();
  });
</script>

{@render children()}
