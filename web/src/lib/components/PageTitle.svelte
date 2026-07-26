<script>
  // Branded browser-tab title. Renders "<page> · <site name>" using the user's
  // branding (falls back to OpenPrintHQ). Use in place of a hardcoded <title>.
  import { branding } from '$lib/stores/appearance';
  let { page = '' } = $props();
  const title = $derived(page ? `${page} · ${$branding.siteName}` : $branding.siteName);
</script>

<svelte:head><title>{title}</title></svelte:head>
