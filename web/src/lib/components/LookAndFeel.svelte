<script>
  // Settings → Look & Feel. Lets each user pick one of four theme modes
  // (dark / light / accessible / custom), edit every colour, tune accessibility,
  // scale text, and brand their site. Edits preview live across the whole app;
  // Save persists them per-user to the control-plane.
  import { get } from 'svelte/store';
  import { appearance, saveAppearance as persist } from '$lib/stores/appearance';
  import {
    MODES, TOKEN_GROUPS, TOKEN_LABELS, PRESETS, A11Y_OPTIONS, LOGO_SLOTS,
    ACCESSIBLE_DEFAULT_A11Y, DEFAULT_A11Y, DEFAULT_BRANDING,
    effectiveVars, normalizeConfig, applyAppearance, clampScale,
    contrastRatio, wcagRating, resolveLogo
  } from '$lib/theme';
  import ThemeMockup from './ThemeMockup.svelte';

  // Editable draft (deeply reactive). Seeded from the current saved config.
  let draft = $state(clone(get(appearance)));
  let saving = $state(false);
  let msg = $state(null);
  let dirty = $state(false);
  let logoErr = $state(null);

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
  function setBrand(key, v) { draft.branding = { ...draft.branding, [key]: v }; preview(); }
  // Set one of the three logo slots (light / dark / accessible).
  function setLogo(slot, v) {
    draft.branding = { ...draft.branding, logos: { ...draft.branding.logos, [slot]: v } };
    preview();
  }

  // Read an uploaded image → data-URI. target is 'favicon' or a logo slot key.
  function onImage(target, ev, label) {
    logoErr = null;
    const file = ev.target.files?.[0];
    if (!file) return;
    if (file.size > 512 * 1024) { logoErr = `${label || 'Image'} must be under 512 KB.`; ev.target.value = ''; return; }
    const r = new FileReader();
    r.onload = () => { if (target === 'favicon') setBrand('favicon', r.result); else setLogo(target, r.result); };
    r.onerror = () => { logoErr = 'Could not read that image.'; };
    r.readAsDataURL(file);
    ev.target.value = '';
  }

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
    dirty = false; msg = null; logoErr = null;
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
      <p class="muted">Theme, accessibility, text size and branding for your account. Changes preview live; nothing is shared with other users until you save.</p>
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

    <!-- Branding -->
    <div class="card card-pad epanel span2">
      <span class="eyebrow">Branding</span>
      <p class="muted tiny">Your logo, name and tagline appear across the app, printed reports &amp; labels, and browser tabs.</p>
      <div class="brandgrid">
        <div class="field">
          <label for="bname">Site name</label>
          <input id="bname" class="input" type="text" value={draft.branding.siteName}
                 oninput={(e) => setBrand('siteName', e.target.value)} placeholder="OpenPrintHQ" />
        </div>
        <div class="field">
          <label for="bword">Wordmark override <span class="muted">(optional)</span></label>
          <input id="bword" class="input" type="text" value={draft.branding.wordmark}
                 oninput={(e) => setBrand('wordmark', e.target.value)} placeholder="defaults to site name" />
        </div>
        <div class="field span2">
          <label for="btag">Tagline</label>
          <input id="btag" class="input" type="text" value={draft.branding.tagline}
                 oninput={(e) => setBrand('tagline', e.target.value)} placeholder="One command center for every 3D printer." />
        </div>
        <div class="field span2">
          <label for="btm">Trademark / legal line <span class="muted">(footer)</span></label>
          <input id="btm" class="input" type="text" value={draft.branding.trademark}
                 oninput={(e) => setBrand('trademark', e.target.value)} placeholder="© 2026 Your Company. YourBrand™ is a trademark of Your Company." />
        </div>
        <div class="field span2">
          <label for="bcontact">Contact info <span class="muted">(footer — email, URL or address for this host)</span></label>
          <input id="bcontact" class="input" type="text" value={draft.branding.contact}
                 oninput={(e) => setBrand('contact', e.target.value)} placeholder="support@yourfarm.example · yourfarm.example" />
        </div>

        <div class="field span2">
          <label>Logos <span class="muted">(≤ 512 KB each, PNG/SVG — one per theme so the mark stays legible on every background)</span></label>
          <div class="logogrid">
            {#each LOGO_SLOTS as [slot, slotLabel, slotHint]}
              <div class="logoslot">
                <div class="slothead">
                  <b>{slotLabel}</b>
                  <small class="muted">{slotHint}</small>
                </div>
                <div class="upload">
                  <span class="logoprev" style={`background:${(PRESETS[slot] || PRESETS.light)['--ophq-bg-2']}`}>
                    {#if draft.branding.logos[slot]}<img src={draft.branding.logos[slot]} alt={`${slotLabel} logo preview`} />{:else}<span class="muted tiny">default</span>{/if}
                  </span>
                  <div class="ubtns">
                    <label class="btn btn-ghost btn-sm file"><input type="file" accept="image/*" onchange={(e) => onImage(slot, e, `${slotLabel} logo`)} />Upload</label>
                    {#if draft.branding.logos[slot]}<button class="btn btn-ghost btn-sm" onclick={() => setLogo(slot, '')}>Remove</button>{/if}
                  </div>
                </div>
              </div>
            {/each}
          </div>
        </div>
        <div class="field">
          <label>Favicon <span class="muted">(≤ 512 KB)</span></label>
          <div class="upload">
            <span class="logoprev sm" style={`background:${curVars['--ophq-bg-2']}`}>
              {#if draft.branding.favicon}<img src={draft.branding.favicon} alt="Current favicon preview" />{:else}<span class="muted tiny">default</span>{/if}
            </span>
            <div class="ubtns">
              <label class="btn btn-ghost btn-sm file"><input type="file" accept="image/*" onchange={(e) => onImage('favicon', e, 'Favicon')} />Upload</label>
              {#if draft.branding.favicon}<button class="btn btn-ghost btn-sm" onclick={() => setBrand('favicon', '')}>Remove</button>{/if}
            </div>
          </div>
        </div>
      </div>
      {#if logoErr}<p class="err">{logoErr}</p>{/if}
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
  .span2 { grid-column: 1 / -1; }
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

  .brandgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.9rem 1rem; }
  .brandgrid .span2 { grid-column: 1 / -1; }
  .brandgrid .field { margin: 0; }
  .logogrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.9rem; }
  .logoslot { display: flex; flex-direction: column; gap: 0.45rem; border: 1px solid var(--ophq-border-soft); border-radius: 10px; padding: 0.6rem; }
  .slothead { display: flex; flex-direction: column; gap: 0.1rem; }
  .slothead b { font-size: 0.86rem; }
  .slothead small { font-size: 0.72rem; line-height: 1.35; }
  .upload { display: flex; align-items: center; gap: 0.8rem; }
  .logoprev { display: inline-grid; place-items: center; width: 88px; height: 44px; border-radius: 8px; border: 1px solid var(--ophq-border); overflow: hidden; }
  .logoprev.sm { width: 44px; }
  .logoprev img { max-width: 100%; max-height: 100%; object-fit: contain; }
  .ubtns { display: flex; gap: 0.4rem; }
  .file { position: relative; overflow: hidden; }
  .file input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }

  .lf-foot { display: flex; align-items: center; gap: 0.8rem; margin-top: 1.2rem; padding-top: 1rem; border-top: 1px solid var(--ophq-border-soft); }

  @media (max-width: 900px) {
    .modes { grid-template-columns: repeat(2, 1fr); }
    .editor { grid-template-columns: 1fr; }
    .swatches, .brandgrid { grid-template-columns: 1fr; }
  }
</style>
