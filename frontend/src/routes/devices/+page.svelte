<script>
  // Devices: the browser add-on, TVs and phones that reach this hub, and the keys they use. Brings together what
  // were the Browser add-on and Connect a TV pages and the phone part of Settings (old addresses redirect here).
  import { onMount } from 'svelte';
  import { Icon } from 'openprinthq-ui';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';
  import Page from '$lib/Page.svelte';

  const origin = typeof location !== 'undefined' ? location.origin : '';
  // Browser add-on
  let clipKeys = $state([]);
  let sites = $state([]);
  let clipCreated = $state('');
  let clipName = $state('Browser add-on');
  let clipErr = $state('');
  let showSites = $state(false);
  // Keys for phones and TVs (API keys tied to a device)
  let keys = $state([]);
  async function load() {
    try { clipKeys = (await api.get('/api/clip/v1/keys')).keys; clipErr = ''; } catch (e) { clipKeys = []; clipErr = e.message || String(e); }
    try { sites = (await api.get('/api/clip/v1/sites')).sites; } catch { sites = []; }
    try { keys = (await api.get('/api/mobile/v1/keys')).keys; } catch { keys = []; }
  }
  async function makeClip() { const r = await attempt(() => api.post('/api/clip/v1/keys', { name: clipName || 'Browser add-on' })); clipCreated = r.key; load(); }
  async function revokeClip(k) { if (confirm(`Disconnect ${k.name}? The add-on using its key stops working.`)) { await api.del(`/api/clip/v1/keys/${k.id}`); load(); } }
  async function revokeKey(k) { if (confirm(`Disconnect ${k.name}? It stops working until it is connected again.`)) { await api.del(`/api/keys/${k.id}`); load(); } }
  async function copy(t) { try { await navigator.clipboard.writeText(t); toast('Copied'); } catch { toast('Select it and copy it'); } }

  // Connect a TV: the TV app shows a short code; approving it gives that TV a view-only key.
  let tvCode = $state('');
  let tvName = $state('');
  let tvDone = $state(null);
  function tidy(v) {
    const c = (v || '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
    return c.length > 4 ? `${c.slice(0, 4)}-${c.slice(4)}` : c;
  }
  async function approveTv() {
    const r = await attempt(() => api.post('/api/mobile/v1/tv/approve', { code: tvCode, name: tvName.trim() || null }), 'TV connected');
    if (r) { tvDone = r; tvCode = ''; tvName = ''; load(); }
  }

  // Connect a phone by QR code (one-time, 10 minutes; the phone confirms before a key is made)
  let pair = $state(null);
  let pairAccess = $state('act');
  let pairDays = $state('90');
  let pairLeft = $state(0);
  let pairTimer = null;
  let created = $state('');
  function stopTimer() { if (pairTimer) clearInterval(pairTimer); pairTimer = null; }
  async function startPair() {
    stopTimer();
    try {
      pair = await attempt(() => api.post('/api/mobile/v1/pairings', { access: pairAccess, days: pairDays === 'never' ? null : Number(pairDays) }));
    } catch { return; }
    tickPair();
    pairTimer = setInterval(tickPair, 2000);
  }
  async function tickPair() {
    if (!pair) return;
    pairLeft = Math.max(0, Math.round((new Date(pair.expires_at) - Date.now()) / 1000));
    try { const s = await api.get(`/api/mobile/v1/pairings/${pair.id}`); pair = { ...pair, ...s }; } catch {}
    if (pair && pair.state !== 'waiting') {
      stopTimer();
      if (pair.state === 'connected') { toast(`${pair.device} is connected`); load(); }
    }
  }
  async function cancelPair() {
    stopTimer();
    if (pair && pair.state === 'waiting') { try { await api.del(`/api/mobile/v1/pairings/${pair.id}`); } catch {} }
    pair = null;
  }
  async function copyPairLink() { try { await navigator.clipboard.writeText(pair.url); toast('Link copied; open it on the phone'); } catch { toast('Could not copy the link'); } }
  async function mkPhone() {
    const r = await attempt(() => api.post('/api/mobile/v1/keys', { name: 'Phone', scopes: ['read', 'write'], expires_in_days: 90, bind_instance: true, note: 'OpenPrintHQ phone app' }));
    created = r.key; load();
  }
  $effect(() => () => stopTimer());

  onMount(() => {
    const c = new URLSearchParams(location.search).get('code');   // a TV's QR code opens /tv?code=..., which lands here
    if (c) tvCode = tidy(c);
    load();
  });

  const kindOf = (k) => (k.name || '').startsWith('TV: ') ? 'TV, view only' : 'Phone app';
  const devices = $derived([
    ...keys.filter((k) => !k.revoked && (k.name || '').startsWith('TV: ') || (!k.revoked && k.instance !== 'any'))
      .map((k) => ({ key: k, name: (k.name || '').replace(/^TV: /, ''), kind: kindOf(k), seen: k.last_used_at, revoke: () => revokeKey(k) })),
    ...clipKeys.map((k) => ({ key: k, name: k.name, kind: 'Browser add-on', seen: k.last_used_at, revoke: () => revokeClip(k) }))
  ]);
</script>

<Page title="Devices" description="The browser add-on, TVs and phones that reach this hub, and the keys they use.">
  <div class="grid gap-[14px]" style="grid-template-columns:repeat(2,minmax(0,1fr));flex:none">
    <section class="ah-glass ah-card" id="add-on">
      <div class="flex items-center gap-[10px]"><div class="ah-icon-tile"><Icon name="puzzle" /></div>
        <div style="flex:1;min-width:0"><h2 class="ah-card-title">Browser add-on</h2><div class="ah-muted">Firefox, Chrome and Edge</div></div>
        {#if clipKeys.length}<span class="chip ok">{clipKeys.length} connected</span>{/if}</div>
      <p class="ah-secondary">Puts <b>Add to hub</b> on model pages and in the toolbar. For sites that only let a signed-in browser download (Thangs, MyMiniFactory, Cults3D, paid models anywhere), it sends the files from your browser too. Licenses and the designer's credit always come with the model.</p>
      <div class="panel" style="padding:10px 14px"><b>1. Install it.</b> <span class="ah-muted">Firefox: the signed file from the hub team (<code>artisan-hub-clipper-firefox.xpi</code>), or about:debugging, This Firefox, Load Temporary Add-on. Chrome and Edge: unzip <code>artisan-hub-clipper-chromium.zip</code>, open chrome://extensions (edge://extensions), turn on Developer mode and choose Load unpacked. Store listings come later.</span></div>
      <div class="panel flex flex-col gap-2" style="padding:10px 14px">
        <b>2. Make your key and paste it into the add-on, with this hub's address.</b>
        {#if clipErr}<span class="ah-field-error">{clipErr}</span>{/if}
        <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); makeClip(); }}><input class="input" placeholder="Name (Firefox on the desk PC)" aria-label="Add-on key name" bind:value={clipName} /><button class="btn primary">Make a key</button></form>
        {#if clipCreated}
          <div class="ah-banner ah-banner--live" style="flex-wrap:wrap;word-break:break-all"><b>Copy it now, it is shown once:</b><button class="btn sm" onclick={() => copy(clipCreated)}>Copy key</button><code style="width:100%">{clipCreated}</code>
            <span>Hub address: <code>{origin}</code></span><button class="btn sm" onclick={() => copy(origin)}>Copy address</button></div>
        {/if}
        <span class="ah-muted">In the add-on's options, paste the address and the key and press Test. The key can only add models by link and send their files, works for a year, and stops when you lose the right to add models.</span>
      </div>
      <button class="btn sm ghost" style="align-self:flex-start" onclick={() => (showSites = !showSites)}>{showSites ? 'Hide' : 'Show'} what works where</button>
      {#if showSites}
        <table class="grid"><thead><tr><th>Site</th><th>Files</th><th>Notes</th></tr></thead><tbody>
          {#each sites as s}<tr><td style="font-weight:600">{s.label}</td><td>{s.files === 'hub' ? 'Hub downloads files' : 'Files from your browser'}</td><td class="ah-muted">{s.needs || ''}{s.needs && s.notes ? '. ' : ''}{s.notes}</td></tr>{/each}
        </tbody></table>
      {/if}
    </section>

    <section class="ah-glass ah-card" id="tv">
      <div class="flex items-center gap-[10px]"><div class="ah-icon-tile"><Icon name="tv" /></div>
        <div style="flex:1;min-width:0"><h2 class="ah-card-title">Connect a TV</h2><div class="ah-muted">Apple TV and Android TV</div></div></div>
      <p class="ah-secondary">Open OpenPrintHQ on the TV. It shows a code; type it here. The TV gets its own view-only key: printers, alerts, the queue and today's orders. It cannot change anything.</p>
      {#if tvDone}<div class="ah-banner ah-banner--live">Connected <b>{tvDone.name || tvDone.device}</b>. The TV shows the wall in a few seconds.</div>{/if}
      {#if can('api_keys')}
        <form class="flex gap-3 items-end" onsubmit={(e) => { e.preventDefault(); approveTv(); }}>
          <div class="ah-field" style="flex:1"><label for="tvc">Code on the TV</label>
            <input id="tvc" class="input ah-mono" style="letter-spacing:.12em;text-transform:uppercase" placeholder="XXXX-XXXX" maxlength="9" autocomplete="off"
              value={tvCode} oninput={(e) => { tvCode = tidy(e.currentTarget.value); e.currentTarget.value = tvCode; }} /></div>
          <div class="ah-field" style="flex:1"><label for="tvn">Name (optional)</label><input id="tvn" class="input" placeholder="Print room TV" maxlength="120" bind:value={tvName} /></div>
          <button class="btn primary" disabled={tvCode.replace('-', '').length !== 8}>Connect this TV</button>
        </form>
      {:else}<p class="ah-muted">Connecting a TV needs the permission to create API keys.</p>{/if}
    </section>
  </div>

  <section class="ah-glass ah-card" id="phone" style="flex:none">
    <div class="flex items-center gap-[10px]"><div class="ah-icon-tile"><Icon name="scan" /></div>
      <div style="flex:1;min-width:0"><h2 class="ah-card-title">Phone app</h2><div class="ah-muted">Scan spools and parts, watch printers, take orders</div></div>
      <a class="btn sm" href="/get-app" data-sveltekit-reload><Icon name="download" size={16} />Get the app</a></div>
    {#if can('api_keys')}
      {#if !pair}
        <p class="ah-secondary">Scan the code with the phone's camera or the OpenPrintHQ app. If the app is not installed yet, the phone offers to install it. The phone shows what it is connecting to and asks you to confirm; only then is a key made, tied to that phone. The code works once, for 10 minutes.</p>
        <div class="flex flex-wrap gap-3 items-center">
          <span class="ah-muted">The phone can</span>
          <div class="ah-seg" role="group" aria-label="What the phone can do">{#each [['view', 'View only'], ['act', 'View and act'], ['all', 'Everything I can do']] as [val, label]}<button type="button" aria-pressed={pairAccess === val} onclick={() => (pairAccess = val)}>{label}</button>{/each}</div>
          <span class="ah-muted">for</span>
          <div class="ah-seg" role="group" aria-label="For how long">{#each [['30', '30 days'], ['90', '90 days'], ['365', '1 year'], ['never', 'No expiry']] as [val, label]}<button type="button" aria-pressed={pairDays === val} onclick={() => (pairDays = val)}>{label}</button>{/each}</div>
          <button class="btn primary" onclick={startPair}>Show QR code</button>
          <button class="btn ghost" onclick={mkPhone} title="Read and write, 90 days, tied to the first phone that uses it">Make a key to paste</button>
        </div>
        {#if created}<div class="ah-banner ah-banner--live" style="flex-wrap:wrap;word-break:break-all"><b>Copy it now, it is shown once:</b><button class="btn sm" onclick={() => copy(created)}>Copy</button><code style="width:100%">{created}</code><span>Paste it into the OpenPrintHQ app with this address: {origin}. It works for 90 days and only on the first phone that uses it.</span></div>{/if}
      {:else}
        <div class="flex flex-wrap gap-6 items-center">
          <div class="pair-qr" class:faded={pair.state !== 'waiting'}>{@html pair.qr_svg}</div>
          <div style="min-width:14rem" class="flex flex-col gap-2">
            {#if pair.state === 'waiting'}
              <p><b>Waiting for a phone</b>, {Math.floor(pairLeft / 60)}:{String(pairLeft % 60).padStart(2, '0')} left</p>
              <p class="ah-muted">{pair.access}, {pair.days ? `${pair.days} days` : 'no expiry'}</p>
            {:else if pair.state === 'connected'}
              <p class="chip ok" style="align-self:flex-start">{pair.device} is connected</p>
              <p class="ah-muted">Its key is in the list below, tied to that phone.</p>
            {:else}
              <p><b>This code {pair.state === 'expired' ? 'has expired' : 'was cancelled'}.</b></p>
            {/if}
            <div class="flex gap-2">
              {#if pair.state === 'waiting'}
                <button class="btn" onclick={cancelPair}>Cancel</button>
                <button class="btn" onclick={copyPairLink} title="For a phone that cannot scan: send it this link">Copy link</button>
              {:else}
                <button class="btn primary" onclick={startPair}>Make a new code</button>
                <button class="btn" onclick={() => (pair = null)}>Done</button>
              {/if}
            </div>
          </div>
        </div>
      {/if}
    {:else}<p class="ah-muted">Connecting a phone needs the permission to create API keys.</p>{/if}
  </section>

  <section class="ah-glass ah-card" style="flex:1 0 auto;min-height:180px">
    <h2 class="ah-card-title">Connected devices</h2>
    <table class="grid"><thead><tr><th>Device</th><th>Kind</th><th>Last seen</th><th></th></tr></thead><tbody>
      {#each devices as d (d.kind + d.key.id)}
        <tr><td>{d.name}</td><td>{d.kind}</td><td class="ah-muted">{d.seen ? ago(d.seen) : 'Not yet'}{d.key.expires_at ? `, until ${new Date(d.key.expires_at).toLocaleDateString()}` : ''}</td>
          <td style="text-align:right"><button class="btn sm danger" onclick={d.revoke}>Disconnect</button></td></tr>
      {:else}<tr><td colspan="4" class="ah-muted">No devices yet.</td></tr>{/each}
    </tbody></table>
  </section>
</Page>

<style>
  .pair-qr { width: 13rem; max-width: 100%; background: #fff; border-radius: 12px; overflow: hidden; }
  .pair-qr :global(svg) { width: 100%; height: auto; display: block; shape-rendering: crispEdges; }
  .pair-qr.faded { opacity: 0.25; }
</style>
