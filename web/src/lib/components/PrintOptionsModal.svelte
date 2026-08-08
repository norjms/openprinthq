<script>
  // Print Options — the printer's on-board monitoring modules (spaghetti
  // detection, first-layer inspection, and friends). The engine has had a route
  // for these since forever; nothing ever called it.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';
  import ModalShell from '$lib/components/ModalShell.svelte';

  let { printerId, status = null, onclose = () => {}, onsaved = () => {} } = $props();

  // Only the modules worth a switch in the UI. The engine also accepts
  // airprint_detector and auto_recovery_step_loss, which are firmware-internal
  // and not meaningfully user-tunable, so they're deliberately not listed.
  const MODULES = [
    { key: 'spaghetti_detector', label: 'Spaghetti detection',
      hint: 'Watches for a failed print turning into a bird’s nest.', halt: true, sens: true },
    { key: 'first_layer_inspector', label: 'First-layer inspection',
      hint: 'Checks adhesion on layer one before committing to the rest.', halt: true, sens: false },
    { key: 'printing_monitor', label: 'Printing monitor',
      hint: 'General AI monitoring throughout the job.', halt: false, sens: false },
    { key: 'buildplate_marker_detector', label: 'Build-plate marker',
      hint: 'Confirms the plate on the bed is the one the job expects.', halt: true, sens: false },
    { key: 'pileup_detector', label: 'Nozzle pile-up',
      hint: 'Detects material accreting on the hotend.', halt: true, sens: false },
    { key: 'clump_detector', label: 'Filament clump',
      hint: 'Detects clumps being dragged into the print.', halt: true, sens: false },
    { key: 'allow_skip_parts', label: 'Allow skipping objects',
      hint: 'Permits individual objects to be skipped mid-print.', halt: false, sens: false }
  ];
  const SENSITIVITIES = ['low', 'medium', 'high', 'never_halt'];
  const sensLabel = (s) => (s === 'never_halt' ? 'Never halt' : s[0].toUpperCase() + s.slice(1));

  // Seed from whatever the printer is currently reporting. The engine echoes
  // these back on status as `print_options`; shape varies by firmware, so read
  // defensively rather than assuming a flat boolean map.
  function seedFrom(po, key) {
    const v = po?.[key];
    if (v == null) return { enabled: false, print_halt: true, sensitivity: 'medium' };
    if (typeof v === 'boolean') return { enabled: v, print_halt: true, sensitivity: 'medium' };
    return {
      enabled: !!(v.enabled ?? v.on ?? false),
      print_halt: v.print_halt !== false,
      sensitivity: SENSITIVITIES.includes(v.sensitivity) ? v.sensitivity : 'medium'
    };
  }

  let cfg = $state(
    Object.fromEntries(MODULES.map((m) => [m.key, seedFrom(status?.print_options, m.key)]))
  );
  let busyKey = $state(null);
  let msg = $state(null);

  // Each switch is applied on the spot — the engine takes one module per call,
  // so a Save button would just be a loop that can half-fail and leave the
  // dialog lying about what the printer thinks.
  async function apply(m, patch) {
    const next = { ...cfg[m.key], ...patch };
    cfg = { ...cfg, [m.key]: next };
    busyKey = m.key; msg = null;
    try {
      await api.setPrintOption(printerId, {
        module_name: m.key,
        enabled: next.enabled,
        print_halt: next.print_halt,
        sensitivity: next.sensitivity
      });
      msg = { ok: true, text: `${m.label} updated.` };
      onsaved();
    } catch (e) {
      // Put the switch back where it was; the printer never accepted it.
      cfg = { ...cfg, [m.key]: seedFrom(status?.print_options, m.key) };
      msg = { ok: false, text: e?.message || `Could not update ${m.label.toLowerCase()}.` };
    } finally { busyKey = null; }
  }
</script>

<ModalShell title="Print options" subtitle="On-board monitoring while a job runs" width="560px"
            busy={!!busyKey} {onclose}>
  <p class="muted tiny lead">
    These run on the printer itself, not in OpenPrintHQ, so they keep working when
    this page is closed. Halting on detection is safer; sensitivity trades false
    alarms against missed failures.
  </p>

  <ul class="mods">
    {#each MODULES as m (m.key)}
      <li class="mod" class:on={cfg[m.key].enabled}>
        <label class="row">
          <input type="checkbox" checked={cfg[m.key].enabled}
                 disabled={busyKey === m.key}
                 onchange={(e) => apply(m, { enabled: e.currentTarget.checked })}
                 aria-label={m.label} />
          <span class="txt">
            <span class="name">{m.label}</span>
            <span class="muted tiny block">{m.hint}</span>
          </span>
          {#if busyKey === m.key}<span class="muted tiny">saving…</span>{/if}
        </label>

        {#if cfg[m.key].enabled && (m.halt || m.sens)}
          <div class="sub">
            {#if m.halt}
              <label class="opt">
                <input type="checkbox" checked={cfg[m.key].print_halt}
                       disabled={busyKey === m.key}
                       onchange={(e) => apply(m, { print_halt: e.currentTarget.checked })} />
                <span>Pause the print when it triggers</span>
              </label>
            {/if}
            {#if m.sens}
              <label class="opt sel">
                <span>Sensitivity</span>
                <select class="input xs" value={cfg[m.key].sensitivity}
                        disabled={busyKey === m.key}
                        onchange={(e) => apply(m, { sensitivity: e.currentTarget.value })}>
                  {#each SENSITIVITIES as s}<option value={s}>{sensLabel(s)}</option>{/each}
                </select>
              </label>
            {/if}
          </div>
        {/if}
      </li>
    {/each}
  </ul>

  {#if msg}<p class={msg.ok ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}

  {#snippet footer()}
    <button class="btn btn-ghost btn-sm" onclick={() => onclose()} disabled={!!busyKey}>Done</button>
  {/snippet}
</ModalShell>

<style>
  .lead { margin: 0 0 0.9rem; max-width: 60ch; }
  .mods { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
  .mod { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-bg-2); padding: 0.6rem 0.75rem; }
  .mod.on { border-color: color-mix(in srgb, var(--ophq-primary) 45%, var(--ophq-border)); }
  .row { display: flex; align-items: flex-start; gap: 0.6rem; cursor: pointer; }
  .row input[type=checkbox] { width: 16px; height: 16px; margin-top: 0.15rem; accent-color: var(--ophq-primary); flex: 0 0 auto; }
  .txt { flex: 1; min-width: 0; }
  .name { font-size: 0.92rem; font-weight: 600; }
  .sub { margin: 0.55rem 0 0 2.1rem; display: flex; flex-wrap: wrap; gap: 0.5rem 1.2rem; padding-top: 0.5rem; border-top: 1px solid var(--ophq-border-soft); }
  .opt { display: inline-flex; align-items: center; gap: 0.4rem; font-size: 0.83rem; color: var(--ophq-text-2); cursor: pointer; }
  .opt input { width: 15px; height: 15px; accent-color: var(--ophq-primary); }
  .opt.sel { gap: 0.5rem; }
  .input.xs { padding: 0.2rem 0.4rem; font-size: 0.82rem; max-width: 8rem; }
  .tiny { font-size: 0.8rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.88rem; margin: 0.8rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0.8rem 0 0; }
</style>
