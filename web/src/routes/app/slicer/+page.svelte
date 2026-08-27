<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';

  let loading = $state(true);
  let error = $state(null);
  let models = $state([]);
  let presetCats = $state([]);
  let connected = $state(false);

  // Slicer engines. OrcaSlicer is the built-in default; the rest are planned
  // (see the open-source credits on the legal page). Selecting a "soon" engine
  // is disabled until its backend lands.
  // `ready` is not hardcoded: it comes from the deployment. An engine is usable
  // when the control-plane has a workspace image configured for it, so adding a
  // slicer is a config change and this list never goes stale. Hardcoding it is
  // why Bambu and Prusa stayed unselectable after their images shipped.
  const ENGINES = $state([
    { key: 'orca', name: 'OrcaSlicer', abbr: 'O', color: '#00a352', ready: false },
    { key: 'bambu', name: 'BambuStudio', abbr: 'B', color: '#16b978', ready: false },
    { key: 'prusa', name: 'PrusaSlicer', abbr: 'P', color: '#fa6831', ready: false },
    { key: 'cura', name: 'Cura', abbr: 'C', color: '#14aaf5', ready: false },
    { key: 'creality', name: 'CrealityPrint', abbr: 'CP', color: '#059b8f', ready: false },
    { key: 'elegoo', name: 'ElegooSlicer', abbr: 'E', color: '#2b2b2b', ready: false }
  ]);

  async function loadEngines() {
    try {
      const r = await api.slicerWorkspaceEngines();
      const avail = new Set(r?.engines || []);
      for (const e of ENGINES) e.ready = avail.has(e.key);
      // Do not strand the user on an engine this deployment cannot run.
      if (!ENGINES.find((x) => x.key === engine)?.ready) {
        const first = ENGINES.find((x) => x.ready);
        if (first) engine = first.key;
      }
    } catch { /* leave everything unselectable rather than promising a slicer */ }
  }
  let engine = $state('orca');
  function selectEngine(k) {
    const e = ENGINES.find((x) => x.key === k);
    if (!e || !e.ready || k === engine) return;
    engine = k;
    // A different engine is a different image, so whatever is on screen belongs
    // to the old one. Drop it and ask what is running for the new selection.
    wsUrl = null; wsStatus = null; wsError = null; wsFull = false;
    wsLoad();
  }

  async function load() {
    loading = true; error = null;
    try {
      const m = await api.slicerModels();
      models = m && typeof m === 'object' && !Array.isArray(m) ? Object.keys(m) : (Array.isArray(m) ? m : []);
      connected = models.length > 0;
      try {
        const p = await api.slicerPresets();
        presetCats = p && typeof p === 'object' ? Object.keys(p).filter((k) => !k.endsWith('_status')) : [];
      } catch { presetCats = []; }
    } catch (e) {
      error = e.status === 409 ? 'no-instance' : (e.message || 'slicer unreachable');
    } finally {
      loading = false;
    }
  }
  // ---- workspace (containerised desktop slicer in an iframe) ------------
  // The control-plane owns the Kasm session; this only ever holds a URL.
  let wsConfigured = $state(false);
  let wsUrl = $state(null);
  let wsStatus = $state(null);
  let wsBusy = $state(false);
  let wsError = $state(null);
  let wsFull = $state(false);
  // True once a session we were showing has ended, so the empty state can say
  // "closed" rather than looking like it never started.
  let justClosed = $state(false);
  let poll = null;
  // Set by "Open in slicer" on the Files page. The session fetches this file at
  // startup; a running session cannot be given one, since the container
  // environment is fixed at creation.
  let pendingFileId = $state(null);
  let pendingFileName = $state(null);

  async function wsLoad() {
    try {
      const r = await api.slicerWorkspace(engine);
      wsConfigured = !!r.configured;
      const wasRunning = !!wsUrl;
      wsUrl = r.running ? r.url : null;
      wsStatus = r.status || null;
      // A container reports "starting" before it will accept a connection, so
      // keep polling until it is actually up rather than framing a dead URL.
      if (r.running && String(r.status || '').toLowerCase() !== 'running') schedulePoll();
      // Keep watching a live session. When the slicer is closed the session
      // ends, and the embedded URL stops being valid: left alone the frame
      // falls back to the workspace host's own dashboard, which is confusing and
      // is not ours. Drop the frame and show our own guidance instead.
      else if (r.running) schedulePoll(10000);
      else if (wasRunning) { wsFull = false; justClosed = true; }
    } catch { wsConfigured = false; }
  }
  function schedulePoll(ms = 4000) {
    clearTimeout(poll);
    poll = setTimeout(wsLoad, ms);
  }
  async function wsStart() {
    wsBusy = true; wsError = null;
    justClosed = false;
    try {
      const r = await api.slicerWorkspaceStart(engine, pendingFileId);
      wsUrl = r.url; wsStatus = r.status;
      if (String(r.status || '').toLowerCase() !== 'running') schedulePoll();
    } catch (e) { wsError = e.message || 'could not start the slicer'; }
    finally { wsBusy = false; }
  }
  async function wsStop() {
    wsBusy = true; wsError = null;
    try { await api.slicerWorkspaceStop(); wsUrl = null; wsStatus = null; wsFull = false; }
    catch (e) { wsError = e.message || 'could not stop the slicer'; }
    finally { wsBusy = false; }
  }
  function onKey(e) { if (e.key === 'Escape') wsFull = false; }

  onMount(() => {
    const q = new URLSearchParams(window.location.search);
    const f = q.get('file');
    if (f) pendingFileId = f;
    pendingFileName = q.get('name');
    load();
    loadEngines();
    wsLoad();
    window.addEventListener('keydown', onKey);
    return () => { clearTimeout(poll); window.removeEventListener('keydown', onKey); };
  });
</script>

<PageTitle page="Slicer" />

<div class="head">
  <div><h1>Slicer</h1><p class="muted">OrcaSlicer, built in — slice server-side, no desktop app.</p></div>
  <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
</div>

{#if loading}
  <div class="card card-pad muted">Checking slicer…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Slicer unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else}
  <div class="card card-pad engwrap">
    <span class="eyebrow">Slicer engine</span>
    <p class="muted small">OrcaSlicer is built in and ready. More engines are on the way — <a href="/legal#slicers">credits & licenses</a>.</p>
    <div class="engines">
      {#each ENGINES as e}
        <button type="button" class="engine" class:on={e.key === engine} class:soon={!e.ready} disabled={!e.ready}
                onclick={() => selectEngine(e.key)} title={e.ready ? e.name : e.name + ' — coming soon'}>
          <span class="ebadge" style="background:{e.color}">{e.abbr}</span>
          <span class="ename">{e.name}</span>
          {#if !e.ready}<span class="ribbon">Coming soon</span>{/if}
        </button>
      {/each}
    </div>
  </div>

  {#if wsConfigured}
    <div class="card workspace" class:full={wsFull}>
      <div class="wsbar">
        <div class="flex center gap">
          <span class="dot" class:on={wsStatus === 'running'}></span>
          <b>Workspace</b>
          <span class="muted small">
            {#if !wsUrl}Not running{:else if wsStatus === 'running'}{ENGINES.find((x) => x.key === engine)?.name} running{:else}Starting up{/if}
          </span>
          {#if pendingFileId && !wsUrl}
            <span class="pill">will open {pendingFileName || ('file ' + pendingFileId)}</span>
          {/if}
        </div>
        <div class="flex center gap">
          {#if wsUrl}
            <button class="btn btn-ghost btn-sm" onclick={() => (wsFull = !wsFull)}>{wsFull ? 'Exit full screen' : 'Full screen'}</button>
            <button class="btn btn-ghost btn-sm" onclick={wsStop} disabled={wsBusy}>Stop</button>
          {:else}
            <button class="btn btn-sm" onclick={wsStart} disabled={wsBusy}>{wsBusy ? 'Starting...' : 'Open slicer'}</button>
          {/if}
        </div>
      </div>

      {#if wsError}
        <div class="card-pad"><p class="muted">{wsError}</p></div>
      {:else if wsUrl && wsStatus === 'running'}
        <iframe
          title="Slicer workspace"
          src={wsUrl}
          allow="clipboard-read; clipboard-write; fullscreen"
          referrerpolicy="no-referrer"
        ></iframe>
      {:else if wsUrl}
        <div class="wsempty"><p class="muted">Starting your slicer session. This takes a few seconds the first time.</p></div>
      {:else}
        <div class="wsempty">
          {#if justClosed}
            <p class="lead">Slicer closed.</p>
            <p class="muted">Your presets, printers and downloaded models are kept, so opening it
              again picks up where you left off.</p>
          {:else}
            <p class="lead">Run the full slicer in your browser.</p>
          {/if}
          <ol class="steps">
            <li>Choose a slicer above, then <b>Open slicer</b>.</li>
            <li>First time only: pick your printer and filament in the slicer's setup wizard.
              OpenPrintHQ is added as a print destination automatically.</li>
            <li>Bring in a model: use <b>Open in slicer</b> on the Files page, or run
              <code>ophq-get</code> in the slicer's terminal to pull from your library.</li>
            <li>Slice, then <b>Send</b>. The plate lands in your print queue.</li>
          </ol>
          <p class="muted small">Only one slicer runs at a time. Switching stops the other one to
            free its memory, and your work is kept either way.</p>
        </div>
      {/if}
    </div>
  {/if}

  <div class="card card-pad status glow">
    <div class="flex between center">
      <div class="flex center gap">
        <span class="dot" class:on={connected}></span>
        <div>
          <h3>OrcaSlicer {connected ? 'connected' : 'not reachable'}</h3>
          <p class="muted">Server-side slicing engine{connected ? ' — ready to slice STL & 3MF' : ''}.</p>
        </div>
      </div>
      <span class="chip {connected ? 'ok' : 'accent'}">{connected ? 'ready' : 'offline'}</span>
    </div>
  </div>

  <div class="grid two">
    <div class="card card-pad">
      <span class="eyebrow">Printer models ({models.length})</span>
      <div class="chips">
        {#each models.slice(0, 24) as m}<span class="chip">{m}</span>{/each}
        {#if models.length > 24}<span class="chip">+{models.length - 24} more</span>{/if}
        {#if models.length === 0}<p class="muted">—</p>{/if}
      </div>
    </div>
    <div class="card card-pad">
      <span class="eyebrow">Preset sources</span>
      <div class="chips">
        {#each presetCats as c}<span class="chip primary">{c}</span>{/each}
        {#if presetCats.length === 0}<p class="muted">—</p>{/if}
      </div>
    </div>
  </div>

  <div class="card card-pad how">
    <span class="eyebrow">How to slice</span>
    <p class="muted">Upload a model in <a href="/app/files">Files</a>, then use its <b>Slice</b> action. Pick a printer, process and filament (searchable, and auto-filtered to what's compatible with the printer) — OrcaSlicer runs it server-side and, with "Slice &amp; queue" on, the result drops straight into your <a href="/app/queue">Print queue</a>.</p>
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; }
  .head h1 { margin: 0; }
  .engwrap { margin-bottom: 1.2rem; }
  .engwrap .small { margin: 0.3rem 0 0; }
  .engines { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 0.6rem; margin-top: 0.9rem; }
  .engine { position: relative; display: flex; align-items: center; gap: 0.6rem; padding: 0.7rem 0.8rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); color: var(--ophq-text); cursor: pointer; overflow: hidden; text-align: left; }
  .engine.on { border-color: var(--ophq-primary); box-shadow: 0 0 0 1px var(--ophq-primary); }
  .engine.soon { opacity: 0.62; cursor: default; }
  .ebadge { width: 30px; height: 30px; border-radius: 7px; display: grid; place-items: center; color: #fff; font-weight: 800; font-size: 0.8rem; flex: none; }
  .ename { font-weight: 600; font-size: 0.9rem; }
  .ribbon { position: absolute; top: 7px; right: -26px; transform: rotate(35deg); background: var(--ophq-warn, #f0b429); color: #10131a; font-size: 0.56rem; font-weight: 800; letter-spacing: 0.03em; padding: 2px 26px; text-transform: uppercase; }
  .workspace { margin-bottom: 1.2rem; overflow: hidden; }
  .wsbar { display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; padding: 0.7rem 1rem; border-bottom: 1px solid var(--ophq-border); }
  .workspace iframe { display: block; width: 100%; height: 72vh; min-height: 460px; border: 0; background: #0b0e13; }
  .wsempty { padding: 2.2rem 1rem; text-align: center; }
  .wsempty p { margin: 0 0 0.5rem; }
  .wsempty { text-align: left; max-width: 46rem; margin-inline: auto; }
  .wsempty .lead { font-weight: 600; font-size: 1.02rem; }
  .steps { margin: 0.6rem 0 0.8rem; padding-left: 1.2rem; color: var(--ophq-muted); }
  .steps li { margin: 0.3rem 0; line-height: 1.45; }
  .steps b { color: var(--ophq-text); }
  .steps code { font-family: ui-monospace, monospace; font-size: 0.88em;
                padding: 0.05rem 0.3rem; border-radius: 4px; border: 1px solid var(--ophq-border); }
  .pill { font-size: 0.78rem; padding: 0.12rem 0.5rem; border-radius: 999px;
          border: 1px solid var(--ophq-border); color: var(--ophq-muted); }
  .workspace.full { position: fixed; inset: 0; z-index: 200; margin: 0; border-radius: 0; display: flex; flex-direction: column; }
  .workspace.full iframe { flex: 1; height: auto; min-height: 0; }
  .status { margin-bottom: 1.2rem; }
  .status h3 { margin: 0; font-size: 1.1rem; }
  .status p { margin: 0.2rem 0 0; font-size: 0.9rem; }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--ophq-faint); box-shadow: 0 0 0 4px rgba(127,140,161,0.15); }
  .dot.on { background: var(--ophq-success); box-shadow: 0 0 0 4px rgba(53,196,107,0.18); }
  .two { grid-template-columns: 1fr 1fr; }
  .chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.7rem; }
  .how { margin-top: 1.2rem; }
  .how p { margin: 0.5rem 0 0; }
  @media (max-width: 820px) { .two { grid-template-columns: 1fr; } }
</style>
