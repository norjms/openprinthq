<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import { prettyModel } from '$lib/models.js';

  // Connection-type field sets (engine printer_capabilities.py is authoritative).
  const F = {
    name: { key: 'name', label: 'Display name', type: 'text', required: true, ph: 'Voron 2.4 #1' },
    ip: { key: 'ip_address', label: 'IP address', type: 'text', required: true, ph: '10.10.10.50' },
    serial: { key: 'serial_number', label: 'Serial number', type: 'text', required: true, ph: '01P00A...' },
    access: { key: 'access_code', label: 'Access code', type: 'text', required: true, ph: 'LAN access code' },
    port: { key: 'moonraker_port', label: 'Moonraker port', type: 'number', def: 7125 },
    apikey: { key: 'moonraker_api_key', label: 'API key', type: 'text', ph: 'optional' },
    apikeyReq: { key: 'moonraker_api_key', label: 'API key', type: 'text', required: true },
    model: { key: 'model', label: 'Model (optional)', type: 'text', ph: 'e.g. Voron 2.4 / X1C / MK4' }
  };

  const vendors = [
    { key: 'bambu', ct: 'bambu', name: 'Bambu Lab', sub: 'X1 · P1 · A1 · H2D', ex: 'X1 Carbon #1', fields: [F.name, F.ip, F.serial, F.access, F.model] },
    { key: 'klipper', ct: 'klipper', name: 'Klipper · Mainsail / Fluidd', sub: 'Voron · RatRig · Creality · Moonraker', ex: 'Voron 2.4 #1',
      note: 'Any Klipper printer exposed through Moonraker (Mainsail or Fluidd). Enter the IP and Moonraker port (default 7125). An API key is only needed if your Moonraker requires one.',
      fields: [F.name, F.ip, F.port, F.apikey, F.model] },
    { key: 'prusa', ct: 'prusalink', name: 'Prusa (PrusaLink)', sub: 'MK4 · XL · CORE One', ex: 'MK4 #1', fields: [F.name, F.ip, F.apikeyReq, F.model] },
    { key: 'octoprint', ct: 'octoprint', name: 'OctoPrint', sub: 'REST API', ex: 'Ender 3 #1', fields: [F.name, F.ip, F.apikeyReq, F.model] },
    { key: 'duet', ct: 'duet', name: 'Duet / RepRap', sub: 'DWC', ex: 'RatRig V-Core #1', fields: [F.name, F.ip, F.model] },
    { key: 'flashforge', ct: 'flashforge', name: 'FlashForge', sub: 'LAN', ex: 'Adventurer 5M #1', fields: [F.name, F.ip, F.model] },
    { key: 'mks', ct: 'mks', name: 'MKS', sub: 'WiFi module', ex: 'Sidewinder X2 #1', fields: [F.name, F.ip, F.model] },
    { key: 'snapmaker', ct: 'snapmaker', name: 'Snapmaker', sub: 'Artisan · J1 · 2.0', ex: 'Artisan #1',
      note: 'First connection: tap Allow on the printer’s touchscreen to authorize OpenPrintHQ.',
      fields: [F.name, F.ip, F.apikey, F.model] }
  ];
  const vendorByKey = Object.fromEntries(vendors.map((v) => [v.key, v]));

  // Catalog comm-mechanism -> our connection vendor. Covers the whole OrcaSlicer set.
  const MECH_TO_VENDOR = {
    bambu_mqtt: 'bambu', moonraker: 'klipper', creality_ws: 'klipper',
    octoprint: 'octoprint', repetier: 'octoprint', esp3d: 'octoprint',
    elegoo_sdcp: 'octoprint', marlin_serial: 'octoprint',
    prusalink: 'prusa', duet_rrf: 'duet', flashforge_tcp: 'flashforge', mks_tcp: 'mks'
  };
  const VENDOR_NAMES = {
    BBL: 'Bambu Lab', Creality: 'Creality', Prusa: 'Prusa', Voron: 'Voron', Ratrig: 'RatRig',
    Snapmaker: 'Snapmaker', Anycubic: 'Anycubic', Elegoo: 'Elegoo', Sovol: 'Sovol', Qidi: 'QIDI',
    FlashForge: 'FlashForge', Raise3D: 'Raise3D', Ultimaker: 'Ultimaker', Artillery: 'Artillery',
    BIQU: 'BIQU', TwoTrees: 'Two Trees', Kingroon: 'Kingroon', Vzbot: 'VzBot'
  };
  const vname = (c) => VENDOR_NAMES[c] || c;
  const vendorForRow = (r) => (r.vendor === 'Snapmaker' ? 'snapmaker' : (MECH_TO_VENDOR[r.mechanism_key] || 'octoprint'));

  // ---- catalog (search-first) ----
  let catalog = $state([]);
  let catalogErr = $state(null);
  let catalogLoading = $state(true);
  let query = $state('');

  onMount(async () => {
    try { const d = await api.printerCatalog({ limit: 2000 }); catalog = d.printers || []; }
    catch (e) { catalogErr = e.message || 'catalog unavailable'; }
    finally { catalogLoading = false; }
    try { deploymentMode = (await api.pubConfig()).deployment_mode || 'cloud'; } catch { /* */ }
    try { const cs = await api.connectors(); connectors = Array.isArray(cs) ? cs : (cs?.items || []); } catch { /* */ }
    // Deep-link from the Connectors "Scan LAN → Add" flow.
    try {
      const q = new URLSearchParams(window.location.search);
      const vendor = q.get('vendor'); const ip = q.get('ip');
      const conn = q.get('connector'); const serial = q.get('serial'); const model = q.get('model');
      if (conn) siteConnectorId = conn;
      if (vendor && vendorByKey[vendor]) {
        pickVendor(vendorByKey[vendor]);
        if (ip) values.ip_address = ip;
        if (serial) values.serial_number = serial;
        if (model) values.model = model;
        if (ip) pickedIp = ip;
      }
    } catch { /* */ }
  });

  const results = $derived.by(() => {
    const q = query.trim().toLowerCase();
    if (!q || q.length < 2) return [];
    const out = [];
    for (const r of catalog) {
      const hay = (vname(r.vendor) + ' ' + r.model).toLowerCase();
      const i = hay.indexOf(q);
      if (i >= 0) out.push({ r, score: i * 100 + (r.popularity_rank ?? 9) });
    }
    out.sort((a, b) => a.score - b.score);
    return out.slice(0, 40).map((s) => s.r);
  });
  const popular = $derived(catalog.filter((r) => (r.popularity_rank ?? 9) <= 1).slice(0, 10));

  const caps = (r) => {
    const c = [];
    if (r.is_multi_nozzle) c.push(r.nozzle_count + '-nozzle');
    if (r.has_chamber_heater) c.push('chamber');
    if (r.has_aux_fan) c.push('aux fan');
    return c;
  };

  let selected = $state(null);
  let pickedCatalog = $state(null);
  let values = $state({});
  let busy = $state(false);
  let err = $state(null);

  // ---- network discovery ----
  let subnet = $state('10.10.10.0/24');
  let scanning = $state(false);
  let scanProgress = $state({ scanned: 0, total: 0 });
  let discovered = $state([]);
  let scanned = $state(false);
  let scanErr = $state(null);
  let pickedIp = $state(null);
  let existing = $state([]);
  let hiddenCount = $state(0);

  // ---- sites (local connectors) ----
  let deploymentMode = $state('cloud');
  let connectors = $state([]);           // [{id,name,online}]
  let siteConnectorId = $state('');      // '' = Direct (same network as the engine)
  let siteScanning = $state(false);
  let siteScanMsg = $state('');
  const selectedSiteOnline = $derived(connectors.find((c) => String(c.id) === String(siteConnectorId))?.online ?? false);

  function alreadyAdded(p) {
    const serial = String(p.serial || '').trim().toUpperCase();
    const ip = String(p.ip_address || '').trim();
    const ct = selected?.ct;
    return existing.some((e) => {
      const eSerial = String(e.serial_number || '').trim().toUpperCase();
      const eIp = String(e.ip_address || '').trim();
      if (serial && eSerial && serial === eSerial) return true;
      if (ip && eIp && ip === eIp && e.connection_type === ct) return true;
      return false;
    });
  }

  function pick(v) {
    selected = v; err = null; values = {};
    discovered = []; scanned = false; scanErr = null; pickedIp = null;
    scanProgress = { scanned: 0, total: 0 };
    for (const f of v.fields) if (f.def !== undefined) values[f.key] = f.def;
  }

  function pickCatalog(r) {
    pickedCatalog = r;
    pick(vendorByKey[vendorForRow(r)] || vendorByKey.octoprint);
    values.model = r.model;
  }
  function pickVendor(v) { pickedCatalog = null; pick(v); }
  function changeSel() { selected = null; pickedCatalog = null; }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const isKlipper = $derived(selected?.ct === 'klipper');

  async function scan() {
    const klip = isKlipper;
    scanning = true; scanErr = null; discovered = []; scanned = false; hiddenCount = 0;
    scanProgress = { scanned: 0, total: 0 };
    try {
      try { existing = await api.printers() || []; } catch { /* keep prior */ }
      await (klip ? api.discoverKlipperScan(subnet, 1.5) : api.discoverScan(subnet, 1.5));
      for (let i = 0; i < 40; i++) {
        await sleep(1500);
        const s = await (klip ? api.discoverKlipperScanStatus() : api.discoverScanStatus());
        scanProgress = { scanned: s.scanned || 0, total: s.total || 0 };
        if (!s.running) break;
      }
      const raw = await (klip ? api.discoveredKlipperPrinters() : api.discoveredPrinters());
      const fresh = (raw || []).filter((p) => !alreadyAdded(p));
      hiddenCount = (raw || []).length - fresh.length;
      discovered = fresh;
    } catch (e) {
      scanErr = e.message || 'scan failed';
    } finally {
      scanning = false; scanned = true;
    }
  }

  // Scan the selected site's LAN through its connector (not the cloud engine).
  async function scanSiteLan() {
    if (!siteConnectorId) return;
    siteScanning = true; siteScanMsg = ''; scanErr = null; discovered = []; scanned = false; hiddenCount = 0;
    try {
      try { existing = await api.printers() || []; } catch { /* */ }
      const r = await api.discoverConnector(Number(siteConnectorId), 8000);
      if (!r.connector_online) { siteScanMsg = 'That site’s connector is offline — start the Cloud Client there, then scan again.'; }
      else {
        const raw = (r.devices || []).map((d) => ({ name: d.name, model: d.model, ip_address: d.ip, serial: d.serial, vendor: d.vendor }));
        const fresh = raw.filter((p) => !alreadyAdded(p));
        hiddenCount = raw.length - fresh.length;
        discovered = fresh;
        if (!raw.length) siteScanMsg = 'No printers announced on that LAN during the scan window. Confirm they’re on and in LAN mode.';
      }
    } catch (e) { siteScanMsg = e.message || 'scan failed'; }
    finally { siteScanning = false; scanned = true; }
  }

  function pickDiscovered(p) {
    values.name = values.name || p.name || p.model || (isKlipper ? 'Klipper printer' : 'Bambu printer');
    values.ip_address = p.ip_address;
    if (isKlipper) {
      values.moonraker_port = values.moonraker_port || 7125;
    } else {
      values.serial_number = p.serial;
      if (p.model) values.model = prettyModel(p.model);
    }
    pickedIp = p.ip_address;
  }

  async function submit(e) {
    e.preventDefault();
    busy = true; err = null;
    const body = { connection_type: selected.ct };
    for (const f of selected.fields) {
      const val = values[f.key];
      if (val !== undefined && val !== '') body[f.key] = f.type === 'number' ? Number(val) : val;
    }
    try {
      const created = await api.engine('/api/v1/printers/', { method: 'POST', body: JSON.stringify(body) });
      // Route this printer through the chosen site (connector). Empty = Direct.
      if (siteConnectorId) {
        const pid = created?.id ?? created?.printer_id;
        if (pid != null) {
          try { await api.savePrinterAutomation({ [pid]: { connector_id: Number(siteConnectorId) } }); }
          catch { /* route can be set later from Settings → Connectors */ }
        }
      }
      goto('/app/printers');
    } catch (e2) {
      err = e2.message || 'failed to add printer';
    } finally {
      busy = false;
    }
  }
</script>

<PageTitle page="Add printer" />

<div class="head">
  <h1>Add a printer</h1>
  <a href="/app/printers" class="btn btn-ghost btn-sm">← Back</a>
</div>

{#if !selected}
  <p class="muted lead">Search your printer by brand or model — we set up the right connection automatically. Everything connects through your private engine; nothing leaves your network.</p>

  <div class="finder card card-pad">
    <input class="input search" bind:value={query} autocomplete="off" spellcheck="false"
           placeholder="Search — e.g. X1 Carbon, Voron 2.4, Ender 3 V3, Prusa MK4…" aria-label="Search printers" />

    {#if query.trim().length >= 2}
      {#if results.length}
        <div class="results">
          {#each results as r}
            <button type="button" class="resrow" onclick={() => pickCatalog(r)}>
              <span class="rmain"><b class="rm">{r.model}</b><span class="rv muted">{vname(r.vendor)}</span></span>
              <span class="rmeta">
                <span class="conn">{vendorByKey[vendorForRow(r)]?.name?.split(' ')[0] ?? 'Host'}</span>
                {#each caps(r) as c}<span class="cap">{c}</span>{/each}
              </span>
            </button>
          {/each}
        </div>
      {:else if !catalogLoading}
        <p class="muted tiny nores">No match in the {catalog.length}-model catalog. Pick a platform below, or scan your network.</p>
      {/if}
    {:else}
      {#if popular.length}
        <div class="pop">
          <span class="muted tiny poplabel">Popular</span>
          {#each popular as r}
            <button type="button" class="chip" onclick={() => pickCatalog(r)}>{r.model}</button>
          {/each}
        </div>
      {/if}
      {#if catalogErr}<p class="muted tiny">Catalog unavailable ({catalogErr}) — pick a platform below.</p>{/if}
    {/if}

    <div class="scanrow2">
      <span class="muted tiny">On your network right now?</span>
      <button type="button" class="btn btn-ghost btn-sm" onclick={() => pickVendor(vendorByKey.bambu)}>Scan for Bambu</button>
      <button type="button" class="btn btn-ghost btn-sm" onclick={() => pickVendor(vendorByKey.klipper)}>Scan for Klipper</button>
    </div>
  </div>

  <details class="browse">
    <summary>Or browse by platform</summary>
    <div class="grid vend">
      {#each vendors as v}
        <button class="card card-pad vendor" type="button" onclick={() => pickVendor(v)}>
          <h3>{v.name}</h3>
          <span class="muted">{v.sub}</span>
        </button>
      {/each}
    </div>
  </details>
{:else}
  <form class="card card-pad form" onsubmit={submit}>
    <div class="flex between center">
      <div>
        <h3>{pickedCatalog ? pickedCatalog.model : selected.name}</h3>
        {#if pickedCatalog}<span class="muted tiny">{vname(pickedCatalog.vendor)} · connects via {selected.name}</span>{/if}
      </div>
      <button type="button" class="btn btn-ghost btn-sm" onclick={changeSel}>Change</button>
    </div>

    {#if deploymentMode === 'cloud'}
    <div class="field">
      <label for="site">Site {#if connectors.length}<span class="muted tiny">— which network is this printer on?</span>{/if}</label>
      {#if connectors.length}
        <select id="site" class="input" bind:value={siteConnectorId}>
          <option value="">Direct — same network as this instance</option>
          {#each connectors as c}<option value={String(c.id)}>{c.name}{c.online ? '' : ' (offline)'}</option>{/each}
        </select>
        {#if siteConnectorId}
          <p class="muted tiny">Reached through the <b>{connectors.find((c) => String(c.id) === String(siteConnectorId))?.name}</b> connector. {selectedSiteOnline ? '' : 'That connector is currently offline — the printer is saved and will connect when the Cloud Client is running there.'}</p>
        {/if}
      {:else}
        <p class="muted tiny">No local connectors yet. Printers are reached directly on this instance’s network. To add printers on a remote site, set up a connector in <a href="/app/settings">Settings → Connectors</a>.</p>
      {/if}
    </div>
    {/if}

    {#if siteConnectorId}
      <div class="discover">
        <div class="dtitle"><b>Find printers on this site’s network</b></div>
        <p class="muted tiny">Scans the <b>{connectors.find((c) => String(c.id) === String(siteConnectorId))?.name}</b> LAN through its connector and fills in what it finds.</p>
        <div class="scanrow">
          <button type="button" class="btn btn-primary btn-sm" onclick={scanSiteLan} disabled={siteScanning || !selectedSiteOnline}>{siteScanning ? 'Scanning…' : 'Scan this site'}</button>
          {#if !selectedSiteOnline}<span class="muted tiny">connector offline</span>{/if}
        </div>
        {#if siteScanMsg}<p class="muted tiny">{siteScanMsg}</p>{/if}
        {#if discovered.length}
          <div class="found">
            {#each discovered as p}
              <button type="button" class="foundrow" class:sel={pickedIp === p.ip_address} onclick={() => pickDiscovered(p)}>
                <span class="fn">{p.name || prettyModel(p.model) || 'Printer'}{#if p.model}<span class="muted mono"> · {prettyModel(p.model)}</span>{/if}</span>
                <span class="muted mono tiny">{p.ip_address}{#if p.serial} · {p.serial}{/if}</span>
              </button>
            {/each}
          </div>
          {#if hiddenCount}<p class="muted tiny">{hiddenCount} already-added hidden.</p>{/if}
        {/if}
      </div>
    {:else if deploymentMode === 'local' && (selected.ct === 'bambu' || selected.ct === 'klipper')}
      <div class="discover">
        <div class="dtitle"><b>Find printers on your network</b></div>
        {#if isKlipper}
          <p class="muted tiny">Probes your subnet for Klipper printers running Moonraker (port 7125) and fills in the IP — just give it a name.</p>
        {:else}
          <p class="muted tiny">Scans your subnet for Bambu printers in LAN mode and fills in the IP, serial &amp; model — you just add the access code.</p>
        {/if}
        <div class="scanrow">
          <input class="input" bind:value={subnet} placeholder="10.10.10.0/24" aria-label="Subnet to scan" />
          <button type="button" class="btn btn-primary btn-sm" onclick={scan} disabled={scanning}>
            {scanning ? 'Scanning…' : 'Scan'}
          </button>
        </div>
        {#if scanning}
          <div class="bar"><div class="fill" style="width:{scanProgress.total ? Math.round((scanProgress.scanned / scanProgress.total) * 100) : 5}%"></div></div>
          <p class="muted tiny">Scanned {scanProgress.scanned}{scanProgress.total ? ' / ' + scanProgress.total : ''} hosts…</p>
        {/if}
        {#if scanErr}<p class="err">{scanErr}</p>{/if}
        {#if discovered.length}
          <div class="found">
            {#each discovered as p}
              <button type="button" class="foundrow" class:sel={pickedIp === p.ip_address} onclick={() => pickDiscovered(p)}>
                <span class="fn">{p.name || prettyModel(p.model) || (isKlipper ? 'Klipper printer' : 'Bambu printer')}{#if p.model && !isKlipper}<span class="muted mono"> · {prettyModel(p.model)}</span>{/if}</span>
                <span class="muted mono tiny">{p.ip_address}{#if !isKlipper} · {p.serial}{/if}</span>
              </button>
            {/each}
          </div>
          {#if hiddenCount}<p class="muted tiny">{hiddenCount} already-added printer{hiddenCount > 1 ? 's' : ''} hidden.</p>{/if}
        {:else if scanned && !scanning}
          {#if hiddenCount}
            <p class="muted tiny">All {hiddenCount} discovered printer{hiddenCount > 1 ? 's are' : ' is'} already added.</p>
          {:else if isKlipper}
            <p class="muted tiny">No Klipper printers found. Confirm Moonraker is reachable on port 7125, or enter details manually below.</p>
          {:else}
            <p class="muted tiny">No printers found. Confirm LAN-only mode is on, or enter details manually below.</p>
          {/if}
        {/if}
      </div>
    {/if}

    {#each selected.fields as f}
      <div class="field">
        <label for={f.key}>{f.label}{f.required ? ' *' : ''}</label>
        <input id={f.key} class="input" type={f.type}
               placeholder={f.key === 'name' ? (selected.ex || f.ph || '') : (f.ph || '')}
               required={f.required} bind:value={values[f.key]} />
      </div>
    {/each}
    {#if selected.note}<p class="muted note">{selected.note}</p>{/if}
    {#if err}<p class="err">{err}</p>{/if}
    <div class="flex gap">
      <button class="btn btn-primary" disabled={busy}>{busy ? 'Connecting…' : 'Add printer'}</button>
      <a href="/app/printers" class="btn btn-ghost">Cancel</a>
    </div>
  </form>
{/if}

<style>
  .head { display: flex; align-items: center; justify-content: space-between; }
  .head h1 { margin: 0; }
  .lead { margin: 0.4rem 0 1.2rem; max-width: 62ch; }
  .finder { margin-bottom: 1rem; }
  .search { width: 100%; font-size: 1.05rem; padding: 0.8rem 1rem; }
  .results { display: flex; flex-direction: column; gap: 0.35rem; margin-top: 0.8rem; max-height: 46vh; overflow-y: auto; }
  .resrow { display: flex; align-items: center; justify-content: space-between; gap: 0.8rem; text-align: left;
    padding: 0.6rem 0.8rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm);
    background: var(--ophq-surface); cursor: pointer; transition: border 0.12s, background 0.12s; }
  .resrow:hover { border-color: var(--ophq-primary); background: var(--ophq-primary-dim); }
  .rmain { display: flex; flex-direction: column; gap: 0.1rem; min-width: 0; }
  .rm { color: var(--ophq-text); font-size: 0.96rem; }
  .rv { font-size: 0.8rem; }
  .rmeta { display: flex; align-items: center; gap: 0.35rem; flex-shrink: 0; }
  .conn { font-size: 0.72rem; font-weight: 600; color: var(--ophq-primary); background: var(--ophq-primary-dim);
    padding: 0.12rem 0.5rem; border-radius: 999px; }
  .cap { font-size: 0.68rem; color: var(--ophq-text-2); background: var(--ophq-bg-2);
    border: 1px solid var(--ophq-border); padding: 0.1rem 0.42rem; border-radius: 999px; }
  .nores { margin-top: 0.8rem; }
  .pop { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; margin-top: 0.8rem; }
  .poplabel { margin-right: 0.2rem; }
  .chip { font-size: 0.82rem; padding: 0.34rem 0.7rem; border: 1px solid var(--ophq-border);
    border-radius: 999px; background: var(--ophq-surface); color: var(--ophq-text); cursor: pointer; transition: border 0.12s; }
  .chip:hover { border-color: var(--ophq-primary); }
  .scanrow2 { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; margin-top: 1rem;
    padding-top: 0.9rem; border-top: 1px solid var(--ophq-border); }
  .browse { margin-top: 0.4rem; }
  .browse summary { cursor: pointer; color: var(--ophq-text-2); font-size: 0.9rem; padding: 0.3rem 0; user-select: none; }
  .vend { grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); margin: 0.8rem 0 0.4rem; }
  .vendor { text-align: left; cursor: pointer; transition: border 0.15s, transform 0.15s; color: var(--ophq-text); }
  .vendor:hover { border-color: var(--ophq-primary); transform: translateY(-2px); }
  .vendor h3 { margin: 0 0 0.3rem; font-size: 1.02rem; color: var(--ophq-text); }
  .form { max-width: 460px; }
  .form h3 { margin: 0 0 0.2rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .note { font-size: 0.83rem; margin: -0.2rem 0 0.6rem; line-height: 1.5; }
  .discover { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 1rem; margin: 0.6rem 0 1.2rem; background: var(--ophq-bg-2); }
  .dtitle { margin-bottom: 0.2rem; }
  .tiny { font-size: 0.8rem; }
  .scanrow { display: flex; gap: 0.5rem; margin: 0.7rem 0 0.4rem; }
  .scanrow .input { flex: 1; }
  .bar { height: 8px; background: var(--ophq-bg); border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; margin: 0.5rem 0 0.2rem; }
  .fill { height: 100%; background: linear-gradient(90deg, var(--ophq-primary), var(--ophq-primary-2)); transition: width 0.3s ease; }
  .found { display: flex; flex-direction: column; gap: 0.4rem; margin: 0.6rem 0; }
  .foundrow { display: flex; flex-direction: column; gap: 0.15rem; text-align: left; padding: 0.55rem 0.7rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); cursor: pointer; transition: border 0.15s; }
  .foundrow:hover { border-color: var(--ophq-primary); }
  .foundrow.sel { border-color: var(--ophq-primary); background: var(--ophq-primary-dim); }
  .foundrow .fn { font-weight: 600; color: var(--ophq-text); font-size: 0.92rem; }
</style>
