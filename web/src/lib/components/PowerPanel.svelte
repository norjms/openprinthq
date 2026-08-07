<script>
  // OpenPrintHQ — smart-plug power control + energy metering for one printer
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';

  let { printerId } = $props();

  let plug = $state(null);      // associated SmartPlug or null
  let status = $state(null);    // live { state, reachable, energy:{power,today,total,...} }
  let loading = $state(true);
  let loadFailed = $state(false);
  let acting = $state(false);
  let msg = $state(null);
  let poll = null;

  // add-plug form
  let adding = $state(false);
  let f = $state({ name: '', ip_address: '', auto_off: true, off_delay_minutes: 5, off_delay_mode: 'time', off_temp_threshold: 70 });

  async function load() {
    loading = true; msg = null; loadFailed = false;
    try {
      plug = await api.plugByPrinter(printerId);
      if (plug && plug.id) await refreshStatus();
      else plug = null;
    } catch (e) {
      // Swallowing this made a failed lookup indistinguishable from "no plug
      // configured": the panel offered the add form, Remove never rendered
      // because it needs a loaded plug, and the only clue was the server
      // rejecting the add as a duplicate. Say what actually happened.
      plug = null;
      loadFailed = true;
      msg = { kind: 'err', text: `Could not load this printer's plug: ${e.message || 'request failed'}` };
    } finally { loading = false; }
  }

  // Remove by printer, for when the plug cannot be loaded but the server still
  // says one is assigned. Without this the only way out was editing the database.
  async function forceRemoveByPrinter() {
    if (!confirm('Remove the plug currently assigned to this printer?')) return;
    acting = true; msg = null;
    try {
      const existing = await api.plugByPrinter(printerId).catch(() => null);
      if (existing?.id) await api.plugDelete(existing.id);
      plug = null; status = null; loadFailed = false;
      msg = { kind: 'ok', text: 'Removed. You can add a plug again now.' };
      await load();
    } catch (e) { msg = { kind: 'err', text: e.message || 'could not remove the assigned plug' }; }
    finally { acting = false; }
  }
  async function refreshStatus() {
    if (!plug?.id) return;
    try { status = await api.plugStatus(plug.id); } catch { status = null; }
  }

  $effect(() => {
    const _ = printerId;
    load();
    clearInterval(poll);
    poll = setInterval(() => { if (plug?.id && !acting) refreshStatus(); }, 12000);
    return () => clearInterval(poll);
  });

  const isOn = $derived((status?.state || plug?.last_state || '').toString().toUpperCase() === 'ON');
  const watts = $derived(status?.energy?.power);
  const kwhToday = $derived(status?.energy?.today);
  const kwhTotal = $derived(status?.energy?.total);

  let confirmOff = $state(false);
  async function control(action) {
    acting = true; msg = null;
    try {
      await api.plugControl(plug.id, action);
      await refreshStatus();
    } catch (e) { msg = { kind: 'err', text: e.message || 'control failed' }; }
    finally { acting = false; confirmOff = false; }
  }
  async function toggleAutoOff() {
    acting = true; msg = null;
    try { plug = await api.plugUpdate(plug.id, { auto_off: !plug.auto_off }); }
    catch (e) { msg = { kind: 'err', text: e.message || 'could not update' }; }
    finally { acting = false; }
  }
  async function removePlug() {
    acting = true; msg = null;
    try { await api.plugDelete(plug.id); plug = null; status = null; }
    catch (e) { msg = { kind: 'err', text: e.message || 'could not remove' }; }
    finally { acting = false; }
  }
  async function createPlug() {
    if (!f.name.trim() || !/^\d{1,3}(\.\d{1,3}){3}$/.test(f.ip_address)) {
      msg = { kind: 'err', text: 'Name and a valid Tasmota IP are required.' }; return;
    }
    acting = true; msg = null;
    try {
      plug = await api.plugCreate({
        name: f.name.trim(), plug_type: 'tasmota', ip_address: f.ip_address,
        printer_id: printerId, enabled: true, auto_on: true,
        auto_off: f.auto_off, off_delay_mode: f.off_delay_mode,
        off_delay_minutes: Number(f.off_delay_minutes) || 5,
        off_temp_threshold: Number(f.off_temp_threshold) || 70
      });
      adding = false;
      await refreshStatus();
    } catch (e) { msg = { kind: 'err', text: e.message || 'could not add plug' }; }
    finally { acting = false; }
  }

  const autoOffLabel = $derived(!plug ? '' :
    !plug.auto_off ? 'Auto power-off is off'
    : plug.off_delay_mode === 'temperature'
      ? `Auto power-off when bed cools below ${plug.off_temp_threshold}°C after a print`
      : `Auto power-off ${plug.off_delay_minutes} min after a print`);
</script>

<div class="card card-pad power">
  <div class="flex between center">
    <h3>Power</h3>
    {#if plug}
      <span class="chip {isOn ? 'ok' : ''}">{status && !status.reachable ? 'unreachable' : isOn ? 'on' : 'off'}</span>
    {/if}
  </div>

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if plug}
    <div class="pgrid">
      <div class="pm"><span class="muted">Draw</span><b>{watts != null ? Math.round(watts) + ' W' : '—'}</b></div>
      <div class="pm"><span class="muted">Today</span><b>{kwhToday != null ? kwhToday.toFixed(2) + ' kWh' : '—'}</b></div>
      <div class="pm"><span class="muted">Total</span><b>{kwhTotal != null ? kwhTotal.toFixed(1) + ' kWh' : '—'}</b></div>
    </div>
    <div class="prow">
      <span class="mono muted plabel">{plug.name}</span>
      <div class="flex gap center">
        {#if isOn}
          {#if confirmOff}
            <span class="muted tiny">Cut power?</span>
            <button class="btn btn-danger btn-sm" onclick={() => control('off')} disabled={acting}>Confirm</button>
            <button class="btn btn-ghost btn-sm" onclick={() => (confirmOff = false)}>Keep on</button>
          {:else}
            <button class="btn btn-ghost btn-sm" onclick={() => (confirmOff = true)} disabled={acting}>Turn off</button>
          {/if}
        {:else}
          <button class="btn btn-primary btn-sm" onclick={() => control('on')} disabled={acting}>Turn on</button>
        {/if}
      </div>
    </div>
    <label class="opt">
      <input type="checkbox" checked={plug.auto_off} onchange={toggleAutoOff} disabled={acting} />
      <span>{autoOffLabel}</span>
    </label>
    <button class="link-btn" onclick={removePlug} disabled={acting}>Remove plug</button>
  {:else if adding}
    <div class="form">
      <div class="fld"><label for="pn">Name</label><input id="pn" class="input" bind:value={f.name} placeholder="Printer plug" /></div>
      <div class="fld"><label for="pip">Tasmota IP</label><input id="pip" class="input" bind:value={f.ip_address} placeholder="10.10.10.60" /></div>
      <label class="opt"><input type="checkbox" bind:checked={f.auto_off} /><span>Auto power-off after a print</span></label>
      {#if f.auto_off}
        <div class="fld inline">
          <label for="pd">after</label>
          <input id="pd" class="input sm" type="number" min="0" max="60" bind:value={f.off_delay_minutes} /><span class="muted">min</span>
        </div>
      {/if}
      <div class="flex gap">
        <button class="btn btn-primary btn-sm" onclick={createPlug} disabled={acting}>{acting ? 'Adding…' : 'Add plug'}</button>
        <button class="btn btn-ghost btn-sm" onclick={() => (adding = false)}>Cancel</button>
      </div>
      {#if msg?.kind === 'err' && /already has/i.test(msg.text || '')}
        <!-- The add was refused because a plug is assigned but could not be
             shown. Offer the way out here, where the problem is visible. -->
        <p class="tiny muted">
          A plug is already assigned to this printer but could not be loaded.
        </p>
        <button class="btn btn-danger btn-sm" onclick={forceRemoveByPrinter} disabled={acting}>
          Remove the assigned plug
        </button>
      {/if}
    </div>
  {:else if loadFailed}
    <p class="muted">
      A plug may be assigned to this printer, but it could not be loaded. Adding a new one
      will be refused until the existing assignment is removed.
    </p>
    <div class="flex gap">
      <button class="btn btn-ghost btn-sm" onclick={load} disabled={acting}>Retry</button>
      <button class="btn btn-danger btn-sm" onclick={forceRemoveByPrinter} disabled={acting}>Remove the assigned plug</button>
    </div>
  {:else}
    <p class="muted">No smart plug linked. Add a Tasmota plug for real power control, live watts, and auto power-off after prints.</p>
    <button class="btn btn-ghost btn-sm" onclick={() => (adding = true)}>+ Link smart plug</button>
  {/if}
  {#if msg}<p class={msg.kind === 'ok' ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}
</div>

<style>
  .power { margin-top: 1.2rem; }
  .power h3 { margin: 0; font-size: 1.05rem; }
  .pgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.8rem; margin: 0.9rem 0; }
  .pm { display: flex; flex-direction: column; gap: 0.2rem; }
  .pm b { font-family: var(--font-mono); font-size: 1.15rem; }
  .prow { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding-top: 0.4rem; border-top: 1px solid var(--ophq-border-soft); }
  .plabel { font-size: 0.82rem; }
  .opt { display: flex; align-items: center; gap: 0.5rem; margin: 0.7rem 0 0.3rem; font-size: 0.88rem; color: var(--ophq-text-2); cursor: pointer; }
  .opt input { width: auto; accent-color: var(--ophq-primary); }
  .link-btn { background: none; border: none; color: var(--ophq-muted); font-size: 0.8rem; cursor: pointer; padding: 0; text-decoration: underline; }
  .form { display: flex; flex-direction: column; gap: 0.6rem; margin-top: 0.6rem; }
  .fld { display: flex; flex-direction: column; gap: 0.3rem; }
  .fld.inline { flex-direction: row; align-items: center; gap: 0.5rem; }
  .fld label { font-size: 0.82rem; color: var(--ophq-text-2); }
  .input.sm { max-width: 90px; }
  .chip.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .tiny { font-size: 0.8rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; margin: 0.6rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; margin: 0.6rem 0 0; }
</style>
