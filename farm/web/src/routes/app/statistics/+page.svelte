<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let s = $state(null);
  let printerNames = $state({});
  let recent = $state([]);

  async function load() {
    loading = true; error = null;
    try {
      s = await api.printStats();
      const pl = await api.printers().catch(() => []);
      const arr = Array.isArray(pl) ? pl : (pl?.printers || pl?.items || []);
      printerNames = Object.fromEntries(arr.map((p) => [String(p.id), p.name || p.model || ('Printer ' + p.id)]));
      const log = await api.printLog(25).catch(() => null);
      const entries = (log && log.items) || (Array.isArray(log) ? log : []);
      recent = entries.map((e) => ({
        id: e.id,
        name: e.print_name || 'Print',
        printer: e.printer_name || printerNames[String(e.printer_id)] || (e.printer_id ? 'Printer ' + e.printer_id : '—'),
        status: (e.status || '').toString(),
        dur: e.duration_seconds,
        ftype: e.filament_type || '',
        fcolor: e.filament_color || '',
        grams: e.filament_used_grams,
        when: e.completed_at || e.started_at || e.created_at
      }));
    }
    catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(load);

  function fmtDur(sec) {
    if (!sec) return '—';
    const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }
  function fmtWhen(v) {
    if (!v) return '—';
    const d = new Date(v);
    return isNaN(d) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function rtone(x) {
    x = (x || '').toLowerCase();
    if (/complete|success|finish/.test(x)) return 'ok';
    if (/fail|abort|error/.test(x)) return 'danger';
    return '';
  }

  const successRate = $derived(s && s.total_prints > 0 ? Math.round((s.successful_prints / s.total_prints) * 100) : null);
  const tiles = $derived(s ? [
    { label: 'Total prints', value: s.total_prints ?? 0 },
    { label: 'Success rate', value: successRate == null ? '—' : successRate + '%', ok: true },
    { label: 'Failed', value: s.failed_prints ?? 0 },
    { label: 'Print time', value: (s.total_print_time_hours ?? 0).toFixed(1) + ' h' },
    { label: 'Filament used', value: ((s.total_filament_grams ?? 0) / 1000).toFixed(2) + ' kg' },
    { label: 'Cost', value: '$' + (s.total_cost ?? 0).toFixed(2) },
    { label: 'Energy', value: (s.total_energy_kwh ?? 0).toFixed(1) + ' kWh' },
    { label: 'Energy cost', value: '$' + (s.total_energy_cost ?? 0).toFixed(2) }
  ] : []);
  const byType = $derived(s?.prints_by_filament_type ? Object.entries(s.prints_by_filament_type) : []);
  const byPrinter = $derived(s?.prints_by_printer
    ? Object.entries(s.prints_by_printer).map(([k, v]) => [printerNames[k] || (k === 'unknown' ? 'Unknown' : `Printer ${k}`), v])
    : []);
</script>

<svelte:head><title>Statistics · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div><h1>Statistics</h1><p class="muted">Production analytics for your fleet.</p></div>
  <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
</div>

{#if loading}
  <div class="card card-pad muted">Loading analytics…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else}
  <div class="tiles">
    {#each tiles as t}
      <div class="card card-pad tile"><span class="muted">{t.label}</span><b class:ok={t.ok}>{t.value}</b></div>
    {/each}
  </div>
  {#if s.total_prints === 0}
    <div class="card card-pad muted note">No prints recorded yet — analytics fill in as you print across your fleet.</div>
  {:else}
    <div class="grid two">
      <div class="card card-pad">
        <span class="eyebrow">By filament type</span>
        {#if byType.length}{#each byType as [k, v]}<div class="row"><span>{k}</span><span class="mono">{v}</span></div>{/each}{:else}<p class="muted">—</p>{/if}
      </div>
      <div class="card card-pad">
        <span class="eyebrow">By printer</span>
        {#if byPrinter.length}{#each byPrinter as [k, v]}<div class="row"><span>{k}</span><span class="mono">{v}</span></div>{/each}{:else}<p class="muted">—</p>{/if}
      </div>
    </div>

    {#if recent.length}
      <div class="card recents">
        <div class="rrow rhead muted mono">
          <span>Job</span><span>Printer</span><span>Filament</span><span>Time</span><span>Status</span><span class="ar">When</span>
        </div>
        {#each recent as r (r.id)}
          <div class="rrow">
            <span class="rname">{r.name}</span>
            <span class="muted">{r.printer}</span>
            <span class="rfil">
              {#if r.fcolor}<span class="dot" style="background:{r.fcolor}"></span>{/if}{r.ftype || '—'}{#if r.grams} · {Math.round(r.grams)} g{/if}
            </span>
            <span class="mono">{fmtDur(r.dur)}</span>
            <span><span class="chip {rtone(r.status)}">{r.status}</span></span>
            <span class="ar muted mono">{fmtWhen(r.when)}</span>
          </div>
        {/each}
      </div>
    {/if}
  {/if}
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; }
  .head h1 { margin: 0; }
  .tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; }
  .tile { display: flex; flex-direction: column; gap: 0.3rem; }
  .tile b { font-size: 1.7rem; font-family: var(--font-mono); }
  .tile b.ok { color: var(--ophq-success); }
  .note { margin-top: 1.2rem; }
  .two { grid-template-columns: 1fr 1fr; margin-top: 1.2rem; }
  .row { display: flex; justify-content: space-between; padding: 0.4rem 0; border-bottom: 1px solid var(--ophq-border-soft); }
  .row:last-child { border-bottom: none; }

  .recents { margin-top: 1.2rem; overflow: hidden; }
  .rrow { display: grid; grid-template-columns: 1.4fr 1fr 1.3fr 80px 110px 70px; gap: 1rem; align-items: center; padding: 0.7rem 1.2rem; border-bottom: 1px solid var(--ophq-border); font-size: 0.9rem; }
  .rrow:last-child { border-bottom: none; }
  .rhead { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .rname { font-weight: 500; }
  .ar { text-align: right; }
  .rfil { display: flex; align-items: center; gap: 0.45rem; color: var(--ophq-text-2); }
  .rfil .dot { width: 12px; height: 12px; border-radius: 50%; border: 1px solid rgba(255,255,255,0.15); flex-shrink: 0; }
  .chip.danger { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .chip.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  @media (max-width: 820px) {
    .tiles { grid-template-columns: repeat(2, 1fr); } .two { grid-template-columns: 1fr; }
    .rrow { grid-template-columns: 1.4fr 1fr 70px; }
    .rrow > :nth-child(3), .rrow > :nth-child(4), .rhead > :nth-child(3), .rhead > :nth-child(4) { display: none; }
  }
</style>
