<script>
  // Live camera via WebRTC (peer-to-peer to go2rtc, co-located with the
  // printers) with graceful fallback to the cached-snapshot path.
  //
  // Flow: the browser makes a WebRTC offer, exchanges it with go2rtc through a
  // tiny control-plane signaling passthrough, and the video then streams
  // browser<->go2rtc DIRECTLY — the control-plane never carries video. While
  // WebRTC negotiates (and if it can't connect — remote NAT/CGNAT until TURN is
  // added), the cached snapshot from CameraImg shows instantly, so the feed is
  // always responsive and never blank.
  import { onMount, onDestroy } from 'svelte';
  import CameraImg from './CameraImg.svelte';

  let {
    printerId, tick = 0, alt = 'camera', title = '',
    mode = 'fill', onclick = null, onerror = null,
  } = $props();

  let videoEl;
  let pc = null;
  let live = $state(false);   // true once WebRTC media is playing
  let attempts = $state(0);   // how many times we've tried to go live
  let retryTimer = null;
  let destroyed = false;

  function cleanup() { try { pc && pc.close(); } catch { /* */ } pc = null; }

  // Falling back is not a permanent verdict. A failed negotiation usually means
  // the connector was momentarily away or go2rtc was restarting, and giving up
  // for the life of the page left the viewer on relayed snapshots -- paying the
  // bandwidth this design exists to avoid -- until they happened to reload.
  // Retry on a slow backoff so a transient failure costs one interval, not the
  // whole session, while a genuinely unreachable peer is not hammered.
  function scheduleRetry() {
    if (destroyed || live || retryTimer) return;
    const delay = Math.min(15000 * Math.max(1, attempts), 120000);
    retryTimer = setTimeout(() => { retryTimer = null; start(); }, delay);
  }

  function giveUp() {
    if (live) return;
    cleanup();
    scheduleRetry();
  }

  // Resolve once ICE candidates are gathered (non-trickle), capped so a slow
  // gather can't stall the handshake.
  function waitForIce(peer) {
    return new Promise((resolve) => {
      if (peer.iceGatheringState === 'complete') return resolve();
      // 2.5s was enough when only host and STUN candidates were in play. TURN
      // allocation adds a round trip to Cloudflare, and on the CGNAT networks
      // where the relay is the ONLY workable path, cutting gathering short
      // discards the candidate that would actually have connected.
      const t = setTimeout(resolve, 6000);
      peer.addEventListener('icegatheringstatechange', () => {
        if (peer.iceGatheringState === 'complete') { clearTimeout(t); resolve(); }
      });
    });
  }

  async function start() {
    if (destroyed || live) return;
    if (typeof RTCPeerConnection === 'undefined') return;   // no retry: never going to work
    attempts += 1;
    cleanup();
    try {
      // Fresh ICE servers (STUN + short-lived TURN for remote/CGNAT) from the
      // control-plane; STUN-only fallback if that call fails.
      let ice = [{ urls: 'stun:stun.cloudflare.com:3478' }, { urls: 'stun:stun.l.google.com:19302' }];
      try {
        const r = await fetch('/api/camera/ice', { credentials: 'include' });
        if (r.ok) { const j = await r.json(); if (Array.isArray(j.iceServers) && j.iceServers.length) ice = j.iceServers; }
      } catch { /* keep STUN default */ }
      pc = new RTCPeerConnection({ iceServers: ice });
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });
      pc.addEventListener('track', (e) => {
        if (videoEl && e.streams && e.streams[0]) {
          videoEl.srcObject = e.streams[0];
          live = true;
          attempts = 0;                       // reset the backoff after success
          if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
        }
      });
      pc.addEventListener('connectionstatechange', () => {
        if (['failed', 'disconnected', 'closed'].includes(pc.connectionState)) {
          // A stream that drops mid-view should also come back on its own.
          live = false;
          giveUp();
        }
      });

      await pc.setLocalDescription(await pc.createOffer());
      await waitForIce(pc);

      const res = await fetch(`/api/camera/webrtc/${printerId}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ type: 'offer', sdp: pc.localDescription.sdp }),
      });
      if (!res.ok) throw new Error('signaling ' + res.status);
      const ans = await res.json();
      const sdp = ans.sdp || (typeof ans === 'string' ? ans : null);
      if (!sdp) throw new Error('no answer sdp');
      await pc.setRemoteDescription({ type: 'answer', sdp });

      // If media hasn't started shortly after negotiation, fall back.
      setTimeout(giveUp, 9000);
    } catch { giveUp(); }
  }

  onMount(start);
  onDestroy(() => { destroyed = true; if (retryTimer) clearTimeout(retryTimer); cleanup(); });
</script>

<!-- svelte-ignore a11y_media_has_caption -->
<div class="wrap {mode}">
  <video bind:this={videoEl} class={mode} class:hidden={!live} autoplay muted playsinline onclick={onclick}></video>
  {#if !live}
    <CameraImg {printerId} {tick} {alt} {title} {mode} {onclick} {onerror} />
  {/if}
  <!-- A still frame and a live stream look identical, so say which this is.
       Otherwise a snapshot up to a minute old reads as current video. -->
  <span class="badge" class:islive={live} title={live
      ? 'Live video, streaming directly from your network'
      : 'Still frames, refreshed every 60 seconds. A direct live connection could not be established.'}>
    {live ? 'LIVE' : 'SNAPSHOT'}
  </span>
</div>

<style>
  .wrap { position: relative; display: inline-block; }
  .wrap.fill { display: block; width: 100%; height: 100%; }
  .badge { position: absolute; top: 6px; right: 6px; z-index: 2;
           padding: 1px 6px; border-radius: 999px; font-size: 0.62rem;
           letter-spacing: 0.06em; font-weight: 600; pointer-events: none;
           background: rgba(0,0,0,0.6); color: #ddd; }
  .badge.islive { background: rgba(20,120,60,0.85); color: #fff; }
  video.fill { width: 100%; height: 100%; object-fit: cover; display: block; cursor: pointer; }
  video.detail { width: 100%; max-width: 640px; aspect-ratio: 16 / 9; object-fit: contain; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); display: block; cursor: pointer; }
  video.contain { max-width: 96vw; max-height: 92vh; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); box-shadow: var(--shadow-glow); display: block; }
  .hidden { display: none; }
</style>
