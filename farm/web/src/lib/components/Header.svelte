<script>
  import { base as basePath } from '$app/paths';
  import Logo from './Logo.svelte';
</script>

<header class="site-header">
  <div class="container inner">
    <a href="{basePath}/app" class="brand"><Logo size={30} /></a>
    <div class="actions">
      <a href="/" class="btn btn-ghost btn-sm" data-sveltekit-reload>ArtisanHub</a>
      <a href="{basePath}/app" class="btn btn-primary btn-sm">Open the farm</a>
    </div>
  </div>
</header>

<style>
  .site-header {
    position: sticky; top: 0; z-index: 50;
    backdrop-filter: blur(12px);
    background: var(--ophq-glass);
    border-bottom: 1px solid var(--ophq-border-soft);
  }
  .inner { display: flex; align-items: center; justify-content: space-between; min-height: 66px; padding: 0.4rem 0; }
  .nav { display: flex; gap: 1.6rem; }
  .nav a { color: var(--ophq-text-2); font-size: 0.92rem; font-weight: 500; }
  .nav a:hover { color: var(--ophq-text); }
  .actions { display: flex; gap: 0.6rem; align-items: center; }
  @media (max-width: 820px) { .nav { display: none; } }
  /* Mobile: the centre nav hides ≤820px, but below 700px the actions cluster
     (theme switcher + Sign in + Launch) can't sit beside the logo without
     overflowing the banner. Let the header wrap so actions drop to their own
     full-width row (right-aligned, wrapping further on very narrow phones)
     instead of spilling off-screen. */
  @media (max-width: 700px) {
    .inner { flex-wrap: wrap; height: auto; min-height: 58px; padding: 0.55rem 0; row-gap: 0.55rem; }
    .brand { flex: 0 0 auto; }
    .actions { width: 100%; justify-content: flex-end; flex-wrap: wrap; gap: 0.4rem 0.5rem; }
  }
  @media (max-width: 380px) {
    .actions .btn-sm { padding-left: 0.7rem; padding-right: 0.7rem; }
  }
</style>
