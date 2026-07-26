<script>
  // OpenPrintHQ — bed ejection & continuous printing (#20).
  // Stores a per-printer "eject" g-code macro (engine settings) and can run it
  // on demand or automatically when a print finishes. Continuous printing
  // itself is handled by the engine queue scheduler (it auto-starts the next
  // non-held job on an idle printer); this panel adds the part-removal step in
  // between so prints don't collide. The auto-eject watcher here runs while the
  // dashboard is open; always-on server-side orchestration is a planned
  // follow-up (see progress notes).
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';

  let { printerId, connected = false, kind = '', status = null } = $props();

  let macro = $state('');
  let auto = $state(false);
  let loaded = $state(false);
  let saving = $state(false);
  let confirmEject = $state(false);
  let busy = $state(false);
  let msg = $state(null);

  const PLACEHOLDER = $derived((kind || '').toLowerCase() === 'klipper'
    ? 'e.g. a Klipper macro that sweeps the bed:\nEJECT_PART\n; or raw moves: G1 Z50 F600 ...'
    : 'Raw g-code to push the finished part off the plate, one command per line.');

  async function loadCfg() {
    try {
      const all = await api.printerAutomation().catch(() => ({}));
      const c = all && (all[printerId] || all[String(printerId)]);
      if (c) { macro = c.eject_gcode || ''; auto = !!c.auto_eject; }
    } catch { /* no instance */ }
    finally { loaded = true; }
  }
  $effect(() => { if (!loaded) loadCfg(); });

  async function save() {
    saving = true; msg = null;
    try { await api.savePrinterAutomation({ [printerId]: { auto_eject: auto, eject_gcode: macro } }); msg = { k: 'ok', t: 'Saved.' }; }
    catch (e) { msg = { k: 'err', t: e.message || 'could not save' }; }
    finally { saving = false; }
  }

  async function ejectNow() {
    if (!macro.trim()) { msg = { k: 'err', t: 'Add an eject macro first.' }; confirmEject = false; return; }
    busy = true; msg = null;
    try { await api.sendGcode(printerId, macro.trim()); msg = { k: 'ok', t: 'Eject sequence sent.' }; }
    catch (e) { msg = { k: 'err', t: e.message || 'eject failed' }; }
    finally { busy = false; confirmEject = false; }
  }

  // Client-side auto-eject: fire once on the transition into a finished state.
  let lastState = $state(null);
  let firedFor = $state(null);
  const isFinished = (s) => /finish|complete|success|idle/i.test(String(s || ''));
  const isPrinting = (s) => /print|run/i.test(String(s || ''));
  $effect(() => {
    const s = String(status?.state || status?.print_status || '').toLowerCase();
    if (!s) return;
    const job = status?.subtask_name || status?.gcode_file || status?.current_print || '';
    if (auto && macro.trim() && connected && isPrinting(lastState) && isFinished(s) && firedFor !== job) {
      firedFor = job;
      api.sendGcode(printerId, macro.trim()).then(() => (msg = { k: 'ok', t: 'Auto-eject sent (print finished).' })).catch(() => {});
    }
    lastState = s;
  });
</script>

<div class="card card-pad eject">
  <div class="eh"><span class="eyebrow">Bed ejection &amp; continuous printing</span></div>
  <p class="muted intro">Store a macro that clears the finished part off the plate. Run it on demand, or let it fire automatically when a print finishes so the queue can roll straight into the next job. Continuous printing itself is automatic — the queue starts the next un-held job on an idle printer.</p>

  <label class="fld"><span>Eject macro</span>
    <textarea class="input mac" rows="3" bind:value={macro} placeholder={PLACEHOLDER} spellcheck="false"></textarea>
  </label>

  <label class="opt"><input type="checkbox" bind:checked={auto} /><span>Auto-eject when a print finishes <span class="muted">(while this dashboard is open)</span></span></label>

  <div class="flex gap acts">
    <button class="btn btn-primary btn-sm" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
    {#if confirmEject}
      <span class="cfm">Run eject on the printer now?
        <button class="btn btn-primary btn-sm" onclick={ejectNow} disabled={busy}>{busy ? 'Sending…' : 'Confirm'}</button>
        <button class="btn btn-ghost btn-sm" onclick={() => (confirmEject = false)}>Cancel</button>
      </span>
    {:else}
      <button class="btn btn-ghost btn-sm" onclick={() => (confirmEject = true)} disabled={!connected || !macro.trim()} title={connected ? 'Run the eject macro now' : 'Printer offline'}>⏏ Eject part now</button>
    {/if}
  </div>
  {#if msg}<p class={msg.k === 'ok' ? 'ok-msg' : 'err'}>{msg.t}</p>{/if}
  <p class="muted tiny">Tip: leave queued jobs un-held (not “manual start”) and they’ll begin automatically on this printer once it’s idle. For strict per-circuit heat-up staggering across many printers, use the batch tools on the Files/Queue pages.</p>
</div>

<style>
  .eject { margin-top: 1.2rem; }
  .intro { font-size: 0.86rem; margin: 0.3rem 0 1rem; max-width: 72ch; }
  .fld { display: flex; flex-direction: column; gap: 0.35rem; }
  .fld span { font-size: 0.8rem; color: var(--ophq-text-2); }
  .mac { font-family: var(--font-mono); font-size: 0.82rem; resize: vertical; }
  .opt { display: flex; align-items: center; gap: 0.5rem; font-size: 0.88rem; color: var(--ophq-text-2); margin: 0.8rem 0 0.2rem; }
  .opt input { width: auto; accent-color: var(--ophq-primary); }
  .acts { margin-top: 0.9rem; align-items: center; }
  .cfm { display: inline-flex; align-items: center; gap: 0.5rem; font-size: 0.86rem; color: var(--ophq-text-2); }
  .ok-msg { color: var(--ophq-success); font-size: 0.86rem; margin: 0.7rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.86rem; margin: 0.7rem 0 0; }
  .tiny { font-size: 0.76rem; margin-top: 0.9rem; }
</style>
