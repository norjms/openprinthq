<script>
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';

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
    { key: 'klipper', ct: 'klipper', name: 'Creality / Voron (Klipper)', sub: 'Moonraker', fields: [F.name, F.ip, F.port, F.apikey, F.model] },
    { key: 'prusa', ct: 'prusalink', name: 'Prusa (PrusaLink)', sub: 'MK4 · XL · CORE One', fields: [F.name, F.ip, F.apikeyReq, F.model] },
    { key: 'octoprint', ct: 'octoprint', name: 'OctoPrint', sub: 'REST API', fields: [F.name, F.ip, F.apikeyReq, F.model] },
    { key: 'duet', ct: 'duet', name: 'Duet / RepRap', sub: 'DWC', fields: [F.name, F.ip, F.model] },
    { key: 'flashforge', ct: 'flashforge', name: 'FlashForge', sub: 'LAN', fields: [F.name, F.ip, F.model] },
    { key: 'mks', ct: 'mks', name: 'MKS', sub: 'WiFi module', fields: [F.name, F.ip, F.model] }
  ];

  let selected = $state(null);
  let values = $state({});
  let busy = $state(false);
  let err = $state(null);

  function pick(v) {
    selected = v; err = null; values = {};
    for (const f of v.fields) if (f.def !== undefined) values[f.key] = f.def;
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
      err = e2.detail?.detail || e2.detail?.error || e2.message || 'failed to add printer';
      if (Array.isArray(err)) err = err.map((x) => x.msg || JSON.stringify(x)).join('; ');
    } finally {
      busy = false;
    }
  }
</script>

<svelte:head><title>Add printer · OpenPrintHQ</title></svelte:head>

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
  <div class="card card-pad soon">
    <b>Snapmaker</b> <span class="chip accent">adapter needed</span>
    <p class="muted">Not yet supported by the engine — a Snapmaker adapter is net-new work on the roadmap.</p>
  </div>
{:else}
  <form class="card card-pad form" onsubmit={submit}>
    <div class="flex between center">
      <h3>{selected.name}</h3>
      <button type="button" class="btn btn-ghost btn-sm" onclick={() => (selected = null)}>Change</button>
    </div>
    {#each selected.fields as f}
      <div class="field">
        <label for={f.key}>{f.label}{f.required ? ' *' : ''}</label>
        <input id={f.key} class="input" type={f.type} placeholder={f.ph || ''}
               required={f.required} bind:value={values[f.key]} />
      </div>
    {/each}
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
  .vendor { text-align: left; cursor: pointer; transition: border 0.15s, transform 0.15s; }
  .vendor:hover { border-color: var(--ophq-primary); transform: translateY(-2px); }
  .vendor h3 { margin: 0 0 0.3rem; font-size: 1.02rem; }
  .soon { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
  .soon p { width: 100%; margin: 0.3rem 0 0; font-size: 0.9rem; }
  .form { max-width: 460px; }
  .form h3 { margin: 0 0 0.4rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
</style>
