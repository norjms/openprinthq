<script>
  // OpenPrintHQ wordmark + hex-nozzle mark
  // SPDX-License-Identifier: AGPL-3.0-or-later
  let { size = 30, wordmark = true } = $props();
</script>

<span class="logo" style="--s:{size}px">
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
    <defs>
      <linearGradient id="ophqg{size}" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
        <stop stop-color="#9a8cff" />
        <stop offset="1" stop-color="#7c6cff" />
      </linearGradient>
    </defs>
    <path d="M32 6 L52 17 V45 L32 56 L12 45 V17 Z" fill="none" stroke="url(#ophqg{size})" stroke-width="3" stroke-linejoin="round" />
    <rect x="21" y="39" width="22" height="4.4" rx="1.6" fill="#ffb020" />
    <rect x="24" y="31" width="16" height="4.4" rx="1.6" fill="url(#ophqg{size})" />
    <rect x="27" y="23" width="10" height="4.4" rx="1.6" fill="url(#ophqg{size})" opacity="0.7" />
  </svg>
  {#if wordmark}
    <span class="wm">OpenPrint<b>HQ</b></span>
  {/if}
</span>

<style>
  .logo { display: inline-flex; align-items: center; gap: 0.55rem; }
  .wm {
    font-family: var(--font-ui);
    font-weight: 700;
    font-size: calc(var(--s) * 0.62);
    letter-spacing: -0.02em;
    color: var(--ophq-text);
  }
  .wm b { color: var(--ophq-primary-2); font-weight: 800; }
</style>
