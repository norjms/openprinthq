<script>
  // Shared popup chrome: overlay, card, header, scrolling body, footer slot.
  // Extracted so the printer page's four dialogs don't each carry their own
  // copy of the same overlay CSS and Escape handling.
  // SPDX-License-Identifier: AGPL-3.0-or-later

  let {
    title, subtitle = '', width = '520px', busy = false,
    onclose = () => {}, children, footer
  } = $props();
</script>

<div class="overlay" role="presentation" onclick={() => !busy && onclose()}>
  <div class="modal card" role="dialog" aria-modal="true" aria-label={title} tabindex="-1"
       style="max-width:{width}" onclick={(e) => e.stopPropagation()}>
    <div class="mhead">
      <div>
        <h3>{title}</h3>
        {#if subtitle}<span class="muted tiny">{subtitle}</span>{/if}
      </div>
      <button class="btn btn-ghost btn-sm" onclick={() => onclose()} disabled={busy} aria-label="Close">✕</button>
    </div>
    <div class="mbody">{@render children?.()}</div>
    {#if footer}<div class="mfoot">{@render footer()}</div>{/if}
  </div>
</div>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape' && !busy) onclose(); }} />

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 60; display: flex; align-items: center; justify-content: center;
    background: rgba(0,0,0,0.55); backdrop-filter: blur(3px); padding: 1rem;
  }
  .modal { width: 100%; padding: 0; overflow: hidden; display: flex; flex-direction: column; max-height: calc(100vh - 2rem); }
  .mhead {
    display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem;
    padding: 1rem 1.1rem 0.8rem; border-bottom: 1px solid var(--ophq-border-soft);
  }
  .mhead h3 { margin: 0; font-size: 1.05rem; }
  .mbody { padding: 1rem 1.1rem; overflow-y: auto; }
  .mfoot {
    display: flex; justify-content: flex-end; gap: 0.5rem;
    padding: 0.8rem 1.1rem; border-top: 1px solid var(--ophq-border-soft); background: var(--ophq-bg-2);
  }
  .tiny { font-size: 0.8rem; }
</style>
