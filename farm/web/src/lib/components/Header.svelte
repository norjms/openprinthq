<script>
  import Logo from './Logo.svelte';
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
      <a href="/app" class="btn btn-ghost btn-sm">Sign in</a>
      <a href="/app" class="btn btn-primary btn-sm">Launch your HQ</a>
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
</style>
