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
  let done = false;           // negotiation finished (success or gave up)

  function cleanup() { try { pc && pc.close(); } catch { /* */ } pc = null; }
  function giveUp() { if (!live) { done = true; cleanup(); } }

  // Resolve once ICE candidates are gathered (non-trickle), capped so a slow
  // gather can't stall the handshake.
  function waitForIce(peer) {
    return new Promise((resolve) => {
      if (peer.iceGatheringState === 'complete') return resolve();
      const t = setTimeout(resolve, 2500);
      peer.addEventListener('icegatheringstatechange', () => {
        if (peer.iceGatheringState === 'complete') { clearTimeout(t); resolve(); }
      });
    });
  }

  async function start() {
    if (typeof RTCPeerConnection === 'undefined') { done = true; return; }
    try {
      pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });
      pc.addEventListener('track', (e) => {
        if (videoEl && e.streams && e.streams[0]) {
          videoEl.srcObject = e.streams[0];
          live = true;
        }
      });
      pc.addEventListener('connectionstatechange', () => {
        if (['failed', 'disconnected', 'closed'].includes(pc.connectionState)) giveUp();
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
  onDestroy(cleanup);
</script>

<!-- svelte-ignore a11y_media_has_caption -->
<video bind:this={videoEl} class={mode} class:hidden={!live} autoplay muted playsinline onclick={onclick}></video>
{#if !live}
  <CameraImg {printerId} {tick} {alt} {title} {mode} {onclick} {onerror} />
{/if}

<style>
  video.fill { width: 100%; height: 100%; object-fit: cover; display: block; cursor: pointer; }
  video.detail { width: 100%; max-width: 640px; aspect-ratio: 16 / 9; object-fit: contain; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); display: block; cursor: zoom-in; }
  video.contain { max-width: 96vw; max-height: 92vh; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); box-shadow: var(--shadow-glow); display: block; }
  .hidden { display: none; }
</style>
