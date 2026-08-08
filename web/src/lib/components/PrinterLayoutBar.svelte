<script>
  // Toolbar shown while arranging the printer page's sections: where the layout
  // should apply, and Save / Cancel / Reset.
  // SPDX-License-Identifier: AGPL-3.0-or-later

  let {
    scope = 'global',          // 'global' = every printer, 'printer' = this one only
    printerName = 'this printer',
    hasOverride = false,       // this printer already has its own saved layout
    saving = false,
    msg = null,                // { ok: boolean, text: string }
    onscope = () => {}, onsave = () => {}, oncancel = () => {}, onreset = () => {}
  } = $props();
</script>

<div class="lbar card card-pad">
  <div class="lb-top">
    <div class="lb-lead">
      <span class="eyebrow">Arranging sections</span>
      <p class="muted tiny">
        Use ↑ ↓ to change the order and untick <b>Show</b> to hide a section.
        Nothing is saved until you choose Save.
      </p>
    </div>
    <div class="lb-actions">
      <button class="btn btn-ghost btn-sm" onclick={onreset} disabled={saving}>Reset layout</button>
      <button class="btn btn-ghost btn-sm" onclick={oncancel} disabled={saving}>Cancel</button>
      <button class="btn btn-primary btn-sm" onclick={onsave} disabled={saving}>
        {saving ? 'Saving…' : 'Save layout'}
      </button>
    </div>
  </div>

  <fieldset class="lb-scope">
    <legend class="tglabel">Apply to</legend>
    <label class="sopt" class:on={scope === 'global'}>
      <input type="radio" name="layout-scope" value="global"
             checked={scope === 'global'} onchange={() => onscope('global')} />
      <span>
        Every printer
        <span class="muted tiny block">
          Saves as your default layout.{#if hasOverride} Also clears the layout saved just for {printerName}.{/if}
        </span>
      </span>
    </label>
    <label class="sopt" class:on={scope === 'printer'}>
      <input type="radio" name="layout-scope" value="printer"
             checked={scope === 'printer'} onchange={() => onscope('printer')} />
      <span>
        Only {printerName}
        <span class="muted tiny block">
          Saves an override for this printer. Your default layout is left alone.
        </span>
      </span>
    </label>
  </fieldset>

  {#if msg}<p class={msg.ok ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}
</div>

<style>
  .lbar { margin-bottom: 1.2rem; border-color: color-mix(in srgb, var(--ophq-primary) 45%, var(--ophq-border)); }
  .lb-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }
  .lb-lead p { margin: 0.3rem 0 0; max-width: 62ch; }
  .lb-actions { display: flex; gap: 0.5rem; flex-shrink: 0; }

  .lb-scope { border: 0; margin: 0.9rem 0 0; padding: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
  @media (max-width: 720px) { .lb-scope { grid-template-columns: 1fr; } }
  .tglabel { font-size: 0.78rem; font-weight: 700; color: var(--ophq-text-2); text-transform: uppercase; letter-spacing: 0.08em; padding: 0; margin-bottom: 0.4rem; }
  .sopt {
    display: flex; align-items: flex-start; gap: 0.5rem; cursor: pointer;
    padding: 0.55rem 0.7rem; border: 1px solid var(--ophq-border);
    border-radius: var(--radius-sm); background: var(--ophq-bg-2); font-size: 0.88rem;
  }
  .sopt.on { border-color: var(--ophq-primary); background: var(--ophq-primary-dim); }
  .sopt input { width: auto; margin-top: 0.15rem; accent-color: var(--ophq-primary); }
  .tiny { font-size: 0.8rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; margin: 0.8rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; margin: 0.8rem 0 0; }
</style>
