<script>
  // Camera image with instant-load caching.
  //
  // The camera feed is a JPEG snapshot polled through the engine gateway. On a
  // cold page load that means a blank box until the first frame arrives. This
  // component shows the LAST cached frame immediately, then loads the live frame
  // off-screen and swaps it in once ready (and re-caches it). So the page feels
  // responsive: you see the last picture instantly, then it goes live. A failed
  // live load leaves the cached frame in place (with a subtle "reconnecting"
  // hint) rather than blanking out.
  //
  // The cache lives in $lib/camcache.js rather than here, because the same
  // frames are warmed in the background from the app shell. That move is also
  // what fixed it: encoding happened at full sensor resolution and the result
  // was discarded above 300KB, which on real 1680x1080 and 1920x1056 cameras
  // meant two printers in three never cached a single frame and the instant-load
  // behaviour above simply never happened.
  import { onMount } from 'svelte';
  import { readFrame, writeFrame } from '$lib/camcache.js';

  let {
    printerId,
    tick = 0,
    alt = 'camera',
    title = '',
    mode = 'fill',       // 'fill' (cover its container) | 'contain' (lightbox)
    onclick = null,
    onerror = null,      // called when a live frame fails to load
  } = $props();

  let src = $state('');        // what the visible <img> shows
  let stale = $state(false);   // true when showing a cached frame and live is failing
  let lastTick = tick;

  // `tick` is the parent's poll counter, which restarts at 0 on every page load,
  // so on its own it busts the cache within a session but not across one: every
  // cold load asked for ?t=0 again. Mixing in the mount time makes each load ask
  // for a genuinely fresh frame.
  const MOUNTED = Date.now();
  function liveUrl(t) {
    return `/api/engine/api/v1/printers/${printerId}/camera/snapshot?t=${MOUNTED}.${t}`;
  }

  function loadLive(t) {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => { stale = false; src = img.src; writeFrame(printerId, img); };
    img.onerror = () => { stale = !!src; if (onerror) onerror(); };
    img.src = liveUrl(t);
  }

  onMount(() => {
    const cached = readFrame(printerId);
    if (cached) src = cached.d;
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
  img.detail { width: 100%; max-width: 640px; aspect-ratio: 16 / 9; object-fit: contain; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); display: block; cursor: pointer; }
  img.contain { max-width: 96vw; max-height: 92vh; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); box-shadow: var(--shadow-glow); display: block; }
  img.stale { filter: saturate(0.7) brightness(0.9); }
  .skel { width: 100%; height: 100%; background: var(--ophq-bg-2); display: block; }
  .skel.contain { width: 60vw; height: 40vh; border-radius: var(--radius-sm); }
  .recon { position: absolute; top: 8px; left: 9px; font-size: 0.72rem; padding: 0.1rem 0.45rem; border-radius: 999px; background: rgba(0,0,0,0.6); color: #fff; }
</style>
