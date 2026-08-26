<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import CameraStream from '$lib/components/CameraStream.svelte';
  import Timelapses from '$lib/components/Timelapses.svelte';
  import { markSeen, isOnline } from '$lib/online.js';

  let tab = $state('cameras');   // 'cameras' | 'timelapses'
  function setTab(t) {
    tab = t;
    try { const u = new URL(window.location.href); if (t === 'cameras') u.searchParams.delete('tab'); else u.searchParams.set('tab', t); window.history.replaceState({}, '', u); } catch { /* */ }
  }

  let loading = $state(true);
  let error = $state(null);
  let printers = $state([]);       // [{id,name,model,connected,state}]
  let camTick = $state(0);
  let camErr = $state({});         // id -> true when snapshot fails
  let zoomId = $state(null);       // printer id shown fullscreen
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
    } catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(() => {
    try { if (new URLSearchParams(window.location.search).get('tab') === 'timelapses') tab = 'timelapses'; } catch { /* */ }
    load();
    timer = setInterval(() => { camTick++; }, 4000);
    return () => clearInterval(timer);
  });

  const zoomPrinter = $derived(printers.find((p) => p.id === zoomId) || null);
  function tone(s) {
    if (/run|print/.test(s)) return 'primary';
    if (/pause/.test(s)) return 'accent';
    if (/idle|ready|online|finish/.test(s)) return 'ok';
    if (/error|offline|fault|fail/.test(s)) return 'danger';
    return '';
  }
  const anyCams = $derived(printers.some((p) => p.connected !== false && !camErr[p.id]));
</script>

<PageTitle page="Cameras" />
<svelte:window onkeydown={(e) => { if (e.key === 'Escape') zoomId = null; }} />

<div class="head">
  <div><h1>Cameras</h1><p class="muted">Every printer's live view in one grid.</p></div>
  {#if tab === 'cameras'}<button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>{/if}
</div>

<div class="camtabs">
  <button class="camtab" class:on={tab === 'cameras'} onclick={() => setTab('cameras')}>Live cameras</button>
  <button class="camtab" class:on={tab === 'timelapses'} onclick={() => setTab('timelapses')}>Timelapses</button>
</div>

{#if tab === 'timelapses'}
  <Timelapses />
{:else if loading}
  <div class="card card-pad muted">Loading cameras…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else if printers.length === 0}
  <div class="card card-pad empty"><div class="ic">📷</div><h3>No printers yet</h3><p class="muted"><a href="/app/printers/add">Add a printer</a> to see its camera here.</p></div>
{:else}
  <div class="grid cams">
    {#each printers as p (p.id)}
      <div class="card cam">
        <div class="camhd">
          <a href="/app/printers/{p.id}" class="cn">{p.name}</a>
          <span class="chip {tone(p.state)}">{p.state}</span>
        </div>
        <div class="feed">
          {#if p.connected !== false && !camErr[p.id]}
            <CameraStream printerId={p.id} tick={camTick} alt="{p.name} camera" mode="fill"
                 onerror={() => (camErr = { ...camErr, [p.id]: true })} onclick={() => (zoomId = p.id)} title="Click to expand" />
            {#if /run|print/.test(p.state) && p.progress != null}<span class="cam-prog mono">{Math.round(p.progress)}%</span>{/if}
          {:else}
            <div class="nocam muted">{p.connected === false ? 'Offline' : 'No camera feed'}</div>
          {/if}
        </div>
      </div>
    {/each}
  </div>
  {#if !anyCams}<p class="muted hint">No live camera feeds right now — printers must be connected and camera-equipped.</p>{/if}
{/if}

{#if zoomPrinter}
  <div class="lightbox" role="presentation" onclick={() => (zoomId = null)}>
    <button class="lb-close" onclick={() => (zoomId = null)} aria-label="Close">✕</button>
    <CameraStream printerId={zoomPrinter.id} tick={camTick} alt="{zoomPrinter.name} camera" mode="contain" onclick={(e) => e.stopPropagation()} />
    <div class="lb-cap mono">{zoomPrinter.name} · live</div>
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1rem; }
  .head h1 { margin: 0; }
  .camtabs { display: flex; gap: 0.4rem; margin-bottom: 1.2rem; border-bottom: 1px solid var(--ophq-border); }
  .camtab { padding: 0.5rem 0.9rem; font-size: 0.88rem; font-weight: 600; background: none; border: none; border-bottom: 2px solid transparent; color: var(--ophq-text-2); cursor: pointer; margin-bottom: -1px; }
  .camtab.on { color: var(--ophq-primary-2); border-bottom-color: var(--ophq-primary); }
  .empty { text-align: center; padding: 2.6rem; } .empty .ic { font-size: 1.8rem; }
  .cams { grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
  .cam { overflow: hidden; }
  .camhd { display: flex; align-items: center; justify-content: space-between; padding: 0.7rem 0.9rem; }
  .cn { font-weight: 600; color: var(--ophq-text); text-decoration: none; }
  .cn:hover { color: var(--ophq-primary-2); }
  .feed { position: relative; aspect-ratio: 16 / 9; background: var(--ophq-bg-2); border-top: 1px solid var(--ophq-border); }
  .feed img { width: 100%; height: 100%; object-fit: cover; display: block; cursor: zoom-in; }
  .nocam { width: 100%; height: 100%; display: grid; place-items: center; font-size: 0.85rem; }
  .cam-prog { position: absolute; bottom: 8px; right: 9px; font-size: 0.78rem; padding: 0.12rem 0.45rem; border-radius: 999px; background: rgba(0,0,0,0.6); color: #fff; }
  .hint { margin-top: 1rem; font-size: 0.88rem; }
  .chip.primary { color: var(--ophq-primary-2); border-color: rgba(124,108,255,0.35); background: var(--ophq-primary-dim); }
  .chip.accent { color: var(--ophq-accent); border-color: rgba(255,176,32,0.3); background: rgba(255,176,32,0.08); }
  .chip.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .chip.danger { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .lightbox { position: fixed; inset: 0; z-index: 200; background: rgba(3,5,8,0.9); backdrop-filter: blur(6px); display: grid; place-items: center; padding: 2rem; cursor: zoom-out; }
  .lightbox img { max-width: 96vw; max-height: 92vh; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); box-shadow: var(--shadow-glow); cursor: default; }
  .lb-close { position: fixed; top: 1.1rem; right: 1.3rem; width: 40px; height: 40px; border-radius: 50%; background: var(--ophq-surface); border: 1px solid var(--ophq-border); color: var(--ophq-text); font-size: 1.1rem; cursor: pointer; }
  .lb-close:hover { border-color: var(--ophq-primary); }
  .lb-cap { position: fixed; bottom: 1.3rem; left: 50%; transform: translateX(-50%); color: var(--ophq-text-2); font-size: 0.85rem; }
</style>
