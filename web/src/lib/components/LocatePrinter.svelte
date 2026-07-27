<script>
  // OpenPrintHQ — locate an offline printer on the network and relink it.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { relocate, relink, canAutoRelocate, markAutoRelocate } from '$lib/relocate.js';
  import { api } from '$lib/api';

  // `printer` must carry: id, name, ip_address, serial_number, mac_address,
  // connection_type. `auto` runs one scan automatically (rate-limited).
  let { printer, auto = false, compact = false, onrelinked } = $props();

  let phase = $state('idle');   // idle | scanning | found | candidate | offline | error
  let result = $state(null);    // { device, newIp, changed } | { devices }
  let busy = $state(false);     // a relink/reconnect in flight
  let err = $state(null);
  let started = false;

  async function locate() {
    if (busy || phase === 'scanning') return;
    phase = 'scanning'; err = null; result = null;
    try {
      const r = await relocate(printer, api);
      if (r.status === 'found') { result = r; phase = 'found'; }
      else if (r.status === 'candidate') { result = r; phase = 'candidate'; }
      else { phase = 'offline'; }
    } catch (e) {
      err = e?.message || 'scan failed'; phase = 'error';
    }
  }

  async function doRelink(device) {
    busy = true; err = null;
    try {
      await relink(printer, device, api);
      onrelinked?.(device.ip_address);
      phase = 'idle'; result = null;
    } catch (e) {
      err = e?.message || 'relink failed'; phase = 'error';
    } finally { busy = false; }
  }

  // Auto-locate once when mounted for an offline printer (rate-limited).
  $effect(() => {
    if (auto && !started && printer?.id != null && canAutoRelocate(printer.id)) {
      started = true; markAutoRelocate(printer.id); locate();
    }
  });
</script>

<div class="locate" class:compact>
  {#if phase === 'idle'}
    <button class="btn btn-ghost btn-sm" onclick={locate}>⌖ Locate on network</button>
  {:else if phase === 'scanning'}
    <span class="row muted"><span class="spin" aria-hidden="true"></span> Searching the network for {printer.name}…</span>
  {:else if phase === 'found'}
    {#if result.changed}
      <div class="prompt">
        <span class="msg"><b>{printer.name}</b> found at a new address <span class="mono">{result.newIp}</span> (was <span class="mono">{printer.ip_address}</span>). Its IP changed — relink this printer to it?</span>
        <div class="acts">
          <button class="btn btn-primary btn-sm" onclick={() => doRelink(result.device)} disabled={busy}>{busy ? 'Relinking…' : `Relink to ${result.newIp}`}</button>
          <button class="btn btn-ghost btn-sm" onclick={() => (phase = 'idle')} disabled={busy}>Dismiss</button>
        </div>
      </div>
    {:else}
      <div class="prompt">
        <span class="msg"><b>{printer.name}</b> is reachable at <span class="mono">{result.newIp}</span> but the connection dropped. Reconnect?</span>
        <div class="acts">
          <button class="btn btn-primary btn-sm" onclick={() => doRelink(result.device)} disabled={busy}>{busy ? 'Reconnecting…' : 'Reconnect'}</button>
          <button class="btn btn-ghost btn-sm" onclick={() => (phase = 'idle')} disabled={busy}>Dismiss</button>
        </div>
      </div>
    {/if}
  {:else if phase === 'candidate'}
    <div class="prompt">
      <span class="msg">Couldn't confirm <b>{printer.name}</b> by hardware id yet. Found {result.devices.length} Klipper printer{result.devices.length > 1 ? 's' : ''} on the network — is one of these it?</span>
      <div class="cands">
        {#each result.devices as d}
          <button class="cand" onclick={() => doRelink(d)} disabled={busy}>
            <span class="cn">{d.name || 'Klipper printer'}</span>
            <span class="mono muted tiny">{d.ip_address}{#if d.mac} · {d.mac}{/if}</span>
          </button>
        {/each}
      </div>
      <button class="btn btn-ghost btn-sm" onclick={() => (phase = 'idle')} disabled={busy}>None of these</button>
    </div>
  {:else if phase === 'offline'}
    <span class="row muted">Not found on the network — {printer.name} appears to be truly offline. <button class="link" onclick={locate}>Scan again</button></span>
  {:else if phase === 'error'}
    <span class="row err">{err} <button class="link" onclick={locate}>Retry</button></span>
  {/if}
</div>

<style>
  .locate { font-size: 0.9rem; }
  .row { display: inline-flex; align-items: center; gap: 0.45rem; }
  .prompt { display: flex; flex-direction: column; gap: 0.6rem; padding: 0.8rem 0.9rem; border: 1px solid var(--ophq-primary); border-radius: var(--radius-sm); background: var(--ophq-primary-dim); }
  .compact .prompt { padding: 0.6rem 0.7rem; }
  .msg { line-height: 1.5; }
  .acts { display: flex; gap: 0.5rem; }
  .cands { display: flex; flex-direction: column; gap: 0.4rem; }
  .cand { display: flex; flex-direction: column; gap: 0.1rem; text-align: left; padding: 0.5rem 0.7rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); cursor: pointer; }
  .cand:hover:not(:disabled) { border-color: var(--ophq-primary); }
  .cn { font-weight: 600; }
  .tiny { font-size: 0.78rem; }
  .link { background: none; border: none; color: var(--ophq-primary-2); cursor: pointer; padding: 0; font: inherit; text-decoration: underline; }
  .err { color: var(--ophq-danger); }
  .spin { width: 13px; height: 13px; border: 2px solid var(--ophq-border); border-top-color: var(--ophq-primary); border-radius: 50%; display: inline-block; animation: sp 0.8s linear infinite; }
  @keyframes sp { to { transform: rotate(360deg); } }
</style>
