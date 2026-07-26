<script>
  import '../app.css';
  import { onMount } from 'svelte';
  import { loadAppearance } from '$lib/stores/appearance';

  let { children } = $props();

  // Pull the user's saved Look & Feel from the control-plane and apply it. The
  // pre-paint cookie (app.html) already set the theme with no flash; this refreshes
  // from the source of truth and no-ops if not signed in.
  onMount(() => { loadAppearance(); });
</script>

{@render children()}
