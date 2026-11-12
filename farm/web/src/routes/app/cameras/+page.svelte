<script>
  import { base as basePath } from '$app/paths';
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import EngineError from '$lib/components/EngineError.svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import PageHead from '$lib/components/PageHead.svelte';
  import Icon from '$lib/components/AppIcon.svelte';
  import CameraStream from '$lib/components/CameraStream.svelte';
  import { goto } from '$app/navigation';
  import { markSeen, isOnline } from '$lib/online.js';

  let loading = $state(true);
  let error = $state(null);
  let printers = $state([]);       // [{id,name,model,connected,state}]
  let camTick = $state(0);
  let camErr = $state({});         // id -> true when snapshot fails
  let zoomId = $state(null);       // printer id shown fullscreen
  let extras = $state([]);         // named cameras beyond each printer's own (chamber cameras etc.)
  let extraErr = $state({});       // stream -> true when its frame fails
  let timer = null;

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.printers || d?.items || []);
    return arr.map((p) => ({ id: p.id ?? p.printer_id, name: p.name || p.model || ('Printer ' + p.id), model: p.model || '' }));
  }
  async function load() {
    loading = true; error = null;
    // camErr latches a printer out of the grid on a single failed snapshot, and
    // nothing ever cleared it, so one transient failure removed a camera for the
    // life of the page. Refresh is the button a user presses when a tile looks
    // wrong; it has to be able to undo that.
    camErr = {};
    try {
      const base = norm(await api.printers());
      api.cameras()
        .then((r) => { extraErr = {}; extras = (r?.streams || []).filter((c) => c.kind !== 'builtin' && c.enabled); })
        .catch(() => { extras = []; });

      // Paint the grid the moment we know WHICH printers exist, rather than
      // waiting on a status call per printer. Status decides a chip; the camera
      // is the entire point of this page, and holding every tile back until the
      // slowest status round-trip returned meant the cached frames had nothing
      // to paint into during exactly the second they exist to cover.
      printers = base.map((p) => ({ ...p, connected: null, state: 'checking', progress: null }));
      loading = false;

      const live = await Promise.all(base.map((p) => api.printerStatus(p.id).catch(() => null)));
      printers = base.map((p, i) => {
        const s = live[i] || null;
        if (s) markSeen(p.id, !!s.connected);
        const on = isOnline(p.id, s);
        return { ...p, connected: on,
                 state: (s?.state || (on ? 'idle' : on === null ? 'checking' : 'offline')).toString().toLowerCase(),
                 progress: s?.progress ?? null };
      });
    } catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'The engine is not answering.'); }
    finally { loading = false; }
  }
  onMount(() => {
    // Timelapses have their own page in the farm; old links to the tab land there.
    try { if (new URLSearchParams(window.location.search).get('tab') === 'timelapses') { goto(basePath + '/app/timelapses', { replaceState: true }); return; } } catch { /* */ }
    load();
    timer = setInterval(() => { camTick++; }, 4000);
    return () => clearInterval(timer);
  });

  const zoomPrinter = $derived(printers.find((p) => p.id === zoomId) || null);
  function tone(s) {
    if (/run|print/.test(s)) return 'live';
    if (/pause/.test(s)) return 'warn';
    if (/idle|ready|online|finish/.test(s)) return 'ok';
    if (/error|offline|fault|fail/.test(s)) return 'danger';
    return '';
  }
  function label(s) {
    if (/run|print/.test(s)) return 'Printing';
    if (/pause/.test(s)) return 'Paused';
    if (/idle|ready|online/.test(s)) return 'Ready';
    if (/finish/.test(s)) return 'Finished';
    if (/offline/.test(s)) return 'Offline';
    if (/error|fault|fail/.test(s)) return 'Error';
    if (/checking/.test(s)) return 'Checking';
    return s ? s[0].toUpperCase() + s.slice(1) : '';
  }
  const anyCams = $derived(printers.some((p) => p.connected !== false && !camErr[p.id]));
</script>

<PageTitle page="Cameras" />
<svelte:window onkeydown={(e) => { if (e.key === 'Escape') zoomId = null; }} />

<PageHead title="Cameras" description="Every printer's live view in one grid.">
  {#snippet actions()}
    <a class="btn" href="{basePath}/app/timelapses"><Icon name="film" size={16} />Timelapses</a>
    <button class="btn" onclick={load}><Icon name="refresh" size={16} />Refresh</button>
  {/snippet}
</PageHead>

{#if loading}
  <div class="card card-pad muted">Loading cameras</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="{basePath}/app">overview</a> first.</p></div>
{:else if error}
  <EngineError {error} onretry={load} />
{:else if printers.length === 0}
  <div class="card card-pad empty"><span class="empty-ic"><Icon name="camera" size={26} /></span><h3>No printers yet</h3><p class="muted"><a href="{basePath}/app/printers/add">Add a printer</a> to see its camera here.</p></div>
{:else}
  <div class="grid cams">
    {#each printers as p (p.id)}
      <div class="card cam">
        <div class="camhd">
          <a href="{basePath}/app/printers/{p.id}" class="cn">{p.name}</a>
          <span class="chip {tone(p.state)}">{label(p.state)}</span>
        </div>
        <div class="feed">
          {#if p.connected !== false && !camErr[p.id]}
            <CameraStream printerId={p.id} tick={camTick} alt="{p.name} camera" mode="fill"
                 onerror={() => (camErr = { ...camErr, [p.id]: true })} onclick={() => (zoomId = p.id)} title="Click to expand" />
            {#if /run|print/.test(p.state) && p.progress != null}<span class="cam-prog">{Math.round(p.progress)}%</span>{/if}
          {:else}
            <div class="nocam muted">{p.connected === false ? 'Offline' : 'No camera feed'}</div>
          {/if}
        </div>
      </div>
    {/each}
    {#each extras as c (c.stream_name)}
      <div class="card cam">
        <div class="camhd">
          <a href="{basePath}/app/printers/{c.printer_id}" class="cn">{c.printer_name}, {c.camera_name}</a>
          <span class="chip {c.state === 'ONLINE' ? 'ok' : c.state === 'OFFLINE' ? 'danger' : ''}">{c.state === 'ONLINE' ? 'Online' : c.state === 'OFFLINE' ? 'Offline' : 'Camera'}</span>
        </div>
        <div class="feed">
          {#if !extraErr[c.stream_name]}
            <img src={api.cameraFrameUrl(c.stream_name, camTick)} alt="{c.printer_name} {c.camera_name}"
                 onerror={() => (extraErr = { ...extraErr, [c.stream_name]: true })} />
          {:else}
            <div class="nocam muted">No camera feed</div>
          {/if}
        </div>
      </div>
    {/each}
  </div>
  {#if !anyCams}<p class="muted hint">No live camera feeds right now. A printer shows here when it is connected and has a camera.</p>{/if}
{/if}

{#if zoomPrinter}
  <div class="lightbox" role="presentation" onclick={() => (zoomId = null)}>
    <button class="lb-close" onclick={() => (zoomId = null)} aria-label="Close"><Icon name="x" size={18} /></button>
    <CameraStream printerId={zoomPrinter.id} tick={camTick} alt="{zoomPrinter.name} camera" mode="contain" onclick={(e) => e.stopPropagation()} />
    <div class="lb-cap">{zoomPrinter.name}, live</div>
  </div>
{/if}

<style>
  .empty { text-align: center; padding: 40px; display: flex; flex-direction: column; align-items: center; }
  .empty-ic { width: 56px; height: 56px; border-radius: 16px; background: var(--ah-chip-live-bg); color: var(--ah-accent); display: grid; place-items: center; margin-bottom: 10px; }
  .cams { grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr)); gap: 14px; }
  .cam { overflow: hidden; display: flex; flex-direction: column; }
  .camhd { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; }
  .cn { font-weight: 600; color: var(--ah-text); text-decoration: none; }
  .cn:hover { color: var(--ah-link); }
  .feed { position: relative; aspect-ratio: 16 / 9; overflow: hidden; background: var(--ah-panel); border-top: 1px solid var(--ah-glass-line); }
  .feed > :global(.wrap) { position: absolute; inset: 0; }
  .feed :global(img), .feed :global(video:not(.hidden)) { width: 100%; height: 100%; object-fit: cover; display: block; cursor: zoom-in; }
  .nocam { width: 100%; height: 100%; display: grid; place-items: center; font-size: 13px; }
  .cam-prog { position: absolute; bottom: 10px; right: 10px; font-size: 12px; padding: 1px 9px; border-radius: 999px; background: rgba(20, 30, 38, 0.6); color: var(--ah-text); }
  .hint { margin: 0; font-size: 13px; }
  .lightbox { position: fixed; inset: 0; z-index: 200; background: rgba(12, 20, 26, 0.88); backdrop-filter: blur(6px); display: grid; place-items: center; padding: 2rem; cursor: zoom-out; }
  .lightbox :global(img) { max-width: 96vw; max-height: 92vh; border-radius: var(--ah-radius-choice); border: 1px solid var(--ah-glass-line); cursor: default; }
  .lb-close { position: fixed; top: 18px; right: 20px; width: 40px; height: 40px; border-radius: 50%; background: var(--ah-glass); border: 1px solid var(--ah-field-line); color: var(--ah-text); cursor: pointer; display: grid; place-items: center; }
  .lb-cap { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); color: var(--ah-text-2); font-size: 13px; }
</style>
