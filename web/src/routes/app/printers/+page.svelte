<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import CameraImg from '$lib/components/CameraImg.svelte';
  import DownloadClient from '$lib/components/DownloadClient.svelte';
  import { prettyModel, printerLabel } from '$lib/models.js';
  import { markSeen, recentlyOnline } from '$lib/online.js';

  let loading = $state(true);
  let error = $state(null);
  let printers = $state([]);
  let timer = null;
  // Camera thumbnails: a slow 60s snapshot poll (bump camTick) keeps resource
  // use low, independent of the 5s status poll. CameraImg shows the cached last
  // frame instantly on load, then swaps in the live one.
  let camTick = $state(0);
  let camTimer = null;

  function base(data) {
    const arr = Array.isArray(data) ? data : (data?.printers || data?.items || data?.results || []);
    return arr.map((p) => ({
      id: p.id ?? p.printer_id ?? p.serial ?? p.name,
      name: p.name ?? p.friendly_name ?? p.model ?? 'Printer',
      model: p.model ?? p.printer_type ?? p.type ?? '',
      vendor: p.connection_type ?? p.vendor ?? p.brand ?? ''
    }));
  }

  async function load(initial = true) {
    if (initial) { loading = true; error = null; }
    try {
      const list = base(await api.printers());
      // Live state comes from the per-printer status endpoint, not the list.
      const live = await Promise.all(list.map((p) => api.printerStatus(p.id).catch(() => null)));
      printers = list.map((p, i) => ({ ...p, live: live[i] }));
      // Remember the last time each printer reported connected (drives hysteresis).
      printers.forEach((p) => markSeen(p.id, p.live?.connected));
      error = null;
    } catch (e) {
      error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable');
    } finally {
      loading = false;
    }
  }

  let deploymentMode = $state('both');
  let connectors = $state([]);            // OpenPrintHQ client apps paired to this instance
  let showRemoteConnect = $state(false);  // local mode: user chose to reveal the connect-a-remote-printer section

  // At least one Cloud Client has paired (has_client_key) with this instance.
  // In 'remote' mode this gates whether printers can be added at all.
  const hasPairedClient = $derived(connectors.some((c) => c.has_client_key));
  // Any connector currently online (live tunnel up).
  const anyClientOnline = $derived(connectors.some((c) => c.online));

  async function loadConnectors() {
    try { const r = await api.connectors(); connectors = Array.isArray(r) ? r : (r?.connectors || []); }
    catch { /* connectors are optional; ignore */ }
  }
  function fmtSeen(v) { if (!v) return 'never'; const d = new Date(v); return isNaN(d) ? 'never' : d.toLocaleString(); }

  onMount(() => {
    load(true);
    loadConnectors();
    api.pubConfig().then((c) => { deploymentMode = c.deployment_mode || 'both'; }).catch(() => {});
    timer = setInterval(() => { load(false); loadConnectors(); }, 5000);
    // Camera snapshots refresh on a slow 60s cadence (independent of the 5s
    // status poll) to keep resource use low. CameraImg keeps the last frame.
    camTimer = setInterval(() => camTick++, 60000);
    return () => { clearInterval(timer); clearInterval(camTimer); };
  });

  function statusOf(p) {
    if (!p.live) return 'unknown';
    if (!p.live.connected) return 'offline';
    return (p.live.state || 'idle').toString().toLowerCase();
  }
  // Card status chip: an idle printer with a clear plate reads "ready" (not the
  // last job's failed/finished). After a print it prompts to clear the plate.
  function dispOf(p) {
    const l = p.live;
    // Online with hysteresis first: a momentary connected=false (MQTT flap) still
    // reads online if the printer was connected within the grace window; a cached
    // last-online makes a fresh reload show the true state instead of "offline".
    const online = l ? (l.connected || recentlyOnline(p.id)) : recentlyOnline(p.id);
    if (!l && !online) return { label: 'checking', tone: '' };
    if (!online) return { label: 'offline', tone: 'danger' };
    const s = (l?.state || '').toString().toLowerCase();
    if (/run|print/.test(s)) return { label: 'printing', tone: 'primary' };
    if (/pause/.test(s)) return { label: 'paused', tone: 'accent' };
    if (l?.awaiting_plate_clear) return { label: 'clear plate', tone: 'accent', clear: true };
    return { label: 'ready', tone: 'ok' };
  }
  // ---- per-card print controls ----
  let acting = $state({});        // `${id}:${key}` -> true (in-flight)
  let confirmStop = $state({});   // id -> true (awaiting stop confirmation)
  async function pAction(p, action, key) {
    acting = { ...acting, [`${p.id}:${key}`]: true };
    try {
      await api.printerAction(p.id, action);
      await api.printerAction(p.id, 'refresh-status').catch(() => {});
      await load(false);
    } catch (e) { /* surfaced on next poll */ }
    finally {
      acting = { ...acting, [`${p.id}:${key}`]: false };
      confirmStop = { ...confirmStop, [p.id]: false };
    }
  }
  const startPrint = (p) => pAction(p, 'print/resume', 'start');
  const pausePrint = (p) => pAction(p, 'print/pause', 'pause');
  const stopPrint = (p) => pAction(p, 'print/stop', 'stop');
  const askStop = (p) => (confirmStop = { ...confirmStop, [p.id]: true });
  const cancelStop = (p) => (confirmStop = { ...confirmStop, [p.id]: false });

  let clearing = $state({});
  async function clearPlate(p, e) {
    e.preventDefault(); e.stopPropagation();
    clearing = { ...clearing, [p.id]: true };
    try { await api.clearPlate(p.id); await load(false); }
    catch (err) { /* next poll reflects state */ }
    finally { clearing = { ...clearing, [p.id]: false }; }
  }
  function tone(s) {
    if (/run|print/.test(s)) return 'primary';
    if (/pause/.test(s)) return 'accent';
    if (/idle|ready|online/.test(s)) return 'ok';
    if (/finish|done|complete/.test(s)) return 'ok';
    if (/error|offline|fault|fail/.test(s)) return 'danger';
    return '';
  }
  const t1 = (v) => (v == null ? null : Math.round(Number(v)));

  // ---- fleet firmware ----
  let fw = $state(null);          // { updates:[...], updates_available }
  let fwLoading = $state(false);
  let fwErr = $state(null);
  async function checkFirmware() {
    fwLoading = true; fwErr = null;
    try { fw = await api.firmwareUpdates(); }
    catch (e) { fwErr = e.message || 'firmware check failed'; }
    finally { fwLoading = false; }
  }
</script>

<PageTitle page="Printers" />

{#snippet clientStatus()}
  {#if connectors.length}
    <div class="card card-pad clients">
      <div class="clients-h">
        <span class="clabel">Connected clients</span>
        <span class="muted tiny">OpenPrintHQ Cloud Client apps paired to this instance</span>
      </div>
      <ul class="clist">
        {#each connectors as c (c.id)}
          <li>
            <span class="cn">{c.name}</span>
            <span class="dot {c.online ? 'on' : ''}" title={c.online ? 'online' : 'offline'}></span>
            <span class="cs muted">{c.online ? 'online' : 'offline'}</span>
            {#if c.has_client_key}<span class="cpair" title="A client has paired with this connector.">paired</span>{/if}
            <span class="cseen muted tiny mono">last seen {fmtSeen(c.last_seen)}</span>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
{/snippet}

<div class="head">
  <div>
    <h1>Printers</h1>
    <p class="muted">Bambu Lab, Creality, Prusa, Snapmaker and Voron — one fleet.</p>
  </div>
  <div class="flex gap">
    <button class="btn btn-ghost btn-sm" onclick={() => load(false)}>Refresh</button>
    {#if deploymentMode === 'remote' && !hasPairedClient}
      <span class="btn btn-primary btn-sm disabled" aria-disabled="true" title="Install and pair a Cloud Client on your printers' network first — then you can add printers.">+ Add printer</span>
    {:else}
      <a class="btn btn-primary btn-sm" href="/app/printers/add">+ Add printer</a>
    {/if}
  </div>
</div>

{#if loading}
  <div class="card card-pad muted">Connecting to your engine…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad">
    <h3>No instance yet</h3>
    <p class="muted">Provision your instance from the <a href="/app">overview</a> to start adding printers.</p>
  </div>
{:else if error}
  <div class="card card-pad">
    <h3>Engine unreachable</h3>
    <p class="muted">{error}</p>
    <button class="btn btn-ghost btn-sm" onclick={() => load()}>Retry</button>
  </div>
{:else if printers.length === 0}
  {@render clientStatus()}

  {#if deploymentMode === 'remote'}
    <!-- Remote: you must install + pair a client before adding printers. The
         connect/download section sits ABOVE the empty state, and the add
         buttons stay disabled until at least one client pairs. -->
    <div class="card card-pad"><DownloadClient /></div>
    <div class="card card-pad empty">
      <div class="ic">🖨️</div>
      <h3>No printers yet</h3>
      {#if hasPairedClient}
        <p class="muted">A client is paired. Add your first printer — Bambu, Klipper (Mainsail/Fluidd), Prusa, Snapmaker and more are supported out of the box.</p>
        <a class="btn btn-primary" href="/app/printers/add">+ Add your first printer</a>
      {:else}
        <p class="muted">Install the Cloud Client on your printers' network and pair it (above). Once a client connects, you can add printers here.</p>
        <span class="btn btn-primary disabled" aria-disabled="true" title="Waiting for a Cloud Client to pair.">+ Add your first printer</span>
      {/if}
    </div>
  {:else}
    <!-- Local or Both: add directly. -->
    <div class="card card-pad empty">
      <div class="ic">🖨️</div>
      <h3>No printers yet</h3>
      <p class="muted">Your engine is live and ready. Add your first printer — Bambu, Klipper (Mainsail/Fluidd), Prusa, Snapmaker and more are supported out of the box.</p>
      <a class="btn btn-primary" href="/app/printers/add">+ Add your first printer</a>
    </div>
    {#if deploymentMode === 'both'}
      <div class="card card-pad"><DownloadClient /></div>
    {:else}
      <!-- Local: connect-a-remote-printer section hidden behind a toggle. -->
      {#if showRemoteConnect}
        <div class="card card-pad">
          <DownloadClient />
          <button class="btn btn-ghost btn-sm" onclick={() => (showRemoteConnect = false)}>Hide</button>
        </div>
      {:else}
        <button class="btn btn-ghost btn-sm reveal" onclick={() => (showRemoteConnect = true)}>Want to add a printer not on the same network as OpenPrintHQ?</button>
      {/if}
    {/if}
  {/if}
{:else}
  {@render clientStatus()}
  <div class="grid printers">
    {#each printers as p (p.id)}
      {@const st = statusOf(p)}
      {@const d = dispOf(p)}
      <div class="card card-pad printer">
        <div class="flex between center">
          <a class="cardlink" href="/app/printers/{p.id}"><h3>{p.name}</h3></a>
          {#if d.clear}
            <button class="chip accent clearchip" onclick={(e) => clearPlate(p, e)} disabled={clearing[p.id]}>
              {clearing[p.id] ? 'Clearing…' : '✓ Clear plate'}
            </button>
          {:else}
            <span class="chip {d.tone}">{d.label}</span>
          {/if}
        </div>
        <a class="cardlink body" href="/app/printers/{p.id}">
          <div class="meta mono">
            {#if printerLabel(p.vendor, p.model)}<span>{printerLabel(p.vendor, p.model)}</span>{/if}
          </div>
          {#if p.live?.connected || recentlyOnline(p.id)}
            <div class="cam">
              <CameraImg printerId={p.id} tick={camTick} alt={`${p.name || 'Printer'} camera`} mode="fill" />
              {#if /run|print/.test(st) && p.live.progress != null}
                <span class="cam-prog mono">{Math.round(p.live.progress)}%</span>
              {/if}
            </div>
          {:else}
            <div class="offhint muted tiny">Offline — open to locate it on the network in case its IP changed.</div>
          {/if}
          {#if p.live?.connected && /run|print/.test(st) && p.live.progress != null}
            <div class="bar"><div class="fill" style="width:{Math.min(100, Math.max(0, p.live.progress))}%"></div></div>
          {/if}
        </a>

        <!-- Quick actions -->
        {#if confirmStop[p.id]}
          <div class="cardbtns confirm">
            <span class="cf-q">Stop the print?</span>
            <button class="cbtn danger" onclick={() => stopPrint(p)} disabled={acting[`${p.id}:stop`]}>
              {acting[`${p.id}:stop`] ? 'Stopping…' : 'Confirm stop'}
            </button>
            <button class="cbtn" onclick={() => cancelStop(p)} disabled={acting[`${p.id}:stop`]}>Cancel</button>
          </div>
        {:else}
          <div class="cardbtns">
            <button class="cbtn" data-tip="Start print" aria-label="Start print"
                    onclick={() => startPrint(p)} disabled={!p.live?.connected || acting[`${p.id}:start`]}>▶</button>
            <button class="cbtn" data-tip="Pause printer" aria-label="Pause printer"
                    onclick={() => pausePrint(p)} disabled={!p.live?.connected || acting[`${p.id}:pause`]}>❙❙</button>
            <button class="cbtn danger" data-tip="Stop print (asks to confirm)" aria-label="Stop print"
                    onclick={() => askStop(p)} disabled={!p.live?.connected}>■</button>
            <a class="cbtn" href="/app/queue?printer={p.id}" data-tip="Open this printer's queue" aria-label="Open print queue">≣</a>
            <button class="cbtn" data-tip="Open camera fullscreen in a new tab" aria-label="Open camera fullscreen in a new tab"
                    onclick={() => window.open(`/app/printers/${p.id}/camera`, '_blank')}>⛶</button>
          </div>
        {/if}
      </div>
    {/each}
  </div>

  <div class="card card-pad fw">
    <div class="flex between center">
      <div>
        <span class="eyebrow">Firmware</span>
        {#if fw}<span class="muted fwsum">{fw.updates_available > 0 ? `${fw.updates_available} update${fw.updates_available > 1 ? 's' : ''} available` : 'All up to date'}</span>{/if}
      </div>
      <button class="btn btn-ghost btn-sm" onclick={checkFirmware} disabled={fwLoading}>{fwLoading ? 'Checking…' : (fw ? 'Re-check' : 'Check firmware')}</button>
    </div>
    {#if fwErr}<p class="err">{fwErr}</p>{/if}
    {#if fw?.updates?.length}
      <div class="fwlist">
        {#each fw.updates as u (u.printer_id)}
          <div class="fwrow">
            <span class="fwn">{u.printer_name}{#if u.model}<span class="muted mono"> · {prettyModel(u.model)}</span>{/if}</span>
            <span class="mono muted fwcur">{u.current_version || 'unknown'}</span>
            {#if u.update_available}
              <span class="chip accent" title={u.latest_version || ''}>update{#if u.latest_version} → {u.latest_version}{/if}</span>
            {:else if u.current_version}
              <span class="chip ok">up to date</span>
            {:else}
              <span class="chip">—</span>
            {/if}
          </div>
        {/each}
      </div>
      <p class="muted tiny">Bambu checks Bambu's firmware feed; Klipper checks Moonraker's update manager. Apply updates from the printer's own screen / Mainsail.</p>
    {:else if fw}
      <p class="muted">No printers to check.</p>
    {:else}
      <p class="muted">Check current firmware and available updates across every printer (Bambu + Klipper).</p>
    {/if}
  </div>

  {#if deploymentMode !== 'local'}
    <details class="card card-pad connect-more">
      <summary>Connect printers on another network</summary>
      <div class="connect-more-body"><DownloadClient /></div>
    </details>
  {/if}
{/if}

<style>
  .connect-more { margin-top: 1.2rem; }
  .clients { margin-bottom: 1rem; }
  .clients-h { display: flex; align-items: baseline; gap: 0.6rem; margin-bottom: 0.5rem; flex-wrap: wrap; }
  .clabel { font-weight: 600; }
  .clist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.35rem; }
  .clist li { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; }
  .clist .dot { width: 0.55rem; height: 0.55rem; border-radius: 999px; background: var(--ophq-danger); display: inline-block; }
  .clist .dot.on { background: var(--ophq-success); }
  .clist .cn { font-weight: 600; }
  .clist .cpair { font-size: 0.68rem; color: var(--ophq-success); border: 1px solid rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); padding: 0.05rem 0.4rem; border-radius: 999px; }
  .clist .cseen { margin-left: auto; }
  .btn.disabled { opacity: 0.5; cursor: not-allowed; pointer-events: none; }
  .reveal { margin-top: 0.2rem; }
  .connect-more summary { cursor: pointer; font-weight: 600; }
  .connect-more-body { margin-top: 1rem; }
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; gap: 1rem; }
  .head h1 { margin: 0; }
  .printers { grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
  .printer { display: block; color: var(--ophq-text); text-decoration: none; transition: border 0.15s, transform 0.15s; }
  .printer:hover { border-color: var(--ophq-primary); transform: translateY(-2px); color: var(--ophq-text); }
  .printer h3 { margin: 0; font-size: 1.05rem; color: var(--ophq-text); }
  .printer .meta { display: flex; gap: 0.6rem; margin-top: 0.5rem; color: var(--ophq-muted); font-size: 0.85rem; }
  .cam { position: relative; margin-top: 0.7rem; aspect-ratio: 16 / 9; border-radius: var(--radius-sm); overflow: hidden; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); }
  .offhint { margin-top: 0.6rem; line-height: 1.45; }
  .cam img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .cam-prog { position: absolute; bottom: 6px; right: 7px; font-size: 0.75rem; padding: 0.1rem 0.4rem; border-radius: 999px; background: rgba(0,0,0,0.55); color: #fff; backdrop-filter: blur(2px); }
  .bar { height: 6px; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; margin-top: 0.5rem; }
  /* card links (title + body navigate to the printer; buttons sit outside) */
  .cardlink { color: var(--ophq-text); text-decoration: none; }
  .cardlink.body { display: block; }
  /* quick-action buttons */
  .cardbtns { display: flex; gap: 0.35rem; margin-top: 0.7rem; padding-top: 0.7rem; border-top: 1px solid var(--ophq-border-soft); }
  .cbtn { flex: 1; display: inline-flex; align-items: center; justify-content: center; min-height: 34px; padding: 0.3rem 0.4rem; background: var(--ophq-surface); border: 1px solid var(--ophq-border); color: var(--ophq-text-2); border-radius: var(--radius-sm); font-size: 0.9rem; cursor: pointer; text-decoration: none; }
  .cbtn:hover:not(:disabled) { border-color: var(--ophq-primary); color: var(--ophq-text); }
  .cbtn.danger:hover:not(:disabled) { border-color: var(--ophq-danger); color: var(--ophq-danger); }
  .cbtn:disabled { opacity: 0.4; cursor: default; }
  .cardbtns.confirm { align-items: center; gap: 0.5rem; }
  .cardbtns.confirm .cf-q { flex: 1; font-size: 0.85rem; color: var(--ophq-text-2); }
  .cardbtns.confirm .cbtn { flex: 0 0 auto; padding: 0.3rem 0.7rem; font-size: 0.82rem; }
  .cbtn.danger { color: var(--ophq-danger); }
  .fill { height: 100%; background: linear-gradient(90deg, var(--ophq-primary), var(--ophq-primary-2)); transition: width 0.4s ease; }
  .chip.danger { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .chip.accent { color: var(--ophq-accent); border-color: rgba(255,176,32,0.3); background: rgba(255,176,32,0.08); }
  .chip.primary { color: var(--ophq-primary-2); border-color: rgba(124,108,255,0.35); background: var(--ophq-primary-dim); }
  .clearchip { cursor: pointer; font: inherit; line-height: inherit; }
  .clearchip:hover:not(:disabled) { filter: brightness(1.08); }
  .clearchip:disabled { opacity: 0.6; cursor: default; }
  .fw { margin-top: 1.4rem; }
  .fwsum { margin-left: 0.6rem; font-size: 0.85rem; }
  .fwlist { display: flex; flex-direction: column; gap: 0.4rem; margin: 0.9rem 0 0.6rem; }
  .fwrow { display: grid; grid-template-columns: 1fr auto auto; gap: 1rem; align-items: center; padding: 0.5rem 0.7rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); }
  .fwn { font-size: 0.9rem; }
  .fwcur { font-size: 0.8rem; }
  .fw .chip.accent { color: var(--ophq-accent); border-color: rgba(255,176,32,0.35); background: rgba(255,176,32,0.08); }
  .fw .chip.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .fw .tiny { font-size: 0.78rem; }
  .fw .err { color: var(--ophq-danger); font-size: 0.88rem; }
  .empty { text-align: center; padding: 2.6rem; }
  .empty .ic { font-size: 2rem; margin-bottom: 0.4rem; }
  .empty p { max-width: 48ch; margin: 0.6rem auto 1.4rem; }
</style>
