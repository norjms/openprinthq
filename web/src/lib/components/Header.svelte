<script>
  import Logo from './Logo.svelte';
  import ThemeSwitcher from './ThemeSwitcher.svelte';
</script>

<header class="site-header">
  <div class="container inner">
    <a href="/" class="brand"><Logo size={30} /></a>
    <nav class="nav">
      <a href="/#features">Features</a>
      <a href="/#printers">Printers</a>
      <a href="/#how">How it works</a>
      <a href="/#open">Open source</a>
    </nav>
    <div class="actions">
      <ThemeSwitcher />
      <a href="/app" class="btn btn-ghost btn-sm" data-sveltekit-reload>Sign in</a>
      <a href="/signup" class="btn btn-primary btn-sm">Launch your HQ</a>
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
  .inner { display: flex; align-items: center; justify-content: space-between; height: 66px; }
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
