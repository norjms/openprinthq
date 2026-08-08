<script>
  // Camera cluster: title bar of real actions, then the feed.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // Only actions with something behind them get a button. Bambu Studio's bar has
  // a record control; nothing in our stack can record a stream, so there is no
  // record button here rather than a decorative one.
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import CameraStream from '$lib/components/CameraStream.svelte';

  let {
    printerId, printerName = 'printer', status = null, connected = false,
    isBambu = true, tick = 0, onerror = () => {}, onopen = () => {}
  } = $props();

  let cap = $state(null);           // { webrtc, webrtc_source, routed_via_connector, relay_available }
  let timelapse = $state(null);     // null = unknown
  let liveview = $state(true);
  let busy = $state(null);
  let msg = $state(null);

  // The control-plane can just tell us what this camera supports. The stream
  // component used to find out by attempting a negotiation and seeing if it
  // failed, which is slower and noisier in the console.
  onMount(async () => {
    try { cap = await api.cameraCapability(printerId); } catch { cap = null; }
  });

  // Timelapse state isn't in the status payload, so it starts unknown and only
  // becomes definite once the user sets it. Showing a confident "off" we can't
  // actually verify would be worse than showing nothing.
  $effect(() => {
    const ipc = status?.ipcam || status?.camera;
    if (ipc && typeof ipc === 'object') {
      if ('timelapse' in ipc) timelapse = ipc.timelapse === 'enable' || ipc.timelapse === true;
      if ('mode_bits' in ipc || 'liveview' in ipc) liveview = ipc.liveview !== 'disable' && ipc.liveview !== false;
    }
  });

  async function toggleTimelapse() {
    busy = 'timelapse'; msg = null;
    const next = !timelapse;
    try {
      await api.setTimelapse(printerId, next);
      timelapse = next;
      msg = { ok: true, text: next ? 'Timelapse recording on.' : 'Timelapse recording off.' };
    } catch (e) {
      msg = { ok: false, text: e?.message || 'Could not change the timelapse setting.' };
    } finally { busy = null; }
  }

  async function toggleLiveview() {
    busy = 'liveview'; msg = null;
    const next = !liveview;
    try {
      await api.setLiveview(printerId, next);
      liveview = next;
      msg = { ok: true, text: next
        ? 'Camera feed enabled at the printer.'
        : 'Camera feed disabled at the printer — nobody can view it until it is turned back on.' };
    } catch (e) {
      msg = { ok: false, text: e?.message || 'Could not change the camera feed.' };
    } finally { busy = null; }
  }

  function snapshot() {
    // Straight to the engine's snapshot route in a new tab; the browser handles
    // saving it. Nothing to build here.
    window.open(`/api/engine/api/v1/printers/${printerId}/camera/snapshot?t=${Date.now()}`, '_blank');
  }

  const sourceLabel = $derived(
    !cap ? '' :
    cap.webrtc ? (cap.webrtc_source === 'rtsps' ? 'live · rtsp' : 'live · mjpeg') : 'snapshots'
  );
</script>

<div class="card cam">
  <div class="chead">
    <span class="ctitle">Camera</span>
    {#if sourceLabel}<span class="src mono">{sourceLabel}</span>{/if}
    {#if cap?.routed_via_connector}<span class="src mono">via connector</span>{/if}
    <span class="sp"></span>

    <button class="ic" type="button" onclick={snapshot}
            data-tip="Save a still frame" aria-label="Save a still frame">
      <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 8h3l2-2h8l2 2h3v11H3z" /><circle cx="12" cy="13" r="3.5" />
      </svg>
    </button>

    {#if isBambu}
      <button class="ic" class:on={timelapse === true} type="button" onclick={toggleTimelapse}
              disabled={!connected || busy === 'timelapse'}
              data-tip={timelapse ? 'Timelapse recording is on' : 'Record a timelapse of prints'}
              aria-label="Toggle timelapse recording" aria-pressed={timelapse === true}>
        <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" />
        </svg>
      </button>

      <button class="ic" class:off={!liveview} type="button" onclick={toggleLiveview}
              disabled={!connected || busy === 'liveview'}
              data-tip={liveview ? 'Disable the feed at the printer (everyone loses it)' : 'Enable the camera feed at the printer'}
              aria-label="Toggle the printer's camera feed" aria-pressed={liveview}>
        <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" /><circle cx="12" cy="12" r="2.8" />
          {#if !liveview}<path d="M4 20L20 4" />{/if}
        </svg>
      </button>
    {/if}

    <button class="ic" type="button" onclick={onopen}
            data-tip="Open the camera in its own tab" aria-label="Open the camera in its own tab">
      <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 4h6v6" /><path d="M20 4l-8 8" /><path d="M19 14v5H5V5h5" />
      </svg>
    </button>
  </div>

  <div class="stage">
    <CameraStream printerId={printerId} tick={tick} mode="detail"
                  alt="{printerName} camera live view" title="Open the camera in its own tab"
                  onerror={onerror} onclick={onopen} />
  </div>

  {#if msg}<p class={msg.ok ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}
</div>

<style>
  .cam { padding: 0; overflow: hidden; }
  .chead {
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.55rem 0.75rem; border-bottom: 1px solid var(--ophq-border-soft);
  }
  .ctitle { font-size: 0.9rem; color: var(--ophq-text-2); font-weight: 600; }
  .src { font-size: 0.72rem; color: var(--ophq-muted); border: 1px solid var(--ophq-border); border-radius: 999px; padding: 0.05rem 0.45rem; white-space: nowrap; }
  .sp { flex: 1; }

  .ic {
    width: 30px; height: 30px; display: inline-grid; place-items: center;
    border-radius: var(--radius-sm); border: 1px solid transparent;
    background: transparent; color: var(--ophq-text-2); cursor: pointer;
  }
  .ic svg { width: 18px; height: 18px; }
  .ic:hover:not(:disabled) { border-color: var(--ophq-border); color: var(--ophq-text); background: var(--ophq-bg-2); }
  .ic:disabled { opacity: 0.4; cursor: default; }
  .ic:focus-visible { outline: 2px solid var(--ophq-primary); outline-offset: 1px; }
  .ic.on { color: var(--ophq-accent); border-color: color-mix(in srgb, var(--ophq-accent) 40%, transparent); background: color-mix(in srgb, var(--ophq-accent) 12%, transparent); }
  .ic.off { color: var(--ophq-danger); }

  /* Hold a 16:9 box regardless of what the feed hands back, so the card keeps
     its shape while a stream negotiates or a snapshot arrives at an odd size. */
  .stage { background: #000; display: grid; place-items: center; aspect-ratio: 16 / 9; overflow: hidden; }
  .stage :global(img), .stage :global(video) { width: 100%; height: 100%; object-fit: contain; }
  .ok-msg { color: var(--ophq-success); font-size: 0.82rem; margin: 0; padding: 0.5rem 0.75rem; }
  .err { color: var(--ophq-danger); font-size: 0.82rem; margin: 0; padding: 0.5rem 0.75rem; }
</style>
