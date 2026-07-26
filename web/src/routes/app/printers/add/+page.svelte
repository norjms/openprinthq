<script>
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';

  // Connection types are the engine's authoritative set (printer_capabilities.py).
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
    { key: 'bambu', ct: 'bambu', name: 'Bambu Lab', sub: 'X1 · P1 · A1 · H2D', fields: [F.name, F.ip, F.serial, F.access, F.model] },
    { key: 'klipper', ct: 'klipper', name: 'Klipper · Mainsail / Fluidd', sub: 'Voron · RatRig · Creality · Moonraker',
      note: 'Any Klipper printer exposed through Moonraker (Mainsail or Fluidd) — Voron, RatRig, Creality K1/K2, custom builds. Enter the printer’s IP and Moonraker port (default 7125). An API key is only needed if your Moonraker requires one.',
      fields: [F.name, F.ip, F.port, F.apikey, F.model] },
    { key: 'prusa', ct: 'prusalink', name: 'Prusa (PrusaLink)', sub: 'MK4 · XL · CORE One', fields: [F.name, F.ip, F.apikeyReq, F.model] },
    { key: 'octoprint', ct: 'octoprint', name: 'OctoPrint', sub: 'REST API', fields: [F.name, F.ip, F.apikeyReq, F.model] },
    { key: 'duet', ct: 'duet', name: 'Duet / RepRap', sub: 'DWC', fields: [F.name, F.ip, F.model] },
    { key: 'flashforge', ct: 'flashforge', name: 'FlashForge', sub: 'LAN', fields: [F.name, F.ip, F.model] },
    { key: 'mks', ct: 'mks', name: 'MKS', sub: 'WiFi module', fields: [F.name, F.ip, F.model] },
    { key: 'snapmaker', ct: 'snapmaker', name: 'Snapmaker', sub: 'Artisan · J1 · 2.0',
      note: 'First connection: tap Allow on the printer’s touchscreen to authorize OpenPrintHQ.',
      fields: [F.name, F.ip, F.apikey, F.model] }
  ];

  let selected = $state(null);
  let values = $state({});
  let busy = $state(false);
  let err = $state(null);

  // ---- Bambu network discovery ----
  let subnet = $state('10.10.10.0/24');
  let scanning = $state(false);
  let scanProgress = $state({ scanned: 0, total: 0 });
  let discovered = $state([]);
  let scanned = $state(false);
  let scanErr = $state(null);
  let pickedIp = $state(null);

  function pick(v) {
    selected = v; err = null; values = {};
    discovered = []; scanned = false; scanErr = null; pickedIp = null;
    scanProgress = { scanned: 0, total: 0 };
    for (const f of v.fields) if (f.def !== undefined) values[f.key] = f.def;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Discovery is offered for two vendors with different engine endpoints:
  // Bambu (SSDP/port scan) and Klipper (Moonraker port-7125 probe).
  const isKlipper = $derived(selected?.ct === 'klipper');

  async function scan() {
    const klip = isKlipper;
    scanning = true; scanErr = null; discovered = []; scanned = false;
    scanProgress = { scanned: 0, total: 0 };
    try {
      await (klip ? api.discoverKlipperScan(subnet, 1.5) : api.discoverScan(subnet, 1.5));
      for (let i = 0; i < 40; i++) {
        await sleep(1500);
        const s = await (klip ? api.discoverKlipperScanStatus() : api.discoverScanStatus());
        scanProgress = { scanned: s.scanned || 0, total: s.total || 0 };
        if (!s.running) break;
      }
      discovered = await (klip ? api.discoveredKlipperPrinters() : api.discoveredPrinters());
    } catch (e) {
      scanErr = e.message || 'scan failed';
    } finally {
      scanning = false; scanned = true;
    }
  }

  function pickDiscovered(p) {
    values.name = values.name || p.name || p.model || (isKlipper ? 'Klipper printer' : 'Bambu printer');
    values.ip_address = p.ip_address;
    if (isKlipper) {
      // Moonraker printers are reached by IP + port; no serial/access code.
      values.moonraker_port = values.moonraker_port || 7125;
    } else {
      values.serial_number = p.serial;
      if (p.model) values.model = p.model;
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
      await api.engine('/api/v1/printers/', { method: 'POST', body: JSON.stringify(body) });
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
  <p class="muted lead">Pick your printer's platform. OpenPrintHQ connects through your private engine — nothing leaves your network.</p>
  <div class="grid vend">
    {#each vendors as v}
      <button class="card card-pad vendor" type="button" onclick={() => pick(v)}>
        <h3>{v.name}</h3>
        <span class="muted">{v.sub}</span>
      </button>
    {/each}
  </div>
  <p class="muted small">Running Klipper on a Voron, RatRig or Creality? Use <b>Klipper · Mainsail / Fluidd</b> — it connects through Moonraker.</p>
{:else}
  <form class="card card-pad form" onsubmit={submit}>
    <div class="flex between center">
      <h3>{selected.name}</h3>
      <button type="button" class="btn btn-ghost btn-sm" onclick={() => (selected = null)}>Change</button>
    </div>

    {#if selected.ct === 'bambu' || selected.ct === 'klipper'}
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
                <span class="fn">{p.name || p.model || (isKlipper ? 'Klipper printer' : 'Bambu printer')}{#if p.model && !isKlipper}<span class="muted mono"> · {p.model}</span>{/if}</span>
                <span class="muted mono tiny">{p.ip_address}{#if !isKlipper} · {p.serial}{/if}</span>
              </button>
            {/each}
          </div>
          {#if isKlipper}
            <p class="muted tiny">Selected the printer? Give it a <b>display name</b> below. Add an API key only if your Moonraker requires one.</p>
          {:else}
            <p class="muted tiny">Selected the printer? Enter its <b>access code</b> below (Settings → LAN-only mode on the printer's screen).</p>
          {/if}
        {:else if scanned && !scanning}
          {#if isKlipper}
            <p class="muted tiny">No Klipper printers found. Confirm Moonraker is reachable on port 7125 and the subnet is right, or enter the details manually below.</p>
          {:else}
            <p class="muted tiny">No printers found. Confirm LAN-only mode is on and the subnet is right, or enter the details manually below.</p>
          {/if}
        {/if}
      </div>
    {/if}

    {#each selected.fields as f}
      <div class="field">
        <label for={f.key}>{f.label}{f.required ? ' *' : ''}</label>
        <input id={f.key} class="input" type={f.type} placeholder={f.ph || ''}
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
  .lead { margin: 0.4rem 0 1.4rem; max-width: 58ch; }
  .vend { grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); margin-bottom: 1.2rem; }
  .vendor { text-align: left; cursor: pointer; transition: border 0.15s, transform 0.15s; color: var(--ophq-text); }
  .vendor h3 { color: var(--ophq-text); }
  .vendor:hover { border-color: var(--ophq-primary); transform: translateY(-2px); }
  .vendor h3 { margin: 0 0 0.3rem; font-size: 1.02rem; }
  .small { margin-top: 1rem; font-size: 0.88rem; }
  .form { max-width: 460px; }
  .form h3 { margin: 0 0 0.4rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .note { font-size: 0.83rem; margin: -0.2rem 0 0.6rem; line-height: 1.5; }

  .discover { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 1rem; margin: 0.4rem 0 1.2rem; background: var(--ophq-bg-2); }
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
