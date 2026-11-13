<script>
  import { base as basePath } from '$app/paths';
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import EngineError from '$lib/components/EngineError.svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import PageHead from '$lib/components/PageHead.svelte';
  import Icon from '$lib/components/AppIcon.svelte';
  import { displayState } from '$lib/printerState.js';
  import CameraImg from '$lib/components/CameraImg.svelte';
  import DownloadClient from '$lib/components/DownloadClient.svelte';
  import { prettyModel, printerLabel } from '$lib/models.js';
  import { markSeen, recentlyOnline } from '$lib/online.js';

  let loading = $state(true);
  let error = $state(null);
  let printers = $state([]);
  // Team mode: a member sees only the printers the owner gave them, and does not add printers.
  let me = $state(null);
  const member = $derived(!!me?.team && !me?.isOwner);
  const limited = $derived(member && me?.printerAccess !== 'all');
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
    api.me().then((m) => { me = m; }).catch(() => {});
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
    if (!l && !online) return { label: 'Checking', tone: '' };
    if (!online) return { label: 'Offline', tone: 'danger' };
    const s = (l?.state || '').toString().toLowerCase();
    if (/run|print/.test(s)) return { label: 'Printing', tone: 'live' };
    if (/pause/.test(s)) return { label: 'Paused', tone: 'warn' };
    if (l?.awaiting_plate_clear) return { label: 'Clear plate', tone: 'warn', clear: true };
    return { label: 'Ready', tone: 'ok' };
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

  // ---- status filter ----
  let view = $state('all');
  const VIEWS = [['all', 'All'], ['printing', 'Printing'], ['ready', 'Ready'], ['attention', 'Needs you'], ['offline', 'Offline']];
  function matches(p) {
    const k = displayState(p.id, p.live).key;
    if (view === 'all') return true;
    if (view === 'attention') return ['paused', 'error', 'clear'].includes(k);
    return k === view;
  }
  const shown = $derived(printers.filter(matches));

  // ---- fleet firmware ----
  let fw = $state(null);          // { updates:[...], updates_available }
  let fwLoading = $state(false);
  let fwErr = $state(null);
  async function checkFirmware() {
    fwLoading = true; fwErr = null;
    try { fw = await api.firmwareUpdates(); }
    catch (e) { fwErr = e.message || 'The firmware check failed.'; }
    finally { fwLoading = false; }
  }
</script>

<PageTitle page="Printers" />

{#snippet clientStatus()}
  {#if connectors.length}
    <section class="card card-pad widget clients" aria-label="Connected clients">
      <div class="w-head">
        <span class="w-ic"><Icon name="plug" /></span>
        <div><h2 class="ah-widget-title">Connected clients</h2><div class="ah-muted">OpenPrintHQ client apps paired to this instance</div></div>
      </div>
      <ul class="clist">
        {#each connectors as c (c.id)}
          <li>
            <div class="ctext">
              <b>{c.name}</b>
              <span class="ah-muted">
                {#if c.client?.hostname}<span class="mono" title="The machine reporting through this connector{c.client.version ? `, running client ${c.client.version}` : ''}">{c.client.hostname}</span>{', '}{/if}last seen {fmtSeen(c.last_seen)}
              </span>
            </div>
            {#if c.duplicate_agents}<span class="chip danger" title="Two clients are using this connector's token and evicting each other.">Duplicate client</span>{/if}
            {#if c.has_client_key}<span class="chip" title="A client has paired with this connector.">Paired</span>{/if}
            <span class="chip {c.online ? 'ok' : ''}">{c.online ? 'Online' : 'Offline'}</span>
          </li>
          {#if c.duplicate_agents}
            <!-- Said here, on the page whose printers are misbehaving, because
                 that is where someone lands when this is happening. The symptom
                 (printers dropping offline, commands not landing) looks nothing
                 like its cause. -->
            <li class="dupwarn ah-banner ah-banner--bad">
              <span><strong>Two clients are sharing this connector.</strong>
              Its session has been replaced {c.recent_session_replacements} times in the last five minutes.
              They evict each other on every reconnect, and each eviction drops the tunnels the other one
              owned, which shows up as printers flapping offline and commands never landing.
              Stop the extra client: a duplicate autostart entry on one machine, a second machine using
              the same token, or a background service and the desktop app both running. Give each site
              its own connector instead of sharing a token.</span>
            </li>
          {/if}
        {/each}
      </ul>
    </section>
  {/if}
{/snippet}

{#snippet addButton(label, big = false)}
  {#if member}
  {:else if deploymentMode === 'remote' && !hasPairedClient}
    <span class="btn btn-primary {big ? '' : ''} disabled" aria-disabled="true" title="Install and pair a client app on your printers' network first. Then you can add printers."><Icon name="plus" size={16} />{label}</span>
  {:else}
    <a class="btn btn-primary" href="{basePath}/app/printers/add"><Icon name="plus" size={16} />{label}</a>
  {/if}
{/snippet}

<PageHead title="Printers" description="Bambu Lab, Creality, Prusa, Snapmaker and Voron: one fleet.">
  {#snippet actions()}
    {#if printers.length}
      <div class="ah-seg" role="group" aria-label="Show printers">
        {#each VIEWS as [v, label] (v)}
          <button type="button" aria-pressed={view === v} onclick={() => (view = v)}>{label}</button>
        {/each}
      </div>
    {/if}
    <button class="btn" onclick={() => load(false)}><Icon name="refresh" size={16} />Refresh</button>
    <a class="btn btn-ghost" href="{basePath}/app/printers/groups">Groups</a>
    {@render addButton('Add printer')}
  {/snippet}
</PageHead>

{#if limited && !loading && printers.length > 0}
  <p class="muted">You see the printers the owner gave you. Ask the owner for access to more printers.</p>
{/if}

{#if loading}
  <div class="card card-pad muted">Connecting to your engine</div>
{:else if error === 'no-instance'}
  <div class="card card-pad">
    <h3>No instance yet</h3>
    <p class="muted">Provision your instance from the <a href="{basePath}/app">overview</a> to start adding printers.</p>
  </div>
{:else if error}
  <EngineError {error} onretry={() => load()} />
{:else if printers.length === 0 && member}
  <div class="card card-pad empty">
    <span class="empty-ic"><Icon name="printer" size={28} /></span>
    <h3>No printers to show</h3>
    <p class="muted">Ask the owner for access to more printers.</p>
  </div>
{:else if printers.length === 0}
  {@render clientStatus()}

  {#if deploymentMode === 'remote'}
    <!-- Remote: a client must be installed and paired before printers can be
         added. The connect section sits above the empty state, and the add
         buttons stay disabled until at least one client pairs. -->
    <div class="card card-pad"><DownloadClient /></div>
    <div class="card card-pad empty">
      <span class="empty-ic"><Icon name="printer" size={28} /></span>
      <h3>No printers yet</h3>
      {#if hasPairedClient}
        <p class="muted">A client is paired. Add your first printer: Bambu, Klipper (Mainsail or Fluidd), Prusa, Snapmaker and more work out of the box.</p>
      {:else}
        <p class="muted">Install the client app on your printers' network and pair it (above). Once a client connects, you can add printers here.</p>
      {/if}
      {@render addButton('Add your first printer')}
    </div>
  {:else}
    <div class="card card-pad empty">
      <span class="empty-ic"><Icon name="printer" size={28} /></span>
      <h3>No printers yet</h3>
      <p class="muted">Your engine is live and ready. Add your first printer: Bambu, Klipper (Mainsail or Fluidd), Prusa, Snapmaker and more work out of the box.</p>
      {@render addButton('Add your first printer')}
    </div>
    {#if deploymentMode === 'both'}
      <div class="card card-pad"><DownloadClient /></div>
    {:else}
      {#if showRemoteConnect}
        <div class="card card-pad">
          <DownloadClient />
          <button class="btn btn-ghost btn-sm" onclick={() => (showRemoteConnect = false)}>Hide</button>
        </div>
      {:else}
        <button class="btn btn-ghost btn-sm reveal" onclick={() => (showRemoteConnect = true)}>Add a printer that is not on the same network as ArtisanHub Farm</button>
      {/if}
    {/if}
  {/if}
{:else}
  <div class="pg fill">
    <section class="card grid-card" aria-label="Printer grid">
      <div class="printers">
        {#each shown as p (p.id)}
          {@const st = statusOf(p)}
          {@const d = dispOf(p)}
          <article class="printer" aria-label={p.name}>
            <div class="p-top">
              <a class="cardlink" href="{basePath}/app/printers/{p.id}"><h3>{p.name}</h3></a>
              {#if d.clear}
                <button class="chip warn clearchip" onclick={(e) => clearPlate(p, e)} disabled={clearing[p.id]}>
                  {clearing[p.id] ? 'Clearing' : 'Clear plate'}
                </button>
              {:else}
                <span class="chip {d.tone}">{d.label}</span>
              {/if}
            </div>
            <a class="cardlink body" href="{basePath}/app/printers/{p.id}">
              <div class="meta">{printerLabel(p.vendor, p.model)}</div>
              {#if p.live?.connected || recentlyOnline(p.id)}
                <div class="cam">
                  <CameraImg printerId={p.id} tick={camTick} alt={`${p.name || 'Printer'} camera`} mode="fill" />
                  {#if /run|print/.test(st) && p.live.progress != null}
                    <span class="cam-prog">{Math.round(p.live.progress)}%</span>
                  {/if}
                </div>
              {:else}
                <div class="offhint muted">Offline. Open it to look for it on the network in case its address changed.</div>
              {/if}
              <div class="ah-progress" class:ah-progress--attention={/pause/.test(st)} role="progressbar" aria-label="{p.name} progress"
                   aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(p.live?.progress || 0)}>
                <i style="width:{/run|print|pause/.test(st) ? Math.min(100, Math.max(0, p.live?.progress || 0)) : 0}%"></i>
              </div>
            </a>

            {#if confirmStop[p.id]}
              <div class="cardbtns confirm">
                <span class="cf-q">Stop the print?</span>
                <button class="btn btn-danger btn-sm" onclick={() => stopPrint(p)} disabled={acting[`${p.id}:stop`]}>
                  {acting[`${p.id}:stop`] ? 'Stopping' : 'Stop print'}
                </button>
                <button class="btn btn-sm" onclick={() => cancelStop(p)} disabled={acting[`${p.id}:stop`]}>Cancel</button>
              </div>
            {:else}
              <div class="cardbtns">
                <button class="cbtn" data-tip="Start print" aria-label="Start print"
                        onclick={() => startPrint(p)} disabled={!p.live?.connected || acting[`${p.id}:start`]}><Icon name="play" size={16} /></button>
                <button class="cbtn" data-tip="Pause printer" aria-label="Pause printer"
                        onclick={() => pausePrint(p)} disabled={!p.live?.connected || acting[`${p.id}:pause`]}><Icon name="pause" size={16} /></button>
                <button class="cbtn danger" data-tip="Stop print (asks first)" aria-label="Stop print"
                        onclick={() => askStop(p)} disabled={!p.live?.connected}><Icon name="stop" size={16} /></button>
                <a class="cbtn" href="{basePath}/app/queue?printer={p.id}" data-tip="Open this printer's queue" aria-label="Open print queue"><Icon name="list-queue" size={16} /></a>
                <button class="cbtn" data-tip="Open the camera full screen in a new tab" aria-label="Open the camera full screen in a new tab"
                        onclick={() => window.open(`${basePath}/app/printers/${p.id}/camera`, '_blank')}><Icon name="expand" size={16} /></button>
              </div>
            {/if}
          </article>
        {:else}
          <p class="muted none">No printers match. Pick another status.</p>
        {/each}
      </div>
    </section>

    <aside class="side" aria-label="Widgets">
      {@render clientStatus()}

      <section class="card card-pad widget fw" aria-label="Firmware">
        <div class="w-head">
          <span class="w-ic"><Icon name="download" /></span>
          <div>
            <h2 class="ah-widget-title">Firmware</h2>
            <div class="ah-muted">{fw ? (fw.updates_available > 0 ? `${fw.updates_available} update${fw.updates_available > 1 ? 's' : ''} available` : 'All up to date') : 'Bambu and Klipper printers'}</div>
          </div>
          <button class="btn btn-sm w-act" onclick={checkFirmware} disabled={fwLoading}>{fwLoading ? 'Checking' : (fw ? 'Check again' : 'Check firmware')}</button>
        </div>
        {#if fwErr}<p class="err">{fwErr}</p>{/if}
        {#if fw?.updates?.length}
          <div class="fwlist">
            {#each fw.updates as u (u.printer_id)}
              <div class="fwrow">
                <span class="fwn">{u.printer_name}{#if u.model}<span class="muted">, {prettyModel(u.model)}</span>{/if}</span>
                <span class="mono muted fwcur">{u.current_version || 'Unknown'}</span>
                {#if u.update_available}
                  <span class="chip warn" title={u.latest_version || ''}>Update{#if u.latest_version} to {u.latest_version}{/if}</span>
                {:else if u.current_version}
                  <span class="chip ok">Up to date</span>
                {:else}
                  <span class="chip">Unknown</span>
                {/if}
              </div>
            {/each}
          </div>
          <p class="muted tiny">Bambu checks Bambu's firmware feed; Klipper checks Moonraker's update manager. Apply updates from the printer's own screen or Mainsail.</p>
        {:else if fw}
          <p class="muted">No printers to check.</p>
        {:else}
          <p class="muted">See the firmware on every printer and any update waiting for it.</p>
        {/if}
      </section>

      {#if deploymentMode !== 'local'}
        <details class="card card-pad widget connect-more">
          <summary><span class="w-ic"><Icon name="plug" /></span><span><b>Connect printers on another network</b><span class="ah-muted">Pair the client app at that site</span></span></summary>
          <div class="connect-more-body"><DownloadClient /></div>
        </details>
      {/if}
    </aside>
  </div>
{/if}

<style>
  .pg { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 0.6fr); gap: 14px; flex: 1; min-height: 0; }
  .grid-card { padding: 14px; min-height: 0; overflow: auto; }
  .printers { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
  .printer { display: flex; flex-direction: column; gap: 8px; padding: 12px; border-radius: var(--ah-radius-choice); background: var(--ah-panel); border: 1px solid var(--ah-panel-line); min-width: 0; }
  .printer:hover { border-color: rgba(255, 255, 255, 0.28); }
  .p-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .printer h3 { margin: 0; font-size: 15px; }
  .meta { color: var(--ah-text-3); font-size: 12px; margin-bottom: 8px; }
  .cardlink { color: var(--ah-text); text-decoration: none; min-width: 0; }
  .cardlink.body { display: flex; flex-direction: column; gap: 8px; }
  .cam { position: relative; aspect-ratio: 16 / 10; border-radius: 14px; overflow: hidden; background: var(--ah-panel); border: 1px solid var(--ah-panel-line); }
  .cam-prog { position: absolute; bottom: 8px; right: 8px; font-size: 12px; padding: 1px 8px; border-radius: 999px; background: rgba(20, 30, 38, 0.6); color: var(--ah-text); }
  .offhint { font-size: 12px; line-height: 1.45; min-height: 60px; }
  .cardbtns { display: flex; gap: 6px; padding-top: 8px; border-top: 1px solid var(--ah-row-line); }
  .cbtn { flex: 1; display: inline-flex; align-items: center; justify-content: center; height: 34px; background: var(--ah-glass); border: 1px solid var(--ah-field-line); color: var(--ah-text); border-radius: var(--ah-radius-btn-sm); cursor: pointer; text-decoration: none; }
  .cbtn:hover:not(:disabled) { border-color: rgba(255, 255, 255, 0.4); color: var(--ah-text); }
  .cbtn.danger { color: var(--ah-chip-bad-text); }
  .cbtn:disabled { opacity: 0.4; cursor: default; }
  .cardbtns.confirm { align-items: center; }
  .cf-q { flex: 1; font-size: 13px; color: var(--ah-text-2); }
  .clearchip { cursor: pointer; font: inherit; font-size: 12px; }
  .clearchip:disabled { opacity: 0.6; cursor: default; }
  .none { grid-column: 1 / -1; }

  .side { display: flex; flex-direction: column; gap: 14px; min-height: 0; overflow: auto; }
  .widget { display: flex; flex-direction: column; gap: 10px; margin: 0; }
  .w-head { display: flex; align-items: center; gap: 10px; }
  .w-head > div { flex: 1; min-width: 0; }
  .w-ic { width: 36px; height: 36px; flex: none; border-radius: 12px; background: var(--ah-chip-live-bg); display: grid; place-items: center; color: var(--ah-accent); }
  .clist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .clist li { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-top: 1px solid var(--ah-row-line); }
  .ctext { flex: 1; min-width: 0; display: flex; flex-direction: column; font-size: 13px; }
  .clist .dupwarn { display: flex; font-size: 13px; line-height: 1.45; }
  .btn.disabled { opacity: 0.5; cursor: not-allowed; pointer-events: none; }
  .fwlist { display: flex; flex-direction: column; }
  .fwrow { display: grid; grid-template-columns: 1fr auto auto; gap: 10px; align-items: center; padding: 7px 0; border-top: 1px solid var(--ah-row-line); font-size: 13px; }
  .fw p { margin: 0; }
  .tiny { font-size: 12px; }
  .err { color: var(--ophq-danger); }
  .connect-more summary { display: flex; align-items: center; gap: 10px; cursor: pointer; list-style: none; }
  .connect-more summary::-webkit-details-marker { display: none; }
  .connect-more summary > span:last-child { display: flex; flex-direction: column; }
  .connect-more-body { margin-top: 10px; }
  .empty { text-align: center; padding: 40px; display: flex; flex-direction: column; align-items: center; }
  .empty-ic { width: 56px; height: 56px; border-radius: 16px; background: var(--ah-chip-live-bg); color: var(--ah-accent); display: grid; place-items: center; margin-bottom: 10px; }
  .empty p { max-width: 48ch; margin: 8px auto 18px; }
  @media (max-width: 1099px) { .pg { grid-template-columns: 1fr; } .grid-card, .side { overflow: visible; } }
</style>
