<script>
  // Calibration routines. These physically move the machine for several minutes,
  // so nothing fires without an explicit confirm and nothing is offered while a
  // print is running.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';
  import ModalShell from '$lib/components/ModalShell.svelte';

  let {
    printerId, isBambu = true, connected = false, printing = false,
    supportsChamber = false, onclose = () => {}
  } = $props();

  // Engine flags, in the order you'd sensibly run them.
  const ROUTINES = [
    { key: 'bed_leveling', label: 'Bed levelling',
      hint: 'Re-probes the bed mesh. Run after moving the printer or changing the plate.', mins: '2–4 min' },
    { key: 'vibration', label: 'Vibration compensation',
      hint: 'Measures resonance so ringing can be cancelled. The machine will shake audibly.', mins: '3–5 min' },
    { key: 'motor_noise', label: 'Motor noise cancellation',
      hint: 'Profiles the steppers to run quieter.', mins: '2–3 min' },
    { key: 'nozzle_offset', label: 'Nozzle offset',
      hint: 'Calibrates the offset between nozzles. Dual-nozzle machines only.', mins: '3–6 min' },
    { key: 'high_temp_heatbed', label: 'High-temperature bed',
      hint: 'Extra bed calibration for high-temperature plates.', mins: '4–8 min', needsChamber: true }
  ];

  let picked = $state({ bed_leveling: true });
  let confirming = $state(false);
  let running = $state(false);
  let msg = $state(null);

  const chosen = $derived(ROUTINES.filter((r) => picked[r.key]));
  const canRun = $derived(chosen.length > 0 && connected && !printing && isBambu);

  function toggle(key) { picked = { ...picked, [key]: !picked[key] }; }

  async function run() {
    running = true; msg = null;
    try {
      await api.calibrate(printerId, Object.fromEntries(chosen.map((r) => [r.key, true])));
      msg = { ok: true, text: 'Calibration started. Watch the printer — it will move on its own.' };
      confirming = false;
    } catch (e) {
      msg = { ok: false, text: e?.message || 'Could not start calibration.' };
      confirming = false;
    } finally { running = false; }
  }
</script>

<ModalShell title="Calibration" subtitle="Runs on the printer, unattended" width="560px"
            busy={running} {onclose}>
  {#if !isBambu}
    <p class="muted">
      These routines are Bambu-specific. For this printer, use the Klipper tuning
      panel on the printer page — homing, bed mesh, PID, input shaper and pressure
      advance all live there.
    </p>
  {:else}
    <p class="muted tiny lead">
      Pick what to run, then confirm. The printer heats, homes and moves by itself;
      keep the lid closed and don't send other commands until it finishes.
    </p>

    <ul class="routines">
      {#each ROUTINES as r (r.key)}
        {@const blocked = r.needsChamber && !supportsChamber}
        <li class="rt" class:on={picked[r.key]} class:off={blocked}>
          <label class="row">
            <input type="checkbox" checked={!!picked[r.key]} disabled={blocked || running}
                   onchange={() => toggle(r.key)} aria-label={r.label} />
            <span class="txt">
              <span class="name">{r.label} <span class="muted tiny dur">{r.mins}</span></span>
              <span class="muted tiny block">
                {blocked ? 'Not available on this printer.' : r.hint}
              </span>
            </span>
          </label>
        </li>
      {/each}
    </ul>

    {#if !connected}
      <p class="warn">The printer is offline, so nothing can be started.</p>
    {:else if printing}
      <p class="warn">A print is running. Calibration is disabled until it finishes.</p>
    {/if}

    {#if confirming}
      <p class="cq">
        Start {chosen.length === 1 ? chosen[0].label.toLowerCase() : `${chosen.length} routines`}?
        The printer will move for roughly {chosen.length === 1 ? chosen[0].mins : 'ten minutes or more'}.
      </p>
    {/if}
  {/if}

  {#if msg}<p class={msg.ok ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}

  {#snippet footer()}
    {#if isBambu && confirming}
      <button class="btn btn-ghost btn-sm" onclick={() => (confirming = false)} disabled={running}>Cancel</button>
      <button class="btn btn-primary btn-sm" onclick={run} disabled={running}>
        {running ? 'Starting…' : 'Start calibration'}
      </button>
    {:else}
      <button class="btn btn-ghost btn-sm" onclick={() => onclose()}>Close</button>
      {#if isBambu}
        <button class="btn btn-primary btn-sm" onclick={() => (confirming = true)} disabled={!canRun}>
          Run selected
        </button>
      {/if}
    {/if}
  {/snippet}
</ModalShell>

<style>
  .lead { margin: 0 0 0.9rem; max-width: 62ch; }
  .routines { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
  .rt { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-bg-2); padding: 0.6rem 0.75rem; }
  .rt.on { border-color: color-mix(in srgb, var(--ophq-primary) 45%, var(--ophq-border)); }
  .rt.off { opacity: 0.55; }
  .row { display: flex; align-items: flex-start; gap: 0.6rem; cursor: pointer; }
  .rt.off .row { cursor: default; }
  .row input { width: 16px; height: 16px; margin-top: 0.15rem; accent-color: var(--ophq-primary); flex: 0 0 auto; }
  .txt { flex: 1; min-width: 0; }
  .name { font-size: 0.92rem; font-weight: 600; }
  .dur { font-weight: 500; margin-left: 0.4rem; }
  .tiny { font-size: 0.8rem; }
  .warn { color: var(--ophq-warn); font-size: 0.86rem; margin: 0.9rem 0 0; }
  .cq { margin: 0.9rem 0 0; font-size: 0.9rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.88rem; margin: 0.8rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0.8rem 0 0; }
</style>
