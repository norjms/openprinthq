<script>
  // OpenPrintHQ — per-printer maintenance (runtime-based reminders).
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';

  let { printerId } = $props();

  let ov = $state(null);
  let loading = $state(true);
  let busy = $state(null);
  let msg = $state(null);

  async function load() {
    loading = true;
    try { ov = await api.maintenancePrinter(printerId); } catch { ov = null; }
    finally { loading = false; }
  }
  $effect(() => { const _ = printerId; load(); });

  function status(it) {
    const until = Number(it.hours_until_due);
    const interval = Number(it.interval_hours) || 1;
    if (until <= 0) return 'due';
    if (until <= interval * 0.1) return 'soon';
    return 'ok';
  }
  async function logDone(it) {
    busy = it.id; msg = null;
    try { await api.maintenancePerform(it.id, {}); msg = { kind: 'ok', text: `Logged: ${it.maintenance_type_name}.` }; await load(); }
    catch (e) { msg = { kind: 'err', text: e.message || 'could not log' }; }
    finally { busy = null; }
  }
  const items = $derived((ov?.maintenance_items || []).filter((i) => i.enabled !== false));
</script>

{#if !loading && ov}
  <div class="card card-pad maint">
    <div class="mh">
      <h3>Maintenance</h3>
      <span class="muted tiny">{(ov.total_print_hours ?? 0).toFixed(0)} print-hours{#if ov.due_count > 0} · <span class="due-t">{ov.due_count} due</span>{/if}</span>
    </div>
    {#if items.length === 0}
      <p class="muted small">No maintenance schedule for this printer.</p>
    {:else}
      <div class="mlist">
        {#each items as it (it.id)}
          {@const s = status(it)}
          <div class="mrow {s}">
            <span class="mn">{it.maintenance_type_name}</span>
            <span class="mmeta mono">
              {#if s === 'due'}<span class="chip danger">due now</span>
              {:else if s === 'soon'}<span class="chip accent">{Math.max(0, it.hours_until_due).toFixed(0)}h left</span>
              {:else}<span class="muted">{it.hours_until_due.toFixed(0)}h left</span>{/if}
              <span class="muted every">every {it.interval_hours}h</span>
            </span>
            <button class="btn btn-ghost btn-xs" onclick={() => logDone(it)} disabled={busy === it.id}>Log done</button>
          </div>
        {/each}
      </div>
    {/if}
    {#if msg}<p class={msg.kind === 'ok' ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}
  </div>
{/if}

<style>
  .maint { margin-top: 1.2rem; }
  .mh { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.8rem; }
  .mh h3 { margin: 0; font-size: 1.05rem; }
  .due-t { color: var(--ophq-danger); }
  .mlist { display: flex; flex-direction: column; gap: 0.4rem; }
  .mrow { display: grid; grid-template-columns: 1fr auto auto; gap: 1rem; align-items: center; padding: 0.5rem 0.7rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); }
  .mrow.due { border-color: rgba(255,92,108,0.3); }
  .mn { font-size: 0.9rem; }
  .mmeta { display: flex; align-items: center; gap: 0.7rem; font-size: 0.78rem; }
  .every { font-size: 0.72rem; }
  .chip.danger { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .chip.accent { color: var(--ophq-accent); border-color: rgba(255,176,32,0.3); background: rgba(255,176,32,0.08); }
  .small { font-size: 0.9rem; }
  .btn-xs { padding: 0.12rem 0.5rem; font-size: 0.72rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.88rem; margin: 0.7rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0.7rem 0 0; }
</style>
