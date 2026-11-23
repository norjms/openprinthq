<script>
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { page } from '$app/state';
  import { THEMES, REDUCE, look, setLook } from '$lib/theme.svelte.js';
  import Page from '$lib/Page.svelte';
  import { loadNav } from '$lib/nav.svelte.js';
  const TABS = [['general', 'General'], ['crafts', 'Shop and crafts'], ['slicing', 'Slicing and costs'], ['sites', 'Model sites'], ['keys', 'API keys']];
  let tab = $state(TABS.some((t) => t[0] === page.url.searchParams.get('tab')) ? page.url.searchParams.get('tab') : 'general');
  function pick(t) { tab = t; const u = new URL(location.href); u.searchParams.set('tab', t); history.replaceState(history.state, '', u); }
  const farmName = 'the farm';
  let s = $state(null);
  let keys = $state([]);
  let conns = $state([]);
  const origin = typeof location !== "undefined" ? location.origin : "";
  let newKey = $state('');
  let created = $state('');
  let createdNote = $state('');
  let mw = $state(null);
  let mwf = $state({ email: '', password: '', code: '', tfa_key: null });
  let mwStep = $state('creds');
  let mwMsg = $state('');
  let mwBusy = $state(false);
  let crafts = $state(null);
  async function loadCrafts() { try { crafts = await api.get('/api/crafts/v1'); } catch { crafts = null; } }
  // The shop's own logo (docs/brand.md): the phone app draws it in place of the OpenPrintHQ wordmark.
  let logo = $state(null);
  async function loadLogo() { try { logo = (await api.get('/api/brand/v1')).wordmark; } catch { logo = null; } }
  async function uploadLogo(e) {
    const f = e.currentTarget.files?.[0];
    e.currentTarget.value = '';
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f, f.name);
    try {
      const r = await attempt(async () => {
        const res = await fetch('/api/brand/v1/wordmark', { method: 'PUT', body: fd, credentials: 'same-origin' });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail || `${res.status}`);
        return body;
      }, 'Logo saved');
      logo = r.wordmark;
    } catch {}  // attempt already said what went wrong
  }
  async function removeLogo() { if (confirm('Remove the logo? The phone app goes back to the OpenPrintHQ wordmark.')) { try { await attempt(() => api.del('/api/brand/v1/wordmark'), 'Logo removed'); logo = null; } catch {} } }
  async function toggleCraft(c) {
    const enabled = crafts.crafts.filter((x) => !x.always_on && (x.id === c.id ? !x.enabled : x.enabled)).map((x) => x.id);
    crafts = await attempt(() => api.put('/api/crafts/v1', { enabled }), `${c.label} ${c.enabled ? 'off' : 'on'}`);
    loadNav();
  }
  async function load() {
    s = await api.get('/api/settings'); mw = await api.get('/api/makerworld/status');
    try { keys = (await api.get('/api/mobile/v1/keys')).keys; conns = await api.get('/api/oauth/connections'); } catch { keys = []; conns = []; }
  }
  async function mwLogin() {
    mwBusy = true;
    try {
      const body = { email: mwf.email, password: mwStep === 'creds' ? mwf.password : null, code: mwStep !== 'creds' ? mwf.code : null, tfa_key: mwf.tfa_key };
      if (mwStep === 'code') body.password = null;
      const r = await attempt(() => api.post('/api/makerworld/login', body));
      if (r.status === 'connected') { toast(`Connected${r.name ? ' as ' + r.name : ''}`); mwf = { email: '', password: '', code: '', tfa_key: null }; mwStep = 'creds'; mwMsg = ''; load(); }
      else if (r.status === 'code_sent') { mwStep = 'code'; mwMsg = r.message; }
      else if (r.status === 'tfa') { mwStep = 'tfa'; mwf.tfa_key = r.tfa_key; mwMsg = r.message; }
    } finally { mwBusy = false; }
  }
  async function mwLogout() { if (confirm('Disconnect MakerWorld?')) { await api.del('/api/makerworld/login'); load(); } }
  let srcs = $state(null);
  let tvTok = $state('');
  let cr = $state({ email: '', password: '' });
  async function loadSrcs() { srcs = await api.get('/api/sources/status'); }
  async function tvSave() { await attempt(() => api.post('/api/sources/thingiverse/login', { token: tvTok }), 'Thingiverse connected'); tvTok = ''; loadSrcs(); }
  async function crSave() { const r = await attempt(() => api.post('/api/sources/creality/login', cr), 'Creality Cloud connected'); cr = { email: '', password: '' }; loadSrcs(); }
  let cu = $state({ email: '', token: '' });
  async function cuSave() { await attempt(() => api.post('/api/sources/cults3d/login', cu), 'Cults3D connected'); cu = { email: '', token: '' }; loadSrcs(); }
  async function srcOut(p) { if (confirm('Disconnect?')) { await api.del(`/api/sources/${p}/login`); loadSrcs(); } }
  let mp = $state({ mat: '', price: '' });
  const tzs = (() => { try { return Intl.supportedValuesOf('timeZone'); } catch { return ['UTC', 'America/Chicago', 'America/New_York', 'America/Denver', 'America/Los_Angeles', 'Europe/London']; } })();
  let bl = $state(null);
  async function loadBl() { bl = await api.get('/api/import-blocklist'); }
  async function unblock(code) { bl = await attempt(() => api.post('/api/import-blocklist/remove', { code }), `${code} can be imported again`); }
  async function recheck() { await attempt(() => api.post('/api/actions/license-recheck'), 'Checking every MakerWorld model license; you get an email if any changed'); }
  onMount(() => { load(); loadBl(); loadSrcs(); loadCrafts(); loadLogo(); });
  async function matchAll() { await attempt(() => api.post('/api/filaments/match-all'), 'Matching every model in the background'); }
  async function save() { s = await attempt(() => api.put('/api/settings', s), 'Saved'); }
  async function mk() { const r = await attempt(() => api.post('/api/keys', { name: newKey || 'client' })); created = r.key; createdNote = ''; newKey = ''; load(); }
  async function copyKey() { try { await navigator.clipboard.writeText(created); toast('Key copied'); } catch { toast('Select the key and copy it'); } }
  async function disconnect(c) { if (confirm(`Disconnect ${c.app}? It will need to sign in again.`)) { await attempt(() => api.del(`/api/oauth/connections/${c.grant}`), 'Disconnected'); load(); } }
  async function revoke(k) { if (confirm(`Revoke ${k.name}?`)) { await api.del(`/api/keys/${k.id}`); load(); } }
</script>
{#snippet sw(key, title, help, label)}
  <div class="panel ah-kv" style="padding:12px 16px"><div><div style="font-weight:600">{title}</div><div class="ah-muted">{help}</div></div>
    <button class="toggle {s[key] ? 'on' : ''}" role="switch" aria-checked={!!s[key]} aria-label={label || title} onclick={() => { s[key] = !s[key]; save(); }}></button></div>
{/snippet}

<Page title="Settings" description="Settings for this hub. Each one says whether it is yours or everyone's.">
  <div class="ah-tabs" role="tablist" aria-label="Settings">
    {#each TABS as [k, l]}<button role="tab" aria-selected={tab === k} onclick={() => pick(k)}>{l}</button>{/each}
  </div>

  {#if tab === 'crafts'}
    <section class="ah-glass ah-card" id="logo">
      <h2 class="ah-card-title">Your shop's logo</h2>
      <p class="ah-muted" style="margin-top:-6px">Everyone's. The phone app shows it in place of the OpenPrintHQ wordmark. An SVG or PNG, wide rather than tall, at most 256 KB.</p>
      <div class="ah-kv" style="gap:16px;align-items:center">
        <div class="panel" style="width:320px;height:64px;flex:none;display:grid;place-items:center;padding:8px">
          {#if logo}<img src={logo} alt="Your shop's logo" style="max-width:100%;max-height:48px" />{:else}<span class="ah-muted">No logo yet</span>{/if}
        </div>
        {#if crafts?.can_change}
          <label class="btn sm">Upload a logo<input type="file" accept="image/svg+xml,image/png" hidden onchange={uploadLogo} /></label>
          {#if logo}<button type="button" class="btn sm ghost" onclick={removeLogo}>Remove</button>{/if}
        {:else}
          <span class="ah-muted">Only the hub owner or an admin of the first team can change it.</span>
        {/if}
      </div>
    </section>
    <section class="ah-glass ah-card" id="crafts">
      <h2 class="ah-card-title">Crafts</h2>
      <p class="ah-muted" style="margin-top:-6px">What this hub is for. Everyone's: a craft that is off is left out of the sidebar for everybody. Turning one on installs nothing; the OpenPrintHQ installer adds Laser Crafting's tools when you choose it there.</p>
      {#if crafts}
        <div style="display:grid;gap:10px">
          {#each crafts.crafts as c (c.id)}
            <div class="panel ah-kv" style="padding:12px 16px"><div><div style="font-weight:600">{c.label}</div><div class="ah-muted">{c.summary}</div></div>
              {#if c.always_on}<span class="ah-muted">Always on</span>
              {:else}<button class="toggle {c.enabled ? 'on' : ''}" role="switch" aria-checked={c.enabled} aria-label={c.label} disabled={!crafts.can_change} onclick={() => toggleCraft(c)}></button>{/if}
            </div>
          {/each}
        </div>
        {#if !crafts.can_change}<p class="ah-muted">Only the hub owner or an admin of the first team can change the crafts.</p>{/if}
      {/if}
    </section>
  {/if}

  {#if tab === 'general'}
    <section class="ah-glass ah-card" id="appearance">
      <h2 class="ah-card-title">Appearance</h2>
      <p class="ah-muted" style="margin-top:-6px">Just for you, on every device you sign in on.</p>
      <div class="ah-kv" style="gap:16px">
        <div class="ah-field" style="width:280px;flex:none"><label for="theme">Theme</label>
          <select id="theme" class="input" value={look.theme} onchange={(e) => setLook(e.currentTarget.value, null)}>
            {#each Object.entries(THEMES) as [k, l]}<option value={k}>{l}</option>{/each}
          </select></div>
        <p class="ah-muted">More themes, such as light and high contrast, will be offered here.</p>
      </div>
      <div class="panel ah-kv" style="padding:12px 16px"><div><div style="font-weight:600">Reduce transparency</div><div class="ah-muted">Solid cards instead of glass. Follows your device unless set here.</div></div>
        <div class="ah-seg" role="group" aria-label="Reduce transparency">
          {#each Object.entries(REDUCE) as [k, l]}<button type="button" aria-pressed={look.reduce === k} onclick={() => setLook(null, k)}>{l}</button>{/each}
        </div></div>
    </section>
    {#if s}
      <section class="ah-glass ah-card">
        <h2 class="ah-card-title">Automation</h2>
        <p class="ah-muted" style="margin-top:-6px">For everyone in this hub. How orders become prints and stock.</p>
        {@render sw('farm_mirror_library', `Store 3MF and G-code files in ${farmName}`, "Every model's 3MF and G-code files are mirrored into the farm's library under team name, category and model.", 'mirror')}
        {@render sw('consume_parts_on_complete', 'Deduct parts when orders complete', 'Removes per-unit parts from InvenTree stock when WooCommerce marks the order completed. Filament is tracked by Spoolman.', 'consume')}
        {@render sw('hide_on_license_expiry', 'Hide products when a commercial license ends', 'When a membership you sell under passes its end date, its live products are hidden on the store automatically. Off: you get an email and decide.', 'hide on expiry')}
        {@render sw('require_approved_slice', 'Go live only with an approved slice', "A product can't go live on the store until the default printer has an approved print file.", 'require approved slice')}
      </section>
    {/if}

  {:else if tab === 'slicing' && s}
    <div class="ah-cols">
      <section class="ah-glass ah-card">
        <h2 class="ah-card-title">Slicing</h2>
        <p class="ah-muted" style="margin-top:-6px">For everyone in this hub.</p>
        {@render sw('slice_paused', 'Pause slicing', 'Queued slices wait until you turn this off.', 'pause slicing')}
        <div class="panel ah-field" style="padding:12px 16px"><label for="fb">Filament brand</label>
          <select id="fb" class="input" bind:value={s.filament_brand} onchange={save}><option value={null}>Keep the requested filaments</option><option value="Bambu Lab">Bambu Lab</option></select>
          <span class="ah-field-help">Replace each model's requested filament with this brand's closest colour (same finish when it has one). The designer's request is kept on the model.</span>
          {#if s.filament_brand}<button class="btn sm" style="align-self:flex-start" onclick={matchAll}>Match the whole library now</button>{/if}</div>
        <div class="panel ah-field" style="padding:12px 16px"><label for="sa">Slice new models automatically</label>
          <select id="sa" class="input" bind:value={s.slice_auto_new} onchange={save}>
            <option value="off">Off</option><option value="default">For the default printer</option><option value="all">For every active printer</option>
          </select>
          <span class="ah-field-help">When a model is imported, slice it right away. The result still waits for review.</span></div>
        {@render sw('slice_nightly', 'Re-slice out-of-date slices overnight', 'After a slicer upgrade or a printer change, slices that changed are redone at night.', 'nightly re-slice')}
        {#if s.slice_nightly}
          <div class="grid grid-cols-2 gap-3">
            <div class="ah-field"><label for="nh">Starts at (hour)</label><input id="nh" class="input" type="number" min="0" max="23" bind:value={s.slice_night_hour} onchange={() => { s.slice_night_hour = Number(s.slice_night_hour); save(); }} /></div>
            <div class="ah-field"><label for="tz">Time zone</label><input id="tz" class="input" list="tzs" bind:value={s.timezone} onchange={save} placeholder="America/Chicago" />
              <datalist id="tzs">{#each tzs as z}<option value={z}></option>{/each}</datalist></div>
          </div>
        {/if}
        {@render sw('slice_auto_approve_clean', 'Approve slices that finish without problems', 'Skip review for any slice with no problems (missing tools or materials, failed prints). Slices with problems still wait for you.', 'auto approve clean slices')}
        {@render sw('slice_auto_approve', 'Approve re-slices that barely changed', `Skip review when an approved slice is redone and print time and grams stay within ${s.slice_auto_approve_pct}%.`, 'auto approve')}
        {#if s.slice_auto_approve}<div class="ah-field" style="width:160px"><label for="tol">Tolerance %</label><input id="tol" class="input" type="number" min="0" max="50" bind:value={s.slice_auto_approve_pct} onchange={() => { s.slice_auto_approve_pct = Number(s.slice_auto_approve_pct); save(); }} /></div>{/if}
      </section>
      <section class="ah-glass ah-card">
        <h2 class="ah-card-title">Cost per unit</h2>
        <p class="ah-muted" style="margin-top:-6px">Filament price comes from Spoolman when the colour is linked to a Spoolman filament with a price, else from these prices. Printers can have their own hourly rate.</p>
        <div class="grid grid-cols-2 gap-3">
          <div class="ah-field"><label for="mpk">Filament price per kg</label><input id="mpk" class="input" type="number" min="0" step="0.5" bind:value={s.default_material_price} onchange={() => { s.default_material_price = Number(s.default_material_price); save(); }} /></div>
          <div class="ah-field"><label for="phr">Printer time per hour</label><input id="phr" class="input" type="number" min="0" step="0.05" bind:value={s.printer_hourly_rate} onchange={() => { s.printer_hourly_rate = Number(s.printer_hourly_rate); save(); }} /></div>
        </div>
        <div class="lbl" style="margin:6px 0 0">Price per kg by material (optional)</div>
        <div class="flex flex-wrap gap-2">
          {#each Object.entries(s.material_prices || {}) as [mat, v]}
            <span class="chip">{mat}: {v}<button class="muted hover:underline" style="background:none;border:0;cursor:pointer" onclick={() => { const m = { ...s.material_prices }; delete m[mat]; s.material_prices = m; save(); }}>Remove</button></span>
          {/each}
        </div>
        <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); if (!mp.mat || mp.price === '') return; s.material_prices = { ...(s.material_prices || {}), [mp.mat.toUpperCase()]: Number(mp.price) }; mp = { mat: '', price: '' }; save(); }}>
          <input class="input" style="width:8rem" placeholder="PETG" aria-label="Material" bind:value={mp.mat} /><input class="input" style="width:8rem" type="number" min="0" step="0.5" placeholder="per kg" aria-label="Price per kg" bind:value={mp.price} /><button class="btn">Add price</button>
        </form>
      </section>
    </div>

  {:else if tab === 'sites'}
    <div class="ah-cols">
      <section class="ah-glass ah-card">
        <h2 class="ah-card-title">MakerWorld account</h2>
        <p class="ah-muted" style="margin-top:-6px">Needed to download model files (3MF) when importing. Browsing, photos, details and license records work without it. Uses your Bambu Lab login; the password is sent once to Bambu Lab and only the session token is stored.</p>
        {#if mw?.connected}
          <div class="ah-kv"><div><span class="chip ok">Connected{mw.name ? ` as ${mw.name}` : ''}</span> <span class="ah-muted">{mw.account}, {ago(mw.connected_at)}</span></div>
            <button class="btn sm danger" onclick={mwLogout}>Disconnect</button></div>
        {:else}
          <form class="flex flex-col gap-2" onsubmit={(e) => { e.preventDefault(); mwLogin(); }}>
            <input class="input" type="email" placeholder="Bambu Lab email" aria-label="Bambu Lab email" bind:value={mwf.email} autocomplete="username" />
            {#if mwStep === 'creds'}<input class="input" type="password" placeholder="Password (leave blank to get an email code)" aria-label="Password" bind:value={mwf.password} autocomplete="current-password" />
            {:else}<input class="input" inputmode="numeric" placeholder={mwStep === 'tfa' ? 'Authenticator code' : 'Code from email'} aria-label="Code" bind:value={mwf.code} autocomplete="one-time-code" />{/if}
            {#if mwMsg}<p class="ah-muted">{mwMsg}</p>{/if}
            <button class="btn primary" style="align-self:flex-start" disabled={mwBusy || !mwf.email}>{mwStep === 'creds' ? 'Connect' : 'Verify'}</button>
          </form>
        {/if}
        <div class="panel ah-kv" style="padding:12px 16px"><div><div style="font-weight:600">License watch</div><div class="ah-muted">Weekly check that imported designs (all sites) still carry the license you recorded.</div></div>
          <button class="btn sm" onclick={recheck}>Check now</button></div>
        {#if bl?.codes?.length}
          <div class="panel flex flex-col gap-2" style="padding:12px 16px">
            <div style="font-weight:600">Deleted models (not imported again)</div>
            <p class="ah-muted">The library scanner skips these. Remove one to let it import again on the next scan. Importing a MakerWorld model by hand also clears it.</p>
            <div class="flex flex-wrap gap-2">{#each bl.codes as c}<span class="chip">{c} <button class="muted hover:underline" style="background:none;border:0;cursor:pointer" onclick={() => unblock(c)}>Allow</button></span>{/each}</div>
          </div>
        {/if}
      </section>
      <section class="ah-glass ah-card">
        <h2 class="ah-card-title">Other model sites</h2>
        <div class="panel" style="padding:12px 16px"><div class="ah-kv"><b>Printables</b><span class="chip ok">No account needed</span></div><div class="ah-muted">Free models download directly.</div></div>
        <div class="panel" style="padding:12px 16px"><div class="ah-kv"><b>Thangs and MyMiniFactory</b><span class="chip ok">No account needed</span></div><div class="ah-muted">Details, photos and license import from the link. Their downloads need you signed in, so the files come from your browser: the OpenPrintHQ add-on's <i>Send files</i>, or drop them on the model's Files tab.</div></div>
        <div class="panel flex flex-col gap-2" style="padding:12px 16px">
          <div class="ah-kv"><b>Cults3D</b>
            {#if srcs?.cults3d?.connected}<span class="chip ok">API key{srcs.cults3d.account ? ` (${srcs.cults3d.account})` : ''}</span><button class="btn sm danger" onclick={() => srcOut('cults3d')}>Remove</button>{/if}</div>
          {#if !srcs?.cults3d?.connected}
            <p class="ah-muted">Cults3D refuses servers, so without a key the browser add-on reads the model page you have open. With your Cults3D nick and an API key from <a target="_blank" rel="noopener" href="https://cults3d.com/en/api/keys">cults3d.com/en/api/keys</a> a pasted link works here and on the phone too. Files always come from your browser.</p>
            <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); cuSave(); }}><input class="input" placeholder="Cults3D nick" aria-label="Cults3D nick" bind:value={cu.email} /><input class="input" placeholder="API key" aria-label="Cults3D API key" bind:value={cu.token} /><button class="btn primary" disabled={!cu.email || !cu.token}>Save</button></form>
          {/if}
        </div>
        <div class="panel flex flex-col gap-2" style="padding:12px 16px">
          <div class="ah-kv"><b>Thingiverse</b>
            {#if srcs?.thingiverse?.connected}<span class="chip ok">Connected</span><button class="btn sm danger" onclick={() => srcOut('thingiverse')}>Remove</button>{/if}</div>
          {#if !srcs?.thingiverse?.connected}
            <p class="ah-muted">Thingiverse's API needs an App Token: create a free app at <a target="_blank" rel="noopener" href="https://www.thingiverse.com/apps/create">thingiverse.com/apps/create</a> (type Web App, any name, and this hub's address as the URL), then copy the App Token here.</p>
            <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); tvSave(); }}><input class="input" placeholder="App Token" aria-label="Thingiverse App Token" bind:value={tvTok} /><button class="btn primary" disabled={!tvTok}>Save</button></form>
          {/if}
        </div>
        <div class="panel flex flex-col gap-2" style="padding:12px 16px">
          <div class="ah-kv"><b>Creality Cloud</b>
            {#if srcs?.creality?.connected}<span class="chip ok">Connected{srcs.creality.name ? ` as ${srcs.creality.name}` : ''}</span><button class="btn sm danger" onclick={() => srcOut('creality')}>Disconnect</button>{/if}</div>
          {#if !srcs?.creality?.connected}
            <p class="ah-muted">Needed only to download 3MF files. The password goes to Creality once; only the session token is stored.</p>
            <form class="flex flex-col gap-2" onsubmit={(e) => { e.preventDefault(); crSave(); }}>
              <input class="input" type="email" placeholder="Creality Cloud email" aria-label="Creality Cloud email" bind:value={cr.email} autocomplete="username" />
              <input class="input" type="password" placeholder="Password" aria-label="Creality Cloud password" bind:value={cr.password} autocomplete="current-password" />
              <button class="btn primary" style="align-self:flex-start" disabled={!cr.email || !cr.password}>Connect</button>
            </form>
          {/if}
        </div>
      </section>
    </div>

  {:else if tab === 'keys'}
    <div class="ah-cols">
      <section class="ah-glass ah-card">
        <h2 class="ah-card-title">API keys</h2>
        <p class="ah-muted" style="margin-top:-6px">For scripts, Claude, n8n and so on. Send <code>Authorization: Bearer ahk_...</code>. Docs: <a href="/api/docs" target="_blank">/api/docs</a>. Quick add: <code>POST /api/v1/ingest</code>. Phones, TVs and the browser add-on are on <a href="/devices">Devices</a>.</p>
        <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); mk(); }}><input class="input" placeholder="Key name" aria-label="Key name" bind:value={newKey} /><button class="btn primary">Create key</button></form>
        {#if created}<div class="ah-banner ah-banner--live" style="flex-wrap:wrap;word-break:break-all"><b>Copy it now, it is shown once:</b> <button class="btn sm" onclick={copyKey}>Copy</button><code style="width:100%">{created}</code>{#if createdNote}<span class="ah-muted">{createdNote}</span>{/if}</div>{/if}
        <table class="grid"><thead><tr><th>Name</th><th>Key</th><th>Used</th><th></th></tr></thead><tbody>
          {#each keys as k}<tr><td>{k.name}</td><td class="ah-muted ah-mono">ahk_{k.prefix}_...</td><td class="ah-muted">{k.last_used_at ? 'used ' + ago(k.last_used_at) : 'never used'}{k.expires_at ? (k.expired ? ', expired' : ', expires ' + new Date(k.expires_at).toLocaleDateString()) : ''}{k.instance !== 'any' ? ', ' + (k.instance === 'bound' ? 'tied to one device' : 'ties to first device') : ''}</td>
            <td>{#if k.revoked}<span class="chip">Revoked</span>{:else}<button class="btn sm danger" onclick={() => revoke(k)}>Revoke</button>{/if}</td></tr>
          {:else}<tr><td colspan="4" class="ah-muted">No keys yet.</td></tr>{/each}
        </tbody></table>
      </section>
      <section class="ah-glass ah-card">
        <h2 class="ah-card-title">Claude connector</h2>
        <p class="ah-muted" style="margin-top:-6px">Add <code>{origin}/mcp</code> as a custom connector in Claude (Settings &gt; Connectors). You sign in with your hub login and approve it; Claude can then find models, slice them for your printers and pick production files.</p>
        <table class="grid"><thead><tr><th>App</th><th>Who</th><th>Connected</th><th></th></tr></thead><tbody>
          {#each conns as c}<tr><td>{c.app}</td><td class="ah-muted">{c.user}, {c.scopes.join(', ')}</td><td class="ah-muted">{ago(c.connected_at)}{c.last_used_at ? ', used ' + ago(c.last_used_at) : ''}</td>
            <td><button class="btn sm danger" onclick={() => disconnect(c)}>Disconnect</button></td></tr>
          {:else}<tr><td colspan="4" class="ah-muted">No apps connected.</td></tr>{/each}
        </tbody></table>
      </section>
    </div>
  {/if}
</Page>
