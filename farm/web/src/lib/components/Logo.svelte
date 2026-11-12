<script>
  // ArtisanHub Farm mark and name, brandable per instance.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  // An uploaded logo replaces the mark and the name. Otherwise the built-in
  // mark (a hexagon holding stacked print layers) sits on an accent tile beside
  // the product name, which the owner can rename.
  import { branding, activeLogo } from '$lib/stores/appearance';

  let { size = 36, wordmark = true } = $props();

  const text = $derived($branding?.wordmark || $branding?.siteName || 'ArtisanHub Farm');
  const custom = $derived($activeLogo || '');
</script>

{#if custom}
  <span class="logo custom">
    <img src={custom} alt={text} />
  </span>
{:else}
  <span class="logo">
    <span class="tile" style="--s:{size}px" aria-hidden="true">
      <svg width={Math.round(size * 0.56)} height={Math.round(size * 0.56)} viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2.5 20.5 7.3v9.4L12 21.5l-8.5-4.8V7.3z" />
        <path d="M8 16h8M9.5 12.8h5M11 9.6h2" />
      </svg>
    </span>
    {#if wordmark}<span class="wm">{text}</span>{/if}
  </span>
{/if}

<style>
  .logo { display: inline-flex; align-items: center; gap: 10px; min-width: 0; }
  .tile {
    width: var(--s); height: var(--s); flex: none; border-radius: 12px;
    display: grid; place-items: center;
    background: var(--ah-accent); color: var(--ah-on-accent);
  }
  .wm {
    font-family: var(--ah-font-display); font-weight: 600; font-size: 18px;
    letter-spacing: -0.02em; color: var(--ah-text);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  /* Uploaded logos are full lockups (mark and name): one fixed height everywhere. */
  .logo.custom { display: flex; width: 100%; }
  .logo.custom img { height: 40px; width: auto; max-width: 100%; object-fit: contain; display: block; }
</style>
