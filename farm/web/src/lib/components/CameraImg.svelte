<script>
  // Camera image with instant-load caching.
  //
  // The camera feed is a JPEG snapshot polled through the engine gateway. On a
  // cold page load that means a blank box until the first frame arrives. This
  // component keeps the LAST frame per printer in localStorage and shows it
  // immediately, then loads the live frame off-screen and swaps it in once ready
  // (and re-caches it). So the page feels responsive: you see the last picture
  // instantly, then it goes live. A failed live load leaves the cached frame in
  // place (with a subtle "reconnecting" hint) rather than blanking out.
  import { onMount } from 'svelte';

  let {
    printerId,
    tick = 0,
    alt = 'camera',
    title = '',
    mode = 'fill',       // 'fill' (cover its container) | 'contain' (lightbox)
    onclick = null,
    onerror = null,      // called when a live frame fails to load
  } = $props();

  const cacheKey = (id) => `ophq_cam_${id}`;
  let src = $state('');        // what the visible <img> shows
  let stale = $state(false);   // true when showing a cached frame and live is failing
  let lastTick = tick;

  function liveUrl(t) {
    return `/api/engine/api/v1/printers/${printerId}/camera/snapshot?t=${t}`;
  }

  function cacheFrame(img) {
    // Draw the just-loaded (same-origin) frame to a canvas and stash a compact
    // JPEG data-URL. Guarded so a huge frame can't blow the localStorage quota.
    try {
      const c = document.createElement('canvas');
      c.width = img.naturalWidth || 640;
      c.height = img.naturalHeight || 360;
      c.getContext('2d').drawImage(img, 0, 0);
      const data = c.toDataURL('image/jpeg', 0.6);
      if (data.length < 300000) localStorage.setItem(cacheKey(printerId), data);
    } catch { /* canvas/quota/tainted — skip caching, live still works */ }
  }

  function loadLive(t) {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => { stale = false; src = img.src; cacheFrame(img); };
    img.onerror = () => { stale = !!src; if (onerror) onerror(); };
    img.src = liveUrl(t);
  }

  onMount(() => {
    try { const cached = localStorage.getItem(cacheKey(printerId)); if (cached) src = cached; } catch { /* */ }
    loadLive(tick);
  });

  // Reload the live frame whenever the parent bumps the poll tick.
  $effect(() => {
    if (tick !== lastTick) { lastTick = tick; loadLive(tick); }
  });
</script>

{#if src}
  <img class={mode} {src} {alt} {title} class:stale onclick={onclick} />
  {#if stale}<span class="recon" aria-hidden="true">reconnecting…</span>{/if}
{:else}
  <div class="skel {mode}" aria-label={alt}></div>
{/if}

<style>
  img.fill { width: 100%; height: 100%; object-fit: cover; display: block; cursor: pointer; }
  img.detail { width: 100%; max-width: 640px; aspect-ratio: 16 / 9; object-fit: contain; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); display: block; cursor: zoom-in; }
  img.contain { max-width: 96vw; max-height: 92vh; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); box-shadow: var(--shadow-glow); display: block; }
  img.stale { filter: saturate(0.7) brightness(0.9); }
  .skel { width: 100%; height: 100%; background: var(--ophq-bg-2); display: block; }
  .skel.contain { width: 60vw; height: 40vh; border-radius: var(--radius-sm); }
  .recon { position: absolute; top: 8px; left: 9px; font-size: 0.72rem; padding: 0.1rem 0.45rem; border-radius: 999px; background: rgba(0,0,0,0.6); color: #fff; }
</style>
