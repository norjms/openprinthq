<script>
  // Printing progress: plate thumbnail, what's running, how far in, and the
  // three job controls (skip objects / pause-resume / stop).
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import SkipObjectsModal from '$lib/components/SkipObjectsModal.svelte';

  let {
    printerId, status = null, isBambu = true, acting = null,
    // Hysteresis-smoothed reachability from the page, NOT status.connected —
    // the raw flag blips for a second or two on transient MQTT reconnects and
    // this panel is the most visible thing on the page.
    online = true,
    onpause = () => {}, onresume = () => {}, onstop = () => {},
    onclearplate = () => {}, refresh = () => {}
  } = $props();

  const st = $derived(status || {});
  const stateStr = $derived((st.state || (online ? 'idle' : 'offline')).toString());
  const isPrinting = $derived(/run|print/i.test(stateStr));
  const isPaused = $derived(/pause/i.test(stateStr));
  const hasJob = $derived(isPrinting || isPaused);
  const awaitingClear = $derived(!!st.awaiting_plate_clear);
  const progress = $derived(Math.min(100, Math.max(0, Number(st.progress) || 0)));
  const jobName = $derived(st.subtask_name || st.gcode_file || st.current_print || '');

  function fmtEta(mins) {
    if (mins == null || mins <= 0) return null;
    const h = Math.floor(mins / 60), m = Math.round(mins % 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }
  const eta = $derived(fmtEta(st.remaining_time));

  // Idle reads "Ready", never the previous job's outcome — that only lingers
  // while the plate still needs clearing.
  const headline = $derived(
    !online ? 'Offline' :
    isPrinting ? 'Printing' :
    isPaused ? 'Paused' :
    awaitingClear ? 'Print finished' :
    'Ready'
  );
  const tone = $derived(
    !online ? 'danger' : isPrinting ? 'primary' : isPaused ? 'accent' : awaitingClear ? 'accent' : 'ok'
  );

  let confirmStop = $state(false);
  let skipOpen = $state(false);
</script>

<div class="card pp">
  <div class="phead"><span class="ptitle">Printing progress</span></div>

  <div class="pbody">
    <div class="thumb" class:empty={!st.cover_url}>
      {#if st.cover_url && hasJob}
        <img src={st.cover_url} alt="Preview of the current print" />
      {:else}
        <span class="cube" aria-hidden="true">◲</span>
      {/if}
    </div>

    <div class="info">
      <div class="line1">
        <span class="jobname" title={jobName}>{hasJob ? (jobName || 'Printing') : '—'}</span>
        {#if hasJob && st.layer_num != null && st.total_layers}
          <span class="mono meta">Layer {st.layer_num}/{st.total_layers}</span>
        {/if}
        {#if eta && hasJob}<span class="mono meta">~{eta} left</span>{/if}
      </div>

      <div class="state {tone}">{headline}</div>

      <div class="bar" role="progressbar" aria-valuenow={Math.round(progress)}
           aria-valuemin="0" aria-valuemax="100" aria-label="Print progress">
        <div class="fill" style="width:{hasJob ? progress : 0}%"></div>
      </div>

      <div class="line3">
        {#if hasJob}
          <span class="mono pct">{Math.round(progress)}%</span>
        {:else if awaitingClear}
          <span class="muted tiny">Clear the build plate, then mark it clear.</span>
        {:else}
          <span class="muted tiny">{online ? 'Nothing printing.' : 'Printer offline.'}</span>
        {/if}
      </div>
    </div>

    <div class="jobctl">
      {#if awaitingClear}
        <button class="btn btn-primary btn-sm" onclick={onclearplate} disabled={!online || !!acting}
                data-tip="Mark the build plate as cleared" aria-label="Mark the build plate as cleared">
          ✓ Clear plate
        </button>
      {/if}

      {#if isBambu}
        <button class="jc" type="button" onclick={() => (skipOpen = true)} disabled={!hasJob}
                data-tip="Skip individual objects on this plate" aria-label="Skip objects">
          <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8"
               stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="7" width="7" height="10" rx="1.5" />
            <rect x="14" y="7" width="7" height="10" rx="1.5" /><path d="M13 5L22 19" />
          </svg>
        </button>
      {/if}

      <button class="jc" type="button" onclick={isPaused ? onresume : onpause}
              disabled={!hasJob || !!acting}
              data-tip={isPaused ? 'Resume the print' : 'Pause the print'}
              aria-label={isPaused ? 'Resume the print' : 'Pause the print'}>
        {#if isPaused}
          <svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M8 5l11 7-11 7z" /></svg>
        {:else}
          <svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor">
            <rect x="7" y="5" width="3.4" height="14" rx="1" /><rect x="13.6" y="5" width="3.4" height="14" rx="1" />
          </svg>
        {/if}
      </button>

      {#if confirmStop}
        <button class="btn btn-danger btn-sm" onclick={() => { confirmStop = false; onstop(); }} disabled={!!acting}>
          {acting === 'stop' ? 'Stopping…' : 'Confirm'}
        </button>
        <button class="btn btn-ghost btn-sm" onclick={() => (confirmStop = false)} disabled={!!acting}>Cancel</button>
      {:else}
        <button class="jc danger" type="button" onclick={() => (confirmStop = true)} disabled={!hasJob || !!acting}
                data-tip="Stop the print (asks to confirm)" aria-label="Stop the print">
          <svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5" /></svg>
        </button>
      {/if}
    </div>
  </div>
</div>

{#if skipOpen}
  <SkipObjectsModal printerId={printerId} onclose={() => (skipOpen = false)} onskipped={refresh} />
{/if}

<style>
  .pp { padding: 0; overflow: hidden; }
  .phead { padding: 0.55rem 0.75rem; border-bottom: 1px solid var(--ophq-border-soft); }
  .ptitle { font-size: 0.9rem; color: var(--ophq-text-2); font-weight: 600; }

  .pbody { display: flex; gap: 1rem; align-items: stretch; padding: 0.9rem 0.85rem; }

  .thumb {
    width: 108px; height: 108px; flex: 0 0 auto; border-radius: var(--radius-sm);
    background: var(--ophq-bg-2); border: 1px solid var(--ophq-border);
    display: grid; place-items: center; overflow: hidden;
  }
  .thumb img { width: 100%; height: 100%; object-fit: cover; }
  .cube { font-size: 2.2rem; color: var(--ophq-muted); }

  .info { flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center; gap: 0.45rem; }
  .line1 { display: flex; align-items: baseline; gap: 0.9rem; flex-wrap: wrap; }
  .jobname { font-size: 0.95rem; color: var(--ophq-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
  .meta { font-size: 0.8rem; color: var(--ophq-muted); white-space: nowrap; }
  .state { font-weight: 700; font-size: 1.05rem; }
  .state.ok { color: var(--ophq-success); }
  .state.primary { color: var(--ophq-primary-2); }
  .state.accent { color: var(--ophq-accent); }
  .state.danger { color: var(--ophq-danger); }

  .bar { height: 8px; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; }
  .fill { height: 100%; background: linear-gradient(90deg, var(--ophq-primary), var(--ophq-primary-2)); transition: width 0.4s ease; }
  .line3 { display: flex; justify-content: flex-end; }
  .pct { font-size: 0.85rem; color: var(--ophq-muted); }

  .jobctl { display: flex; align-items: center; gap: 0.4rem; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; max-width: 46%; }
  .jc {
    width: 38px; height: 38px; display: inline-grid; place-items: center;
    border-radius: var(--radius-sm); border: 1px solid var(--ophq-border);
    background: var(--ophq-bg-2); color: var(--ophq-text-2); cursor: pointer;
  }
  .jc svg { width: 18px; height: 18px; }
  .jc:hover:not(:disabled) { border-color: var(--ophq-primary); color: var(--ophq-text); }
  .jc:disabled { opacity: 0.4; cursor: default; }
  .jc:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: 1px; }
  .jc.danger:hover:not(:disabled) { border-color: var(--ophq-danger); color: var(--ophq-danger); }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .btn-danger:hover { background: #ff7280; color: #fff; }
  .tiny { font-size: 0.8rem; }

  @media (max-width: 640px) {
    .pbody { flex-wrap: wrap; }
    .jobctl { max-width: 100%; width: 100%; justify-content: flex-start; }
  }
</style>
