<script>
  // One printer's camera on Farm > Cameras. The last picture stays on screen while the next one loads (a Bambu
  // Lab camera takes a few seconds to answer), with a spinner saying it is refreshing and how old the picture is.
  // The last picture is also kept in this browser, small, so the page opens with it instead of an empty box.
  import { onMount } from 'svelte';

  /** @type {{ name: string, tick: number }} */
  let { name, tick } = $props();

  const KEY = `ahub_cam_${name}`;
  const MAX_W = 640;           // the cached copy: wide enough for a tile, a few tens of KB
  const MAX_BYTES = 250_000;

  let src = $state('');        // what is on screen: this load's object URL, or the cached copy
  let at = $state(0);          // when that picture was taken (ms)
  let loading = $state(false);
  let failed = $state(false);  // the last load got no picture
  let now = $state(Date.now());
  let objectUrl = '';
  let lastTick = -1;

  function readCache() {
    try {
      const o = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (o && typeof o.d === 'string' && o.d.startsWith('data:image/')) { src = o.d; at = Number(o.t) || 0; }
    } catch { /* storage off or a bad entry: start empty */ }
  }

  function writeCache(img) {
    try {
      const scale = Math.min(1, MAX_W / img.naturalWidth);
      const c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(img.naturalWidth * scale));
      c.height = Math.max(1, Math.round(img.naturalHeight * scale));
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      const d = c.toDataURL('image/jpeg', 0.6);
      if (d.length < MAX_BYTES) localStorage.setItem(KEY, JSON.stringify({ d, t: Date.now() }));
    } catch { /* full or off: the tile still works, it just opens empty next time */ }
  }

  async function refresh() {
    if (loading) return;       // a slow camera: never stack a second request behind the first
    loading = true;
    try {
      const r = await fetch(`/api/mobile/v1/printers/${encodeURIComponent(name)}/snapshot?t=${Date.now()}`,
        { cache: 'no-store', credentials: 'same-origin' });
      if (!r.ok) throw new Error(String(r.status));
      const url = URL.createObjectURL(await r.blob());
      const img = new Image();
      img.src = url;
      await img.decode();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = url;
      src = url;
      at = Date.now();
      failed = false;
      writeCache(img);
    } catch {
      failed = true;
    } finally {
      loading = false;
    }
  }

  $effect(() => { if (tick !== lastTick) { lastTick = tick; refresh(); } });

  onMount(() => {
    readCache();
    const clock = setInterval(() => (now = Date.now()), 1000);
    return () => { clearInterval(clock); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  });

  function age(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 5) return 'just now';
    if (s < 60) return `${s} s ago`;
    const m = Math.round(s / 60);
    return m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
  }
</script>

<div class="cam" data-testid="camera-tile" data-camera={name} aria-busy={loading}>
  {#if src}
    <img {src} alt="Camera on {name}" class:stale={failed} />
  {:else if failed && !loading}
    <span class="ah-muted empty">No camera frame from {name}</span>
  {:else}
    <div class="skel" aria-label="Loading the camera on {name}"></div>
  {/if}
  {#if loading}
    <span class="badge refreshing" role="status"><span class="spin" aria-hidden="true"></span>{src ? 'Refreshing' : 'Loading'}</span>
  {/if}
  {#if src && at}
    <span class="badge when" class:warn={failed}>{failed ? `Last picture ${age(now - at)}` : age(now - at)}</span>
  {/if}
</div>

<style>
  .cam { position: relative; width: 100%; height: 100%; display: grid; place-items: center; }
  img { width: 100%; height: 100%; object-fit: cover; display: block; transition: filter 0.3s; }
  img.stale { filter: saturate(0.6) brightness(0.85); }
  .empty { padding: 0 16px; text-align: center; }
  .skel {
    width: 100%; height: 100%;
    background: linear-gradient(100deg, rgba(255,255,255,0.03) 30%, rgba(255,255,255,0.09) 50%, rgba(255,255,255,0.03) 70%);
    background-size: 220% 100%; animation: shimmer 1.4s linear infinite;
  }
  .badge {
    position: absolute; display: inline-flex; align-items: center; gap: 6px;
    padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 600;
    background: rgba(20, 30, 38, 0.66); color: var(--ah-text-2, #dce6ee); border: 1px solid rgba(255,255,255,0.14);
    backdrop-filter: blur(6px);
  }
  .refreshing { top: 8px; right: 8px; }
  .when { bottom: 8px; left: 8px; font-weight: 500; }
  .when.warn { color: #f4c27a; border-color: rgba(244,194,122,0.4); }
  .spin {
    width: 10px; height: 10px; border-radius: 50%;
    border: 2px solid rgba(255,255,255,0.25); border-top-color: var(--ah-accent, #8fd3ff);
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes shimmer { from { background-position: 120% 0; } to { background-position: -120% 0; } }
  @media (prefers-reduced-motion: reduce) { .spin, .skel { animation: none; } }
</style>
