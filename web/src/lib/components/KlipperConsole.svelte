<script>
  // OpenPrintHQ — realtime Klipper/Moonraker console.
  // Streams Klipper's own g-code responses (polled from Moonraker's
  // server.gcode_store via the engine's /klipper/console passthrough) and lets
  // you submit commands. Unlike the generic send-only GcodeConsole, this shows
  // the printer's replies — "// echo", "!! error", temp reports, etc. — so it's
  // a true console. Klipper transport only; lives in the Move & control area.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { api } from '$lib/api';
  import { onMount } from 'svelte';

  let { printerId, connected = false, printing = false, onhomed = null } = $props();

  let input = $state('');
  let busy = $state(false);
  let store = $state([]);        // [{ message, time, type }]
  let err = $state(null);        // poll/transport error (e.g. Moonraker unreachable)
  let histIdx = $state(-1);
  let sentHistory = $state([]);  // commands the user typed, for ↑/↓ recall

  const MACROS = ['G28', 'QUAD_GANTRY_LEVEL', 'BED_MESH_CALIBRATE', 'M84', 'GET_POSITION', 'M114', 'TURN_OFF_HEATERS', 'FIRMWARE_RESTART'];

  // Classify a store line for colour. Moonraker marks each entry command|response;
  // within responses, "!!" = error/alert, "//" = echo/info.
  function lineKind(l) {
    if (l?.type === 'command') return 'cmd';
    const m = String(l?.message || '');
    if (m.startsWith('!!')) return 'errline';
    if (m.startsWith('//')) return 'echo';
    return 'resp';
  }

  let logEl;
  let atBottom = true;
  function onScroll() {
    if (!logEl) return;
    atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 24;
  }
  function scrollDown() {
    if (typeof requestAnimationFrame === 'undefined') return;
    requestAnimationFrame(() => { if (logEl && atBottom) logEl.scrollTop = logEl.scrollHeight; });
  }

  async function poll() {
    if (!connected) return;
    try {
      const data = await api.klipperConsole(printerId, 150);
      store = Array.isArray(data?.gcode_store) ? data.gcode_store : [];
      err = null;
      if (onhomed) onhomed(data?.homed_axes ?? null);
      scrollDown();
    } catch (e) {
      err = e.message || 'console unavailable';
    }
  }

  onMount(() => {
    poll();
    const t = setInterval(poll, 2000);
    return () => clearInterval(t);
  });

  async function send(cmd) {
    const c = (cmd ?? input).trim();
    if (!c || busy || !connected) return;
    busy = true;
    try {
      await api.sendGcode(printerId, c);
      sentHistory = [...sentHistory, c];
      if (cmd == null) input = '';
      histIdx = -1;
      // Pull the fresh response quickly rather than waiting for the next tick.
      setTimeout(poll, 350);
    } catch (e) {
      err = e.message || 'command failed';
    } finally {
      busy = false;
    }
  }

  function onKey(e) {
    if (e.key === 'Enter') { e.preventDefault(); send(); return; }
    const h = sentHistory;
    if (e.key === 'ArrowUp' && h.length) {
      e.preventDefault();
      histIdx = histIdx < 0 ? h.length - 1 : Math.max(0, histIdx - 1);
      input = h[histIdx] || '';
    } else if (e.key === 'ArrowDown' && h.length) {
      e.preventDefault();
      if (histIdx < 0) return;
      histIdx += 1;
      if (histIdx >= h.length) { histIdx = -1; input = ''; } else input = h[histIdx];
    }
  }
</script>

<div class="card card-pad kcon">
  <div class="ch">
    <span class="eyebrow">Console</span>
    <span class="ch-sub muted">live · Klipper</span>
  </div>

  <div class="macros">
    {#each MACROS as m}
      <button class="chip-btn mono" data-tip={`Send ${m}`} aria-label={`Send ${m}`}
              onclick={() => send(m)} disabled={busy || printing || !connected}>{m}</button>
    {/each}
  </div>

  <div class="logwrap" bind:this={logEl} onscroll={onScroll} tabindex="0" role="log" aria-label="Klipper console output">
    {#if !connected}
      <p class="muted empty">Printer offline — connect to see the live console.</p>
    {:else if err}
      <p class="muted empty">Console unavailable: {err}</p>
    {:else if store.length === 0}
      <p class="muted empty">Waiting for console output… sent commands and the printer's replies appear here live.</p>
    {:else}
      {#each store as l, i (l.time + '-' + i)}
        <div class="line {lineKind(l)}">
          {#if lineKind(l) === 'cmd'}<span class="arrow">›</span>{/if}
          <span class="msg mono">{l.message}</span>
        </div>
      {/each}
    {/if}
  </div>

  <div class="entry">
    <input class="input mono" placeholder={printing ? 'Console disabled while printing' : (connected ? 'Type a G-code command, ↑ for history, Enter to send' : 'Printer offline')}
           bind:value={input} onkeydown={onKey} disabled={busy || printing || !connected}
           spellcheck="false" autocapitalize="off" autocomplete="off" aria-label="G-code command input" />
    <button class="btn btn-primary btn-sm" data-tip="Send the command" aria-label="Send command"
            onclick={() => send()} disabled={busy || printing || !connected || !input.trim()}>{busy ? 'Sending…' : 'Send'}</button>
  </div>
  <p class="muted note">Commands run directly on the printer. A bad move or temperature can damage hardware — use with care.</p>
</div>

<style>
  .kcon { margin-top: 1.2rem; height: 100%; display: flex; flex-direction: column; }
  .ch { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 0.7rem; }
  .ch-sub { font-size: 0.74rem; }
  .macros { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.7rem; }
  .chip-btn { font-size: 0.74rem; padding: 0.22rem 0.5rem; border: 1px solid var(--ophq-border); background: var(--ophq-surface); color: var(--ophq-text-2); border-radius: 999px; cursor: pointer; }
  .chip-btn:hover:not(:disabled) { color: var(--ophq-primary-2); border-color: var(--ophq-primary); }
  .chip-btn:disabled { opacity: 0.5; cursor: default; }
  .logwrap { flex: 1; min-height: 240px; max-height: 420px; overflow-y: auto; background: #070b11; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.6rem 0.7rem; font-size: 0.78rem; }
  .empty { margin: 0; font-size: 0.8rem; }
  .line { display: flex; gap: 0.4rem; align-items: baseline; padding: 0.08rem 0; }
  .arrow { color: var(--ophq-primary-2); }
  .msg { white-space: pre-wrap; word-break: break-word; }
  .line.cmd .msg { color: var(--ophq-text); }
  .line.resp .msg { color: var(--ophq-text-2); }
  .line.echo .msg { color: var(--ophq-muted); }
  .line.errline .msg { color: var(--ophq-danger); }
  .entry { display: flex; gap: 0.5rem; margin-top: 0.7rem; }
  .entry .input { flex: 1; }
  .note { font-size: 0.75rem; margin: 0.5rem 0 0; }
</style>
