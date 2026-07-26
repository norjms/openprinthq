<script>
  import Logo from './Logo.svelte';
  import { branding } from '$lib/stores/appearance';
  // AGPL-3.0 §13: the running instance must offer its source to EVERY user,
  // without login. These point at the public /legal page (served ungated),
  // not the Authentik-gated git host. Hosts can add their own trademark line and
  // contact info via Settings → Look & Feel; the source-offer link stays for
  // license compliance.
  const contact = $derived(($branding.contact || '').trim());
  const isEmail = $derived(/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact));
  const isUrl = $derived(/^https?:\/\/\S+$/i.test(contact));
</script>

<footer class="site-footer">
  <div class="container">
    <div class="top">
      <div class="col brandcol">
        <Logo size={28} />
        <p class="muted">{$branding.tagline} Your printers, your data, your rules.</p>
        <span class="chip ok">AGPL-3.0 · Open source</span>
      </div>
      <div class="col">
        <h4>Product</h4>
        <a href="/#features">Features</a>
        <a href="/#printers">Supported printers</a>
        <a href="/#how">How it works</a>
        <a href="/app">Launch your HQ</a>
      </div>
      <div class="col">
        <h4>Open</h4>
        <a href="/legal#source">Source code</a>
        <a href="/legal#license">License (AGPL-3.0)</a>
        <a href="/legal#attribution">Attribution &amp; credits</a>
      </div>
    </div>
    <hr class="divider" />
    <div class="bottom">
      <span class="muted mono">
        {#if $branding.trademark}{$branding.trademark}{:else}© 2026 {$branding.siteName} contributors{/if}
      </span>
      <span class="foot-right">
        {#if contact}
          <span class="muted contact">
            {#if isEmail}<a href={`mailto:${contact}`}>{contact}</a>
            {:else if isUrl}<a href={contact} target="_blank" rel="noopener">{contact}</a>
            {:else}{contact}{/if}
          </span>
        {/if}
        <!-- AGPL §13 source offer — kept for license compliance -->
        <span class="muted">Source available (<a href="/legal">AGPL-3.0</a>) to every user of this instance.</span>
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
  .bottom { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; font-size: 0.85rem; align-items: flex-start; }
  .foot-right { display: flex; flex-direction: column; gap: 0.3rem; align-items: flex-end; text-align: right; }
  .contact a { color: var(--ophq-primary-2); }
  @media (max-width: 820px) { .top { grid-template-columns: 1fr; } .bottom { flex-direction: column; } .foot-right { align-items: flex-start; text-align: left; } }
</style>
