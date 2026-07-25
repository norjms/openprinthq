<script>
  // OpenPrintHQ — per-printer detail & control
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { api } from '$lib/api';

  const id = $derived($page.params.id);

  let loading = $state(true);
  let error = $state(null);       // 'not-found' | 'no-instance' | string
  let st = $state(null);          // PrinterStatus
  let meta = $state(null);        // static printer record
  let acting = $state(null);      // name of the control currently in flight
  let confirmStop = $state(false);
  let targets = $state({});       // editable temperature targets, keyed by kind
  let timer = null;

  // ---- live camera (polled snapshot through the engine gateway) ----
  let camTick = $state(0);
  let camAvailable = $state(true);
  const camSrc = $derived(`/api/engine/api/v1/printers/${id}/camera/snapshot?t=${camTick}`);

  async function loadStatus(initial = false) {
    if (initial) { loading = true; error = null; }
    try {
      st = await api.printerStatus(id);
      error = null;
    } catch (e) {
      if (e.status === 404) error = 'not-found';
      else if (e.status === 409) error = 'no-instance';
      else error = e.message ||'engine unreachable';
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    timer = setInterval(() => {
      if (acting) return;
      loadStatus(false);
      if (camAvailable) camTick++;
    }, 3000);
    return () => clearInterval(timer);
  });

  // (Re)load whenever the printer id changes — the route component is reused
  // across client-side nav between printers, so onMount alone wouldn't refresh.
  $effect(() => {
    const _id = id;
    camAvailable = true; camTick = 0;
    api.printer(_id).then((m) => (meta = m)).catch(() => {});
    loadStatus(true);
  });

  // ---- state helpers ----
  const stateStr = $derived((st?.state || (st?.connected ? 'idle' : 'offline')).toString());
  const isPrinting = $derived(/run|print/i.test(stateStr));
  const isPaused = $derived(/pause/i.test(stateStr));
  const hasJob = $derived(isPrinting || isPaused || !!st?.current_print || !!st?.subtask_name);

  function tone(s) {
    const x = s.toLowerCase();
    if (/run|print/.test(x)) return 'primary';
    if (/pause/.test(x)) return 'accent';
    if (/idle|ready|finish|online/.test(x)) return 'ok';
    if (/error|offline|fault|fail/.test(x)) return 'danger';
    return '';
  }

  function fmtEta(mins) {
    if (mins == null || mins <= 0) return null;
    const h = Math.floor(mins / 60), m = Math.round(mins % 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }

  // ---- temperature cards (flat dict: nozzle / nozzle_target, bed / bed_target, …) ----
  const tempCards = $derived.by(() => {
    const t = st?.temperatures || {};
    const kinds = [
      { kind: 'nozzle', label: 'Nozzle', key: 'nozzle' },
      { kind: 'nozzle', label: 'Nozzle 2', key: 'nozzle_2', settable: false },
      { kind: 'bed', label: 'Bed', key: 'bed' },
      { kind: 'chamber', label: 'Chamber', key: 'chamber' }
    ];
    return kinds
      .filter((k) => t[k.key] !== undefined && t[k.key] !== null)
      .map((k) => ({
        ...k,
        current: Number(t[k.key]) || 0,
        target: Number(t[`${k.key}_target`]) || 0,
        heating: !!t[`${k.key}_heating`],
        settable: k.settable !== false
      }));
  });

  // ---- actions ----
  async function control(action, label) {
    acting = label;
    try {
      await api.printerAction(id, action);
      await api.printerAction(id, 'refresh-status').catch(() => {});
      await loadStatus(false);
    } catch (e) {
      error = e.message ||`${label} failed`;
    } finally {
      acting = null; confirmStop = false;
    }
  }

  async function setTemp(kind, key) {
    const v = targets[key];
    if (v === undefined || v === '') return;
    acting = `set-${key}`;
    try {
      await api.setTemp(id, kind, Number(v));
      targets[key] = '';
      await loadStatus(false);
    } catch (e) {
      error = e.message ||'set temperature failed';
    } finally {
      acting = null;
    }
  }

  async function toggleConnection() {
    await control(st?.connected ? 'disconnect' : 'connect', st?.connected ? 'disconnect' : 'connect');
  }
</script>

<svelte:head><title>{st?.name || meta?.name || 'Printer'} · OpenPrintHQ</title></svelte:head>

<div class="head">
  <a href="/app/printers" class="btn btn-ghost btn-sm">← Printers</a>
  <button class="btn btn-ghost btn-sm" onclick={() => control('refresh-status', 'refresh')} disabled={!!acting}>
    {acting === 'refresh' ? 'Refreshing…' : 'Refresh'}
  </button>
</div>

{#if loading}
  <div class="card card-pad muted">Connecting to your printer…</div>
{:else if error === 'not-found'}
  <div class="card card-pad">
    <h3>Printer not found</h3>
    <p class="muted">This printer may have been removed. <a href="/app/printers">Back to printers</a>.</p>
  </div>
{:else if error === 'no-instance'}
  <div class="card card-pad">
    <h3>No instance yet</h3>
    <p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p>
  </div>
{:else}
  {#if error}<p class="err banner">{error}</p>{/if}

  <div class="title">
    <div>
      <h1>{st?.name || meta?.name || 'Printer'}</h1>
      <div class="meta mono">
        {#if meta?.model}<span>{meta.model}</span>{/if}
        {#if meta?.connection_type}<span>{meta.connection_type}</span>{/if}
        <span>#{id}</span>
      </div>
    </div>
    <div class="flex gap center">
      <span class="chip {tone(stateStr)}">{stateStr}</span>
      <button class="btn btn-ghost btn-sm" onclick={toggleConnection} disabled={!!acting}>
        {st?.connected ? 'Disconnect' : 'Connect'}
      </button>
    </div>
  </div>

  <div class="cols">
    <!-- Current job -->
    <div class="card card-pad job">
      <h3>Current job</h3>
      {#if hasJob}
        <div class="jobname">{st?.subtask_name || st?.gcode_file || st?.current_print || 'Printing'}</div>
        <div class="bar"><div class="fill" style="width:{Math.min(100, Math.max(0, st?.progress || 0))}%"></div></div>
        <div class="jobmeta mono">
          <span>{Math.round(st?.progress || 0)}%</span>
          {#if st?.layer_num != null && st?.total_layers}<span>layer {st.layer_num}/{st.total_layers}</span>{/if}
          {#if fmtEta(st?.remaining_time)}<span>~{fmtEta(st?.remaining_time)} left</span>{/if}
        </div>
        <div class="controls flex gap">
          {#if isPrinting}
            <button class="btn btn-ghost" onclick={() => control('print/pause', 'pause')} disabled={!!acting}>
              {acting === 'pause' ? 'Pausing…' : 'Pause'}
            </button>
          {/if}
          {#if isPaused}
            <button class="btn btn-primary" onclick={() => control('print/resume', 'resume')} disabled={!!acting}>
              {acting === 'resume' ? 'Resuming…' : 'Resume'}
            </button>
          {/if}
          {#if isPrinting || isPaused}
            {#if confirmStop}
              <button class="btn btn-danger" onclick={() => control('print/stop', 'stop')} disabled={!!acting}>
                {acting === 'stop' ? 'Stopping…' : 'Confirm stop'}
              </button>
              <button class="btn btn-ghost" onclick={() => (confirmStop = false)} disabled={!!acting}>Cancel</button>
            {:else}
              <button class="btn btn-ghost danger-text" onclick={() => (confirmStop = true)} disabled={!!acting}>Stop</button>
            {/if}
          {/if}
        </div>
      {:else}
        <p class="muted">No active print. {st?.connected ? 'Printer is idle and ready.' : 'Printer is offline.'}</p>
      {/if}
    </div>

    <!-- Temperatures -->
    <div class="card card-pad temps">
      <h3>Temperatures</h3>
      {#if tempCards.length === 0}
        <p class="muted">No temperature data{st?.connected ? '' : ' — printer offline'}.</p>
      {:else}
        {#each tempCards as c}
          <div class="temp">
            <div class="tinfo">
              <span class="tlabel">{c.label}</span>
              <span class="tval mono">
                {c.current.toFixed(1)}°<span class="tgt"> / {c.target || '—'}{c.target ? '°' : ''}</span>
                {#if c.heating}<span class="chip accent heat">heating</span>{/if}
              </span>
            </div>
            {#if c.settable}
              <div class="tset">
                <input class="input" type="number" min="0" placeholder="target °C"
                       bind:value={targets[c.key]} />
                <button class="btn btn-ghost btn-sm" onclick={() => setTemp(c.kind, c.key)}
                        disabled={acting === `set-${c.key}`}>Set</button>
                {#if c.target}
                  <button class="btn btn-ghost btn-sm" onclick={() => { targets[c.key] = 0; setTemp(c.kind, c.key); }}
                          disabled={acting === `set-${c.key}`}>Off</button>
                {/if}
              </div>
            {/if}
          </div>
        {/each}
      {/if}
    </div>
  </div>

  {#if camAvailable}
    <div class="card card-pad cover">
      <h3>Camera</h3>
      <img class="cam" src={camSrc} alt="{meta?.name || 'printer'} camera live view"
           onerror={() => (camAvailable = false)} />
    </div>
  {:else if st?.cover_url}
    <div class="card card-pad cover">
      <h3>Preview</h3>
      <img src={st.cover_url} alt="print preview" />
    </div>
  {/if}
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
  .banner { margin: 0 0 1rem; }
  .title { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; margin-bottom: 1.4rem; }
  .title h1 { margin: 0 0 0.3rem; }
  .meta { display: flex; gap: 0.7rem; color: var(--ophq-muted); font-size: 0.85rem; flex-wrap: wrap; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
  @media (max-width: 720px) { .cols { grid-template-columns: 1fr; } }
  .job h3, .temps h3, .cover h3 { margin: 0 0 0.9rem; font-size: 1.05rem; }
  .jobname { font-weight: 600; margin-bottom: 0.7rem; }
  .bar { height: 10px; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; }
  .fill { height: 100%; background: linear-gradient(90deg, var(--ophq-primary), var(--ophq-primary-2)); transition: width 0.4s ease; }
  .jobmeta { display: flex; gap: 1rem; margin-top: 0.6rem; color: var(--ophq-text-2); font-size: 0.85rem; }
  .controls { margin-top: 1.1rem; }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .btn-danger:hover { background: #ff7280; color: #fff; }
  .danger-text { color: var(--ophq-danger); border-color: rgba(255,92,108,0.35); }
  .danger-text:hover { border-color: var(--ophq-danger); color: var(--ophq-danger); }
  .temp { padding: 0.7rem 0; border-bottom: 1px solid var(--ophq-border-soft); }
  .temp:last-child { border-bottom: 0; }
  .tinfo { display: flex; justify-content: space-between; align-items: center; }
  .tlabel { font-weight: 600; }
  .tval { color: var(--ophq-text); font-size: 0.95rem; }
  .tgt { color: var(--ophq-muted); }
  .heat { margin-left: 0.5rem; }
  .tset { display: flex; gap: 0.5rem; margin-top: 0.55rem; }
  .tset .input { max-width: 130px; }
  .cover { margin-top: 1.2rem; }
  .cover img { width: 100%; max-width: 640px; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); display: block; }
  .cover img.cam { background: var(--ophq-bg-2); aspect-ratio: 16 / 9; object-fit: contain; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
</style>
