<script>
  // OpenPrintHQ — g-code console & macros (#23).
  // Free-form g-code entry + curated macro chips, sent through the engine's
  // raw-gcode route (POST /printers/{id}/gcode). Send-only: the printer's
  // response isn't streamed back, so the log records what was sent and whether
  // the transport accepted it; watch live status/temps for the effect.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';

  let { printerId, kind = '', printing = false } = $props();

  let input = $state('');
  let busy = $state(false);
  let log = $state([]);   // [{ cmd, ok, note, t }]
  let histIdx = $state(-1);
  const history = () => log.filter((l) => l.sent).map((l) => l.cmd);

  const KLIPPER_MACROS = ['G28', 'QUAD_GANTRY_LEVEL', 'BED_MESH_CALIBRATE', 'M84', 'TURN_OFF_HEATERS', 'FIRMWARE_RESTART', 'GET_POSITION', 'M114', 'M115'];
  const BAMBU_MACROS = ['M104 S0', 'M140 S0', 'M106 S255', 'M106 S0', 'G28', 'M114', 'M400'];
  const macros = $derived((kind || '').toLowerCase() === 'klipper' ? KLIPPER_MACROS : BAMBU_MACROS);

  async function send(cmd) {
    const c = (cmd ?? input).trim();
    if (!c || busy) return;
    busy = true;
    const entry = { cmd: c, ok: null, sent: false, t: Date.now() };
    log = [...log, entry];
    try {
      await api.sendGcode(printerId, c);
      entry.ok = true; entry.sent = true; entry.note = 'sent';
    } catch (e) {
      entry.ok = false; entry.note = e.message || 'failed';
    }
    log = [...log];               // trigger update
    if (cmd == null) input = '';
    histIdx = -1;
    busy = false;
    queueScroll();
  }

  let logEl;
  function queueScroll() {
    if (typeof requestAnimationFrame === 'undefined') return;
    requestAnimationFrame(() => { if (logEl) logEl.scrollTop = logEl.scrollHeight; });
  }

  function onKey(e) {
    if (e.key === 'Enter') { e.preventDefault(); send(); return; }
    const h = history();
    if (e.key === 'ArrowUp' && h.length) {
      e.preventDefault();
      histIdx = histIdx < 0 ? h.length - 1 : Math.max(0, histIdx - 1);
      input = h[histIdx] || '';
    } else if (e.key === 'ArrowDown' && h.length) {
      e.preventDefault();
      if (histIdx < 0) return;
      histIdx = histIdx + 1;
      if (histIdx >= h.length) { histIdx = -1; input = ''; } else input = h[histIdx];
    }
  }
  function clearLog() { log = []; }
</script>

<div class="card card-pad gcon">
  <div class="ch">
    <span class="eyebrow">G-code console</span>
    {#if log.length}<button class="btn btn-ghost btn-sm" onclick={clearLog}>Clear</button>{/if}
  </div>

  <div class="macros">
    {#each macros as m}
      <button class="chip-btn mono" onclick={() => send(m)} disabled={busy || printing} title="Send {m}">{m}</button>
    {/each}
  </div>

  <div class="logwrap" bind:this={logEl}>
    {#if log.length === 0}
      <p class="muted empty">Sent commands appear here. Responses aren't streamed — watch the printer's live status for the effect.</p>
    {:else}
      {#each log as l (l.t + l.cmd)}
        <div class="line">
          <span class="arrow">›</span>
          <span class="cmd mono">{l.cmd}</span>
          {#if l.ok === true}<span class="ok mono">✓ {l.note}</span>
          {:else if l.ok === false}<span class="bad mono">✗ {l.note}</span>
          {:else}<span class="muted mono">…</span>{/if}
        </div>
      {/each}
    {/if}
  </div>

  <div class="entry">
    <input class="input mono" placeholder={printing ? 'Console disabled while printing' : 'Type a G-code command and press Enter'} bind:value={input} onkeydown={onKey} disabled={busy || printing} spellcheck="false" autocapitalize="off" autocomplete="off" />
    <button class="btn btn-primary btn-sm" onclick={() => send()} disabled={busy || printing || !input.trim()}>{busy ? 'Sending…' : 'Send'}</button>
  </div>
  <p class="muted note">Commands run directly on the printer. Use with care — a bad move or temperature can damage hardware.</p>
</div>

<style>
  .gcon { margin-top: 1.2rem; }
  .ch { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.7rem; }
  .macros { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.7rem; }
  .chip-btn { font-size: 0.74rem; padding: 0.22rem 0.5rem; border: 1px solid var(--ophq-border); background: var(--ophq-surface); color: var(--ophq-text-2); border-radius: 999px; cursor: pointer; }
  .chip-btn:hover:not(:disabled) { color: var(--ophq-primary-2); border-color: var(--ophq-primary); }
  .chip-btn:disabled { opacity: 0.5; cursor: default; }
  .logwrap { height: 168px; overflow-y: auto; background: #070b11; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.6rem 0.7rem; font-size: 0.8rem; }
  .empty { margin: 0; font-size: 0.8rem; }
  .line { display: flex; gap: 0.5rem; align-items: baseline; padding: 0.12rem 0; }
  .arrow { color: var(--ophq-primary-2); }
  .cmd { color: var(--ophq-text); }
  .ok { color: var(--ophq-success); font-size: 0.72rem; }
  .bad { color: var(--ophq-danger); font-size: 0.72rem; }
  .entry { display: flex; gap: 0.5rem; margin-top: 0.7rem; }
  .entry .input { flex: 1; }
  .note { font-size: 0.75rem; margin: 0.5rem 0 0; }
</style>
