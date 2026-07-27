<script>
  // OpenPrintHQ — per-printer settings, edited in a popup layer (not a route).
  // Persisted on the printer record in the database (source of truth) via PATCH.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';

  let {
    printerId, name = 'Printer', isKlipper = false,
    chamberHeater = false, showFilamentPanel = true, showBedEjection = false,
    onclose, onsave, ondelete
  } = $props();

  // Local editable copy, seeded from the record's current values.
  let cfg = $state({
    chamberHeater: !!chamberHeater,
    showFilamentPanel: showFilamentPanel !== false,
    showBedEjection: !!showBedEjection
  });
  let saving = $state(false);
  let err = $state(null);
  let confirmDelete = $state(false);
  let deleting = $state(false);

  async function save() {
    saving = true; err = null;
    try {
      await api.updatePrinter(printerId, {
        chamber_heater: cfg.chamberHeater,
        show_filament_panel: cfg.showFilamentPanel,
        show_bed_ejection: cfg.showBedEjection
      });
      onsave?.({ chamber_heater: cfg.chamberHeater, show_filament_panel: cfg.showFilamentPanel, show_bed_ejection: cfg.showBedEjection });
      onclose?.();
    } catch (e) {
      err = e?.message || 'could not save settings';
    } finally { saving = false; }
  }

  async function doDelete() {
    deleting = true; err = null;
    try {
      await api.deletePrinter(printerId);
      ondelete?.();          // parent navigates away
    } catch (e) {
      err = e?.message || 'could not delete printer';
      deleting = false;
    }
  }
</script>

<div class="overlay" role="presentation" onclick={() => onclose?.()}>
  <div class="modal card" role="dialog" aria-modal="true" aria-label="Printer settings" tabindex="-1"
       onclick={(e) => e.stopPropagation()}>
    <div class="mhead">
      <div>
        <h3>Printer settings</h3>
        <span class="muted tiny">{name}</span>
      </div>
      <button class="btn btn-ghost btn-sm" onclick={() => onclose?.()} aria-label="Close">✕</button>
    </div>

    <div class="mbody">
      <section>
        <h4>Hardware</h4>
        <label class="opt">
          <input type="checkbox" bind:checked={cfg.chamberHeater} />
          <span>
            Chamber heater
            <span class="muted tiny block">
              Turn on if this printer has a controllable chamber heater. A chamber
              temperature control appears below Bed on the Temperatures panel.
              {#if isKlipper}Klipper: exposed as a <span class="mono">heater_generic</span> named <span class="mono">chamber</span>.{/if}
            </span>
          </span>
        </label>
      </section>

      <section>
        <h4>Panels</h4>
        <label class="opt">
          <input type="checkbox" bind:checked={cfg.showFilamentPanel} />
          <span>
            Multi-material unit panel
            <span class="muted tiny block">
              Show the multi-material filament panel (Bambu AMS, Creality CFS, Prusa
              MMU…) when a unit is connected. Hidden automatically if none is detected.
            </span>
          </span>
        </label>
        <label class="opt">
          <input type="checkbox" bind:checked={cfg.showBedEjection} />
          <span>
            Bed ejection &amp; continuous printing
            <span class="muted tiny block">
              Show the automatic bed-clearing / continuous-printing panel. Off by
              default — enable only for printers with a bed-ejection setup.
            </span>
          </span>
        </label>
      </section>

      <section class="danger">
        <h4>Danger zone</h4>
        {#if !confirmDelete}
          <button class="btn btn-ghost btn-sm del" onclick={() => (confirmDelete = true)} disabled={deleting}>
            Delete printer from dashboard
          </button>
          <span class="muted tiny block">
            Removes this printer from your dashboard. Print history and stats are
            kept; any queued jobs assigned to it are returned to the unassigned pool.
          </span>
        {:else}
          <p class="cq">Delete <strong>{name}</strong>? History/stats are preserved and queued
            jobs are unassigned. This can't be undone.</p>
          <div class="drow">
            <button class="btn btn-ghost btn-sm" onclick={() => (confirmDelete = false)} disabled={deleting}>Cancel</button>
            <button class="btn btn-danger btn-sm" onclick={doDelete} disabled={deleting}>
              {deleting ? 'Deleting…' : 'Delete printer'}
            </button>
          </div>
        {/if}
      </section>

      {#if err}<p class="err">{err}</p>{/if}
    </div>

    <div class="mfoot">
      <button class="btn btn-ghost btn-sm" onclick={() => onclose?.()} disabled={saving || deleting}>Cancel</button>
      <button class="btn btn-primary btn-sm" onclick={save} disabled={saving || deleting}>{saving ? 'Saving…' : 'Save'}</button>
    </div>
  </div>
</div>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') onclose?.(); }} />

<style>
  .overlay { position: fixed; inset: 0; z-index: 60; display: flex; align-items: center; justify-content: center;
    background: rgba(0,0,0,0.55); backdrop-filter: blur(3px); padding: 1rem; }
  .modal { width: 100%; max-width: 440px; padding: 0; overflow: hidden; }
  .mhead { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem;
    padding: 1rem 1.2rem; border-bottom: 1px solid var(--ophq-border-soft); }
  .mhead h3 { margin: 0 0 0.15rem; font-size: 1.05rem; }
  .mbody { padding: 1.1rem 1.2rem; display: flex; flex-direction: column; gap: 1.1rem; }
  .mbody section h4 { margin: 0 0 0.7rem; font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--ophq-muted); }
  .opt { display: flex; align-items: flex-start; gap: 0.6rem; font-size: 0.92rem; color: var(--ophq-text); cursor: pointer; }
  .opt input { width: auto; margin-top: 0.2rem; accent-color: var(--ophq-primary); }
  .block { display: block; margin-top: 0.2rem; line-height: 1.45; }
  .tiny { font-size: 0.78rem; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0; }
  section.danger { border-top: 1px solid var(--ophq-border-soft); padding-top: 0.9rem; }
  section.danger h4 { color: var(--ophq-danger); }
  .del { color: var(--ophq-danger); border-color: rgba(255,92,108,0.35); }
  .del:hover { border-color: var(--ophq-danger); }
  .cq { font-size: 0.88rem; margin: 0 0 0.7rem; }
  .drow { display: flex; gap: 0.6rem; }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .btn-danger:hover { background: #ff7280; color: #fff; }
  .mfoot { display: flex; justify-content: flex-end; gap: 0.6rem; padding: 0.9rem 1.2rem;
    border-top: 1px solid var(--ophq-border-soft); background: var(--ophq-bg-2); }
</style>
