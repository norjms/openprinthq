<script>
  import Logo from './Logo.svelte';
  // AGPL-3.0 §13: the running instance must offer its source to users.
  const SOURCE_URL = 'https://internal.example.com/OpenPrintHQ/openprinthq';
</script>

<footer class="site-footer">
  <div class="container">
    <div class="top">
      <div class="col brandcol">
        <Logo size={28} />
        <p class="muted">One command center for every 3D printer. Your printers, your data, your rules.</p>
        <span class="chip ok">AGPL-3.0 · Open source</span>
      </div>
      <div class="col">
        <h4>Product</h4>
        <a href="/#features">Features</a>
        <a href="/#printers">Supported printers</a>
        <a href="/#how">How it works</a>
        <a href="/signup">Launch your HQ</a>
      </div>
      <div class="col">
        <h4>Open</h4>
        <a href={SOURCE_URL}>Source code</a>
        <a href={SOURCE_URL + '/src/branch/main/LICENSE'}>License (AGPL-3.0)</a>
        <a href={SOURCE_URL + '/src/branch/main/NOTICE'}>Attribution &amp; credits</a>
      </div>
    </div>
    <hr class="divider" />
    <div class="bottom">
      <span class="muted mono">© 2026 OpenPrintHQ contributors</span>
      <span class="muted">
        This is free software under the
        <a href={SOURCE_URL}>GNU AGPL v3</a>. Complete corresponding source is available to every user of this instance.
      </span>
    </div>
  </div>
</footer>

<style>
  .site-footer { border-top: 1px solid var(--ophq-border); margin-top: 5rem; padding: 3rem 0 2.5rem; background: var(--ophq-bg-2); }
  .top { display: grid; grid-template-columns: 1.6fr 1fr 1fr; gap: 2rem; }
  .brandcol p { max-width: 34ch; margin: 0.8rem 0; }
  .col h4 { color: var(--ophq-text); font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 0.8rem; }
  .col a { display: block; color: var(--ophq-muted); font-size: 0.9rem; padding: 0.2rem 0; }
  .col a:hover { color: var(--ophq-primary-2); }
  .bottom { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; font-size: 0.85rem; }
  @media (max-width: 820px) { .top { grid-template-columns: 1fr; } }
</style>
