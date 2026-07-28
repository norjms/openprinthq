<script>
  // OpenPrintHQ wordmark + hex-nozzle mark — brandable per user.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  // If the user uploaded a custom logo it replaces the mark + wordmark; otherwise
  // the built-in SVG mark shows with the (optionally renamed) wordmark text. The
  // mark now uses theme variables so it recolours with the active theme.
  import { branding, activeLogo } from '$lib/stores/appearance';

  let { size = 30, wordmark = true } = $props();

  const text = $derived($branding?.wordmark || $branding?.siteName || 'OpenPrintHQ');
  const custom = $derived($activeLogo || '');
  const isDefault = $derived(text === 'OpenPrintHQ');
</script>

{#if custom}
  <span class="logo custom" style="--s:{size}px">
    <img src={custom} alt={text} />
  </span>
{:else}
  <span class="logo" style="--s:{size}px">
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id="ophqg{size}" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
          <stop stop-color="var(--ophq-primary-2)" />
          <stop offset="1" stop-color="var(--ophq-primary)" />
        </linearGradient>
      </defs>
      <path d="M32 6 L52 17 V45 L32 56 L12 45 V17 Z" fill="none" stroke="url(#ophqg{size})" stroke-width="3" stroke-linejoin="round" />
      <rect x="21" y="39" width="22" height="4.4" rx="1.6" fill="var(--ophq-accent)" />
      <rect x="24" y="31" width="16" height="4.4" rx="1.6" fill="url(#ophqg{size})" />
      <rect x="27" y="23" width="10" height="4.4" rx="1.6" fill="url(#ophqg{size})" opacity="0.7" />
    </svg>
    {#if wordmark}
      {#if isDefault}
        <span class="wm">OpenPrint<b>HQ</b></span>
      {:else}
        <span class="wm">{text}</span>
      {/if}
    {/if}
  </span>
{/if}

<style>
  .logo { display: inline-flex; align-items: center; gap: 0.55rem; }
  /* Custom uploads are full lockups (mark + wordmark), so render them much taller
     than the bare mark height so the wordmark stays readable. Height ≈ 3× the
     passed size, capped at 84px; width scales freely. The marketing header grows
     to fit (min-height), and the app sidebar has vertical room. */
  .logo.custom img {
    height: calc(var(--s) * 3); max-height: 84px; width: auto; max-width: 460px;
    object-fit: contain; display: block;
  }
  .logo img { width: auto; max-width: 240px; object-fit: contain; border-radius: 4px; display: block; }
  .wm {
    font-family: var(--font-ui);
    font-weight: 700;
    font-size: calc(var(--s) * 0.62);
    letter-spacing: -0.02em;
    color: var(--ophq-text);
  }
  .wm b { color: var(--ophq-primary-2); font-weight: 800; }
</style>
