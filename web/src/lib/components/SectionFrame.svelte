<script>
  // Edit-mode chrome for one arrangeable section of the printer page.
  //
  // Outside edit mode this renders its children and NOTHING else — no wrapper
  // element — so an unarranged page has exactly the DOM and CSS it always had.
  // Inside edit mode each section gains an outline, its catalogue name, movers
  // and a show/hide toggle. Hidden sections collapse to a stub rather than
  // rendering their (often live, often expensive) content, but stay in the list
  // so they can be brought back.
  // SPDX-License-Identifier: AGPL-3.0-or-later

  let {
    def, hidden = false, editing = false, first = false, last = false,
    // True when the section is arrangeable for this printer but has nothing to
    // draw right now (printer offline, no camera reachable, no AMS attached).
    // It still gets a frame so it can be positioned ahead of time.
    unavailable = false,
    onmove = () => {}, ontoggle = () => {}, children
  } = $props();

  const locked = $derived(!!def?.lockHide);
</script>

{#if !editing}
  {#if !hidden}{@render children?.()}{/if}
{:else}
  <div class="secframe" class:off={hidden}>
    <div class="sf-bar">
      <span class="sf-name">{def?.label || def?.key}</span>
      {#if def?.hint}<span class="sf-hint">{def.hint}</span>{/if}
      <label class="sf-show" title={locked ? 'This section is always shown' : 'Show this section'}>
        <input type="checkbox" checked={!hidden} disabled={locked}
               onchange={() => ontoggle()} aria-label={`Show ${def?.label || def?.key}`} />
        <span>Show</span>
      </label>
      <span class="sf-movers">
        <button class="btn btn-ghost btn-xs" onclick={() => onmove(-1)} disabled={first}
                aria-label={`Move ${def?.label || def?.key} up`}>↑</button>
        <button class="btn btn-ghost btn-xs" onclick={() => onmove(1)} disabled={last}
                aria-label={`Move ${def?.label || def?.key} down`}>↓</button>
      </span>
    </div>
    {#if hidden}
      <p class="sf-stub">Hidden — tick <b>Show</b> to put it back.</p>
    {:else if unavailable}
      <p class="sf-stub">Nothing to show right now. It will appear here when there is.</p>
    {:else}
      <div class="sf-body">{@render children?.()}</div>
    {/if}
  </div>
{/if}

<style>
  .secframe {
    position: relative;
    border: 1px dashed color-mix(in srgb, var(--ophq-primary) 55%, transparent);
    border-radius: var(--radius-sm);
    padding: 0.5rem;
    background: color-mix(in srgb, var(--ophq-primary) 4%, transparent);
    margin-bottom: 1rem;
  }
  .secframe.off { border-style: dotted; opacity: 0.75; background: none; }

  .sf-bar {
    display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;
    margin-bottom: 0.5rem; padding: 0.15rem 0.15rem 0.4rem;
    border-bottom: 1px solid var(--ophq-border-soft);
  }
  .sf-name { font-size: 0.82rem; font-weight: 700; color: var(--ophq-primary-2); white-space: nowrap; }
  .sf-hint {
    flex: 1; min-width: 0; font-size: 0.76rem; color: var(--ophq-muted);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .sf-show { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.78rem; color: var(--ophq-text-2); cursor: pointer; }
  .sf-show input { width: 16px; height: 16px; accent-color: var(--ophq-primary); }
  .sf-show:has(input:disabled) { cursor: default; opacity: 0.6; }
  .sf-movers { display: inline-flex; gap: 0.25rem; }
  .btn-xs { padding: 0.1rem 0.45rem; font-size: 0.85rem; line-height: 1.3; }

  .sf-stub { margin: 0; padding: 0.35rem 0.15rem 0.2rem; font-size: 0.82rem; color: var(--ophq-muted); }

  /* Sections are inert while you arrange them: a stray click must not pause a
     print or set a temperature. */
  .sf-body { pointer-events: none; }
  /* …except the chrome of nested sections (the Bambu dashboard's own blocks),
     which still has to be clickable to arrange them. */
  .sf-body :global(.sf-bar) { pointer-events: auto; }
</style>
