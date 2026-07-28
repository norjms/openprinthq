<script>
  // Settings → Look & Feel. Lets each user pick one of four theme modes
  // (dark / light / accessible / custom), edit every colour, tune accessibility,
  // and scale text. Edits preview live across the whole app;
  // Save persists them per-user to the control-plane.
  import { get } from 'svelte/store';
  import { appearance, saveAppearance as persist } from '$lib/stores/appearance';
  import {
    MODES, TOKEN_GROUPS, TOKEN_LABELS, A11Y_OPTIONS,
    ACCESSIBLE_DEFAULT_A11Y, DEFAULT_A11Y,
    effectiveVars, normalizeConfig, applyAppearance, clampScale,
    contrastRatio, wcagRating, resolveLogo
  } from '$lib/theme';
  import ThemeMockup from './ThemeMockup.svelte';

  // Editable draft (deeply reactive). Seeded from the current saved config.
  let draft = $state(clone(get(appearance)));
  let saving = $state(false);
  let msg = $state(null);
  let dirty = $state(false);

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  // Live-preview the draft across the whole app (no cookie/server write yet).
  function preview() {
    const norm = normalizeConfig(draft);
    applyAppearance(norm);
    appearance.set(norm);
    dirty = true;
    msg = null;
  }

  function pickMode(id) {
    draft.mode = id;
    // Selecting Accessible with no prior edits turns on sensible a11y defaults.
    if (id === 'accessible' && !anyA11yOn(draft.a11y)) draft.a11y = { ...ACCESSIBLE_DEFAULT_A11Y };
    preview();
  }
  function anyA11yOn(a) { return Object.values(a || {}).some(Boolean); }

  function setToken(key, value) {
    if (!draft.overrides[draft.mode]) draft.overrides[draft.mode] = {};
    draft.overrides[draft.mode][key] = value;
    preview();
  }
  function resetMode() {
    draft.overrides[draft.mode] = {};
    preview();
  }
  function toggleA11y(key) {
    draft.a11y = { ...draft.a11y, [key]: !draft.a11y[key] };
    preview();
  }
  function setScale(v) { draft.textScale = clampScale(v); preview(); }

  async function save() {
    saving = true; msg = null;
    try { await persist(draft); dirty = false; msg = { ok: true, text: 'Look & Feel saved.' }; }
    catch (e) { msg = { ok: false, text: e.message || 'Could not save.' }; }
    finally { saving = false; }
  }
  function revert() {
    // Re-apply the last saved config from the store snapshot.
    draft = clone(get(appearance));
    applyAppearance(normalizeConfig(draft));
    dirty = false; msg = null;
  }

  // Effective vars for the currently-edited mode (preset + this mode's overrides).
  const curVars = $derived(effectiveVars(normalizeConfig(draft), draft.mode));
  const isCustom = $derived(draft.mode === 'custom');

  // Live WCAG readouts for the key foreground/background pairs.
  const checks = $derived([
    ['Body text on background', curVars['--ophq-text'], curVars['--ophq-bg']],
    ['Text on card', curVars['--ophq-text'], curVars['--ophq-surface']],
    ['Muted on card', curVars['--ophq-muted'], curVars['--ophq-surface']],
    ['Link on card', curVars['--ophq-primary-2'], curVars['--ophq-surface']],
    ['Button label on primary', '#ffffff', curVars['--ophq-primary']]
  ].map(([label, fg, bg]) => {
    const r = contrastRatio(fg, bg);
    return { label, fg, bg, ratio: r, rating: wcagRating(r) };
  }));
  const wantAAA = $derived(draft.mode === 'accessible');
</script>

<div class="lf">
  <div class="lf-head">
    <div>
      <h2>Look &amp; Feel</h2>
      <p class="muted">Theme, accessibility and text size for your account. Changes preview live; nothing is shared with other users until you save.</p>
    </div>
    <div class="lf-actions">
      <button class="btn btn-ghost btn-sm" onclick={revert} disabled={!dirty && !saving}>Revert</button>
      <button class="btn btn-primary btn-sm" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
    </div>
  </div>
  {#if msg}<p class={msg.ok ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}

  <!-- Mode picker with live mockups -->
  <div class="modes">
    {#each MODES as m}
      <button
        class="mode"
        class:sel={draft.mode === m.id}
        onclick={() => pickMode(m.id)}
        aria-pressed={draft.mode === m.id}
        aria-label={`Use ${m.name} mode`}>
        <ThemeMockup
          vars={effectiveVars(normalizeConfig(draft), m.id)}
          textScale={draft.textScale}
          brandName={draft.branding.wordmark || draft.branding.siteName}
          logo={resolveLogo(draft.branding, m.id)}
          compact={true} />
        <div class="mmeta">
          <b>{m.name}</b>
          {#if draft.mode === m.id}<span class="chip primary">Active</span>{/if}
          <small>{m.blurb}</small>
        </div>
      </button>
    {/each}
  </div>

  <div class="editor">
    <!-- Colour editing for the selected mode -->
    <div class="card card-pad epanel">
      <div class="flex between center">
        <span class="eyebrow">{MODES.find((m) => m.id === draft.mode)?.name} colours</span>
        <button class="btn btn-ghost btn-sm" onclick={resetMode}>Reset to {isCustom ? 'light base' : 'default'}</button>
      </div>
      <p class="muted tiny">{isCustom ? 'Custom mode — define everything from scratch.' : 'Every default mode is editable. Your edits are saved as overrides on top of the preset.'}</p>

      {#each TOKEN_GROUPS as g}
        <div class="tgroup">
          <span class="tglabel">{g.label}</span>
          <div class="swatches">
            {#each g.tokens as [key, label]}
              <label class="sw" title={key}>
                <input type="color" value={curVars[key]} oninput={(e) => setToken(key, e.target.value)} aria-label={label} />
                <span class="swtext">
                  <span class="swname">{label}</span>
                  <input class="hex" type="text" value={curVars[key]} spellcheck="false"
                         onchange={(e) => setToken(key, e.target.value.trim())} aria-label={`${label} hex value`} />
                </span>
              </label>
            {/each}
          </div>
        </div>
      {/each}

      <!-- WCAG contrast readouts -->
      <div class="contrast">
        <span class="tglabel">Contrast {wantAAA ? '(target: AAA ≥ 7:1)' : '(target: AA ≥ 4.5:1)'}</span>
        {#each checks as c}
          <div class="crow">
            <span class="cswatch" style={`background:${c.bg};color:${c.fg};border-color:${curVars['--ophq-border']}`}>Aa</span>
            <span class="clabel">{c.label}</span>
            <span class="cratio mono">{c.ratio ? c.ratio.toFixed(2) : '—'}:1</span>
            <span class="crate {c.rating === 'Fail' ? 'bad' : (c.rating === 'AAA' || (!wantAAA && c.rating === 'AA')) ? 'good' : 'warn'}">{c.rating || '—'}</span>
          </div>
        {/each}
      </div>
    </div>

    <!-- Accessibility + text size -->
    <div class="card card-pad epanel">
      <span class="eyebrow">Accessibility</span>
      <p class="muted tiny">Best-practice options for low vision and motor needs (WCAG). Pair well with Accessible mode.</p>
      <div class="a11y">
        {#each A11Y_OPTIONS as [key, label]}
          <label class="toggle">
            <input type="checkbox" checked={draft.a11y[key]} onchange={() => toggleA11y(key)} />
            <span>{label}</span>
          </label>
        {/each}
      </div>

      <div class="scale">
        <span class="tglabel">Text size — {Math.round(draft.textScale * 100)}%</span>
        <input type="range" min="0.85" max="1.5" step="0.05" value={draft.textScale}
               oninput={(e) => setScale(e.target.value)} aria-label="Global text size scale" />
        <div class="scaleticks mono"><span>85%</span><span>100%</span><span>150%</span></div>
      </div>
    </div>

  </div>

  <div class="lf-foot">
    <button class="btn btn-ghost btn-sm" onclick={revert} disabled={!dirty && !saving}>Revert</button>
    <button class="btn btn-primary" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save Look & Feel'}</button>
    {#if dirty}<span class="muted tiny">Unsaved changes are previewing live.</span>{/if}
  </div>
</div>

<style>
  .lf-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; margin-bottom: 0.6rem; }
  .lf-head h2 { margin: 0 0 0.2rem; font-size: 1.5rem; }
  .lf-head p { margin: 0; max-width: 62ch; font-size: 0.9rem; }
  .lf-actions { display: flex; gap: 0.5rem; flex-shrink: 0; }
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .tiny { font-size: 0.82rem; }

  .modes { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.9rem; margin: 1rem 0 1.4rem; }
  .mode { text-align: left; background: transparent; border: 2px solid var(--ophq-border); border-radius: 12px; padding: 0.5rem; cursor: pointer; transition: border 0.15s, transform 0.15s; display: flex; flex-direction: column; gap: 0.5rem; }
  .mode:hover { transform: translateY(-2px); border-color: var(--ophq-primary); }
  .mode.sel { border-color: var(--ophq-primary); box-shadow: 0 0 0 3px var(--ophq-primary-dim); }
  .mmeta { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; padding: 0 0.2rem 0.2rem; }
  .mmeta b { font-size: 0.95rem; }
  .mmeta small { flex-basis: 100%; color: var(--ophq-muted); font-size: 0.76rem; }

  .editor { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .epanel { margin: 0; }
  .epanel p { margin: 0.3rem 0 0.9rem; }

  .tgroup { margin-top: 0.9rem; }
  .tglabel { display: block; font-size: 0.78rem; font-weight: 700; color: var(--ophq-text-2); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.5rem; }
  .swatches { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
  .sw { display: flex; align-items: center; gap: 0.5rem; }
  .sw input[type=color] { width: 34px; height: 34px; padding: 0; border: 1px solid var(--ophq-border); border-radius: 8px; background: none; cursor: pointer; flex-shrink: 0; }
  .swtext { display: flex; flex-direction: column; gap: 0.1rem; min-width: 0; flex: 1; }
  .swname { font-size: 0.76rem; color: var(--ophq-text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .hex { width: 100%; font-family: var(--font-mono); font-size: 0.74rem; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); color: var(--ophq-text); border-radius: 6px; padding: 0.2rem 0.4rem; }

  .contrast { margin-top: 1.1rem; border-top: 1px solid var(--ophq-border-soft); padding-top: 0.9rem; }
  .crow { display: grid; grid-template-columns: auto 1fr auto auto; align-items: center; gap: 0.6rem; padding: 0.25rem 0; }
  .cswatch { display: inline-grid; place-items: center; width: 34px; height: 26px; border-radius: 6px; border: 1px solid; font-size: 0.72rem; font-weight: 700; }
  .clabel { font-size: 0.82rem; color: var(--ophq-text-2); }
  .cratio { font-size: 0.78rem; color: var(--ophq-muted); }
  .crate { font-size: 0.7rem; font-weight: 700; padding: 0.1rem 0.45rem; border-radius: 999px; }
  .crate.good { color: var(--ophq-success); background: color-mix(in srgb, var(--ophq-success) 12%, transparent); }
  .crate.warn { color: var(--ophq-warn); background: color-mix(in srgb, var(--ophq-warn) 14%, transparent); }
  .crate.bad { color: var(--ophq-danger); background: color-mix(in srgb, var(--ophq-danger) 14%, transparent); }

  .a11y { display: flex; flex-direction: column; gap: 0.55rem; }
  .toggle { display: flex; align-items: center; gap: 0.6rem; font-size: 0.9rem; color: var(--ophq-text-2); cursor: pointer; }
  .toggle input { width: 18px; height: 18px; accent-color: var(--ophq-primary); }
  .scale { margin-top: 1.2rem; border-top: 1px solid var(--ophq-border-soft); padding-top: 0.9rem; }
  .scale input[type=range] { width: 100%; accent-color: var(--ophq-primary); }
  .scaleticks { display: flex; justify-content: space-between; font-size: 0.7rem; color: var(--ophq-faint); margin-top: 0.2rem; }


  .lf-foot { display: flex; align-items: center; gap: 0.8rem; margin-top: 1.2rem; padding-top: 1rem; border-top: 1px solid var(--ophq-border-soft); }

  @media (max-width: 900px) {
    .modes { grid-template-columns: repeat(2, 1fr); }
    .editor { grid-template-columns: 1fr; }
    .swatches { grid-template-columns: 1fr; }
  }
</style>
