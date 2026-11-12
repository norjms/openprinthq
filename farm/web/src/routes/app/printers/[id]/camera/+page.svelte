<script>
  import Icon from '$lib/components/AppIcon.svelte';
  // ArtisanHub Farm, standalone fullscreen camera view, opened in its own tab.
  // Intentionally minimal: just the live stream and a close button that closes
  // this tab. No zoom, no overlays, the tab that launched it is never touched.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { page } from '$app/stores';
  import CameraStream from '$lib/components/CameraStream.svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';

  const id = $derived($page.params.id);
  let tick = $state(0);
  let unavailable = $state(false);

  // WebRTC updates itself live, so this tick only drives the snapshot fallback.
  // At 5s that fallback pulled a full JPEG from the printer through the
  // connector and the control-plane twelve times a minute for what is a still
  // image, which is most of the bandwidth this design set out to avoid.
  $effect(() => {
    const t = setInterval(() => (tick += 1), 60000);
    return () => clearInterval(t);
  });

  function closeTab() {
    // This view is always opened via window.open(), so it can close itself.
    window.close();
  }
</script>

<PageTitle page="Camera" />

<div class="cam-full">
  <button class="cam-x" type="button" onclick={closeTab} aria-label="Close camera tab" title="Close tab"><Icon name="x" size={18} /></button>
  {#if unavailable}
    <div class="cam-msg">Camera unavailable. The printer may be offline or the stream is not reachable.</div>
  {:else}
    <CameraStream printerId={id} {tick} mode="contain" alt="printer camera live view"
                  onerror={() => (unavailable = true)} />
  {/if}
</div>

<style>
  /* Full-viewport black stage; escape the app shell's padding/max-width. */
  :global(body:has(.cam-full)) { margin: 0; }
  .cam-full {
    position: fixed; inset: 0; z-index: 100;
    background: var(--ah-page); display: flex; align-items: center; justify-content: center;
    overflow: hidden;
  }
  .cam-full :global(video), .cam-full :global(img) {
    max-width: 100vw; max-height: 100vh; width: auto; height: auto; object-fit: contain;
    border: 0; border-radius: 0; box-shadow: none; background: transparent;
  }
  /* Hide the snapshot fallback's "reconnecting" chip / skeleton frame in the
     fullscreen tab, the stream fills the whole viewport on its own. */
  .cam-full :global(.recon) { display: none; }
  .cam-full :global(.wrap) { max-width: 100vw; max-height: 100vh; }
  .cam-x {
    position: fixed; top: 1rem; right: 1rem; z-index: 101;
    width: 44px; height: 44px; border-radius: 50%;
    background: rgba(20, 30, 38, 0.7); color: var(--ah-text); border: 1px solid var(--ah-field-line);
    font-size: 1.2rem; line-height: 1; cursor: pointer; display: flex; align-items: center; justify-content: center;
    backdrop-filter: blur(6px);
  }
  .cam-x:hover { border-color: rgba(255, 255, 255, 0.5); }
  .cam-msg { color: var(--ah-text-2); font-size: 15px; padding: 16px 22px; text-align: center; }
</style>
