<script>
  // Settings, Appearance. The look is the shared glass theme; each user can
  // choose transparency, text size and accessibility aids. The sidebar is the
  // hub's, so it is arranged there. Edits preview live; Save stores them for
  // this account.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { get } from 'svelte/store';
  import { Toggle } from 'artisanhub-ui';
  import { appearance, saveAppearance as persist } from '$lib/stores/appearance';
  import { TRANSPARENCY, A11Y_OPTIONS, normalizeConfig, applyAppearance, clampScale } from '$lib/theme';

  let draft = $state(clone(get(appearance)));
  let saving = $state(false);
  let msg = $state(null);
  let dirty = $state(false);

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  function preview() {
    const norm = normalizeConfig(draft);
    applyAppearance(norm);
    appearance.set(norm);
    dirty = true;
    msg = null;
  }
  function pickTransparency(id) { draft.transparency = id; preview(); }
  function setA11y(key, on) { draft.a11y = { ...draft.a11y, [key]: on }; preview(); }
  function setScale(v) { draft.textScale = clampScale(v); preview(); }

  async function save() {
    saving = true; msg = null;
    try { await persist({ ...draft, nav: get(appearance).nav }); dirty = false; msg = { ok: true, text: 'Appearance saved.' }; }
    catch (e) { msg = { ok: false, text: e.message || 'Could not save.' }; }
    finally { saving = false; }
  }
  function revert() {
    draft = clone(get(appearance));
    applyAppearance(normalizeConfig(draft));
    dirty = false; msg = null;
  }
</script>

<div class="lf">
  <div class="lf-head">
    <div>
      <h2>Appearance</h2>
      <p class="muted">Transparency, text size and accessibility for your account. Changes preview live and are kept when you save.</p>
    </div>
    <div class="lf-actions">
      <button class="btn btn-ghost btn-sm" onclick={revert} disabled={!dirty && !saving}>Revert</button>
      <button class="btn btn-primary btn-sm" onclick={save} disabled={saving}>{saving ? 'Saving' : 'Save appearance'}</button>
    </div>
  </div>
  {#if msg}<p class={msg.ok ? 'ok-msg' : 'err'} role="status">{msg.text}</p>{/if}

  <div class="editor">
    <div class="card card-pad epanel">
      <span class="eyebrow">Glass</span>
      <p class="muted tiny">Panels are glass over the page. Solid panels are easier to read for some people and cost less to draw.</p>
      <div class="ah-seg" role="group" aria-label="Transparency">
        {#each TRANSPARENCY as t (t.id)}
          <button type="button" aria-pressed={draft.transparency === t.id} onclick={() => pickTransparency(t.id)}>{t.label}</button>
        {/each}
      </div>

      <div class="scale">
        <label class="tglabel" for="lf-scale">Text size, {Math.round(draft.textScale * 100)}%</label>
        <input id="lf-scale" type="range" min="0.85" max="1.5" step="0.05" value={draft.textScale}
               oninput={(e) => setScale(e.target.value)} />
        <div class="scaleticks mono"><span>85%</span><span>100%</span><span>150%</span></div>
      </div>
    </div>

    <div class="card card-pad epanel">
      <span class="eyebrow">Accessibility</span>
      <p class="muted tiny">Options for low vision and motor needs (WCAG).</p>
      <div class="a11y">
        {#each A11Y_OPTIONS as [key, label] (key)}
          <div class="toggle">
            <Toggle checked={!!draft.a11y[key]} {label} onchange={(on) => setA11y(key, on)} />
            <span>{label}</span>
          </div>
        {/each}
      </div>
    </div>
  </div>

</div>

<style>
  .lf { display: flex; flex-direction: column; gap: 14px; }
  .lf-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
  .lf-head h2 { margin: 0 0 4px; }
  .lf-head p { margin: 0; max-width: 62ch; }
  .lf-actions { display: flex; gap: 8px; flex-shrink: 0; }
  .ok-msg { color: var(--ophq-success); margin: 0; }
  .err { color: var(--ophq-danger); margin: 0; }
  .tiny { font-size: 13px; }
  .editor { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .epanel { margin: 0; display: flex; flex-direction: column; gap: 10px; }
  .epanel p { margin: 0; }
  .tglabel { display: block; font-size: 12px; font-weight: 600; color: var(--ah-text-2); margin-bottom: 6px; }
  .a11y { display: flex; flex-direction: column; gap: 10px; }
  .toggle { display: flex; align-items: center; gap: 12px; color: var(--ah-text-2); }
  .scale { margin-top: 6px; border-top: 1px solid var(--ah-row-line); padding-top: 12px; }
  .scale input[type=range] { width: 100%; accent-color: var(--ah-accent); }
  .scaleticks { display: flex; justify-content: space-between; font-size: 12px; color: var(--ah-text-3); margin-top: 2px; }
  @media (max-width: 900px) { .editor { grid-template-columns: 1fr; } }
</style>
