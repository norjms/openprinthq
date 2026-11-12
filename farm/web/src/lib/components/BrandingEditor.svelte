<script>
  // Site branding editor (owner only, under Settings, Global admin).
  // Branding is stored in the per-user appearance config; the owner's config is
  // exposed as the public site branding. Edits preview live across the app and
  // save through the same path as the appearance settings.
  import { get } from 'svelte/store';
  import { appearance, saveAppearance as persist } from '$lib/stores/appearance';
  import { normalizeConfig, applyAppearance } from '$lib/theme';

  let draft = $state(clone(get(appearance)));
  let saving = $state(false);
  let msg = $state(null);
  let dirty = $state(false);
  let logoErr = $state(null);
  let logoNote = $state(null);

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  // Live-preview the draft across the whole app (no cookie/server write yet).
  function preview() {
    const norm = normalizeConfig(draft);
    applyAppearance(norm);
    appearance.set(norm);
    dirty = true;
    msg = null;
  }

  function setBrand(key, v) { draft.branding = { ...draft.branding, [key]: v }; preview(); }
  // The logo shown on the glass theme (stored in the dark slot; the older
  // light and accessible slots are cleared with it so they cannot show instead).
  function setLogo(v) {
    draft.branding = { ...draft.branding, logo: '', logos: { light: '', dark: v, accessible: '' } };
    preview();
  }
  const logoNow = $derived(draft.branding.logos.dark || draft.branding.logos.light || draft.branding.logos.accessible || draft.branding.logo || '');

  // The server caps each stored image at 512 KB of data-URI *string* (MAX_IMG).
  // Aim a little under that so we never trip the server limit after base64.
  const IMG_URI_LIMIT = 512 * 1024;
  const IMG_URI_TARGET = 500 * 1024;
  const IMG_MAX_DIM = 1024; // largest edge, plenty for a logo/favicon

  function readDataURL(file) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.onerror = () => rej(new Error('read'));
      r.readAsDataURL(file);
    });
  }
  function loadImage(src) {
    return new Promise((res, rej) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => rej(new Error('decode'));
      im.src = src;
    });
  }

  // Return a data-URI that fits IMG_URI_LIMIT. Images already small enough pass
  // through untouched (original format/quality preserved). Oversized images are
  // drawn to a canvas, scaled down and re-encoded (WebP with alpha, PNG fallback)
  // until they fit, so the user never has to shrink an image by hand.
  async function resampleToFit(file) {
    const original = await readDataURL(file);
    if (original.length <= IMG_URI_LIMIT) return { uri: original, resampled: false };
    const img = await loadImage(original);
    const iw = img.naturalWidth || img.width || 1;
    const ih = img.naturalHeight || img.height || 1;
    const base = Math.min(1, IMG_MAX_DIM / Math.max(iw, ih));
    let best = null;
    for (let i = 0; i < 12; i++) {
      const s = base * Math.pow(0.85, i);
      const w = Math.max(1, Math.round(iw * s));
      const h = Math.max(1, Math.round(ih * s));
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const ctx = c.getContext('2d');
      ctx.clearRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
      let cand = c.toDataURL('image/webp', 0.9);
      if (cand.startsWith('data:image/webp')) {
        let q = 0.9;
        while (cand.length > IMG_URI_TARGET && q > 0.4) { q -= 0.15; cand = c.toDataURL('image/webp', q); }
      } else {
        cand = c.toDataURL('image/png'); // no WebP: alpha-safe PNG
      }
      best = cand;
      if (cand.length <= IMG_URI_TARGET) break;
    }
    if (!best || best.length > IMG_URI_LIMIT) throw new Error('too-large');
    return { uri: best, resampled: true };
  }

  // Read an uploaded image as a data-URI (resampled if too big for the store).
  // target is 'favicon' or 'logo'.
  async function onImage(target, ev, label) {
    logoErr = null; logoNote = null;
    const file = ev.target.files?.[0];
    ev.target.value = '';
    if (!file) return;
    if (!/^image\//.test(file.type)) { logoErr = 'Please choose an image file.'; return; }
    try {
      const { uri, resampled } = await resampleToFit(file);
      if (target === 'favicon') setBrand('favicon', uri); else setLogo(uri);
      if (resampled) logoNote = `${label || 'Image'} was large, so it was resampled to fit.`;
    } catch (e) {
      logoErr = e?.message === 'too-large'
        ? `${label || 'Image'} is too detailed to fit even after resampling. Try a simpler or smaller image.`
        : 'Could not read that image.';
    }
  }

  async function save() {
    saving = true; msg = null;
    try { await persist(draft); dirty = false; msg = { ok: true, text: 'Branding saved.' }; }
    catch (e) { msg = { ok: false, text: e.message || 'Could not save.' }; }
    finally { saving = false; }
  }
  function revert() {
    draft = clone(get(appearance));
    applyAppearance(normalizeConfig(draft));
    dirty = false; msg = null; logoErr = null; logoNote = null;
  }
</script>

<div class="card card-pad epanel">
  <div class="be-head">
    <div>
      <span class="eyebrow">Branding</span>
      <p class="muted tiny">Your logo, name and tagline appear across the app, printed reports and labels, browser tabs, and the public landing page. Changes preview live; nothing is shared with other users until you save.</p>
    </div>
    <div class="be-actions">
      <button class="btn btn-ghost btn-sm" onclick={revert} disabled={!dirty && !saving}>Revert</button>
      <button class="btn btn-primary btn-sm" onclick={save} disabled={saving}>{saving ? 'Saving' : 'Save branding'}</button>
    </div>
  </div>
  {#if msg}<p class={msg.ok ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}

  <div class="brandgrid">
    <div class="field">
      <label for="bname">Site name</label>
      <input id="bname" class="input" type="text" value={draft.branding.siteName}
             oninput={(e) => setBrand('siteName', e.target.value)} placeholder="ArtisanHub Farm" />
    </div>
    <div class="field">
      <label for="bword">Product name override <span class="muted">(optional)</span></label>
      <input id="bword" class="input" type="text" value={draft.branding.wordmark}
             oninput={(e) => setBrand('wordmark', e.target.value)} placeholder="defaults to site name" />
    </div>
    <div class="field span2">
      <label for="btag">Tagline</label>
      <input id="btag" class="input" type="text" value={draft.branding.tagline}
             oninput={(e) => setBrand('tagline', e.target.value)} placeholder="One command center for every 3D printer." />
    </div>
    <div class="field span2">
      <label for="btm">Trademark or legal line <span class="muted">(footer)</span></label>
      <input id="btm" class="input" type="text" value={draft.branding.trademark}
             oninput={(e) => setBrand('trademark', e.target.value)} placeholder="© 2026 Your Company. YourBrand is a trademark of Your Company." />
    </div>
    <div class="field span2">
      <label for="bcontact">Contact info <span class="muted">(footer: email, URL or address for this host)</span></label>
      <input id="bcontact" class="input" type="text" value={draft.branding.contact}
             oninput={(e) => setBrand('contact', e.target.value)} placeholder="support@yourfarm.example" />
    </div>

    <div class="field">
      <span class="flabel">Logo <span class="muted">(PNG or SVG, shown on the dark glass; large images are resampled to fit)</span></span>
      <div class="upload">
        <span class="logoprev">
          {#if logoNow}<img src={logoNow} alt="Logo preview" />{:else}<span class="muted tiny">Default</span>{/if}
        </span>
        <div class="ubtns">
          <label class="btn btn-ghost btn-sm file"><input type="file" accept="image/*" onchange={(e) => onImage('logo', e, 'Logo')} />Upload</label>
          {#if logoNow}<button class="btn btn-ghost btn-sm" onclick={() => setLogo('')}>Remove</button>{/if}
        </div>
      </div>
    </div>
    <div class="field">
      <span class="flabel">Favicon <span class="muted">(resampled to fit)</span></span>
      <div class="upload">
        <span class="logoprev sm">
          {#if draft.branding.favicon}<img src={draft.branding.favicon} alt="Current favicon preview" />{:else}<span class="muted tiny">Default</span>{/if}
        </span>
        <div class="ubtns">
          <label class="btn btn-ghost btn-sm file"><input type="file" accept="image/*" onchange={(e) => onImage('favicon', e, 'Favicon')} />Upload</label>
          {#if draft.branding.favicon}<button class="btn btn-ghost btn-sm" onclick={() => setBrand('favicon', '')}>Remove</button>{/if}
        </div>
      </div>
    </div>
  </div>
  {#if logoErr}<p class="err">{logoErr}</p>{/if}
  {#if logoNote}<p class="ok-msg tiny">{logoNote}</p>{/if}
</div>

<style>
  .epanel { margin: 0; }
  .be-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
  .be-head p { margin: 0.3rem 0 0.9rem; max-width: 72ch; }
  .be-actions { display: flex; gap: 0.5rem; flex-shrink: 0; }
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .tiny { font-size: 0.82rem; }
  .span2 { grid-column: 1 / -1; }
  .brandgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.9rem 1rem; }
  .brandgrid .span2 { grid-column: 1 / -1; }
  .brandgrid .field { margin: 0; }
  .flabel { display: block; font-size: 12px; color: var(--ah-text-2); margin-bottom: 6px; font-weight: 500; }
  .upload { display: flex; align-items: center; gap: 0.8rem; }
  .logoprev { display: inline-grid; place-items: center; width: 88px; height: 44px; border-radius: 10px; border: 1px solid var(--ah-panel-line); background: var(--ah-panel); overflow: hidden; }
  .logoprev.sm { width: 44px; }
  .logoprev img { max-width: 100%; max-height: 100%; object-fit: contain; }
  .ubtns { display: flex; gap: 0.4rem; }
  .file { position: relative; overflow: hidden; }
  .file input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
  @media (max-width: 900px) {
    .brandgrid { grid-template-columns: 1fr; }
  }
</style>
