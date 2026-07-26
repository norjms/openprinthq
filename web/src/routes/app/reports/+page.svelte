<script>
  // OpenPrintHQ — print-cost & billing reports (#25).
  // Pulls the print log for a chosen period + printer, computes filament/energy
  // cost per job (falling back to grams × default filament cost when a job has
  // no stored cost), and renders a print-optimised report. "Download PDF" uses
  // the browser's native print-to-PDF (window.print) with @media print CSS that
  // strips the app chrome — zero extra dependencies, reliable output.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import { branding } from '$lib/stores/appearance';

  const CUR_SYM = { USD: '$', EUR: '€', GBP: '£', CAD: 'C$', AUD: 'A$', JPY: '¥', CNY: '¥', INR: '₹', BRL: 'R$', MXN: 'MX$', CHF: 'CHF ', SEK: 'kr ', NOK: 'kr ', DKK: 'kr ', PLN: 'zł ', NZD: 'NZ$', ZAR: 'R ' };

  let loading = $state(true);
  let error = $state(null);
  let printers = $state([]);
  let settings = $state({ currency: 'USD', default_filament_cost: 25 });

  let period = $state('this_month');
  let customFrom = $state('');
  let customTo = $state('');
  let printerFilter = $state('');   // '' = all

  let rows = $state([]);
  let generatedAt = $state('');

  const sym = $derived(CUR_SYM[settings.currency] || (settings.currency ? settings.currency + ' ' : '$'));
  function money(n) { return sym + (Number(n) || 0).toFixed(2); }

  function rangeFor(p) {
    const now = new Date();
    const y = now.getFullYear(), m = now.getMonth();
    if (p === 'this_month') return [new Date(y, m, 1), new Date(y, m + 1, 1)];
    if (p === 'last_month') return [new Date(y, m - 1, 1), new Date(y, m, 1)];
    if (p === 'last_30') return [new Date(now.getTime() - 30 * 864e5), new Date(now.getTime() + 864e5)];
    if (p === 'last_90') return [new Date(now.getTime() - 90 * 864e5), new Date(now.getTime() + 864e5)];
    if (p === 'ytd') return [new Date(y, 0, 1), new Date(now.getTime() + 864e5)];
    if (p === 'custom') {
      const f = customFrom ? new Date(customFrom) : new Date(y, m, 1);
      const t = customTo ? new Date(new Date(customTo).getTime() + 864e5) : new Date(now.getTime() + 864e5);
      return [f, t];
    }
    return [new Date(y, m, 1), new Date(y, m + 1, 1)];
  }
  const periodLabel = $derived.by(() => {
    const [f, t] = rangeFor(period);
    const end = new Date(t.getTime() - 864e5);
    const opt = { year: 'numeric', month: 'short', day: 'numeric' };
    return f.toLocaleDateString(undefined, opt) + ' – ' + end.toLocaleDateString(undefined, opt);
  });

  function jobCost(e) {
    // Prefer stored cost; else estimate filament from grams × default rate.
    const grams = Number(e.filament_used_grams) || 0;
    const estFil = (grams / 1000) * (Number(settings.default_filament_cost) || 0);
    const filCost = (e.cost != null) ? Number(e.cost) : estFil;
    const energyCost = (e.energy_cost != null) ? Number(e.energy_cost) : 0;
    return { filCost, energyCost, total: filCost + energyCost, grams, estimated: e.cost == null };
  }

  async function fetchAll(params) {
    const out = [];
    const limit = 500;
    for (let offset = 0; offset < 4000; offset += limit) {
      const r = await api.printLogQuery({ ...params, limit, offset });
      const items = Array.isArray(r) ? r : (r.entries || r.items || r.logs || []);
      out.push(...items);
      if (items.length < limit) break;
    }
    return out;
  }

  async function run() {
    loading = true; error = null;
    try {
      const [from, to] = rangeFor(period);
      const params = { date_from: from.toISOString(), date_to: to.toISOString() };
      if (printerFilter) params.printer_id = printerFilter;
      const items = await fetchAll(params);
      rows = items.map((e) => ({ ...e, _c: jobCost(e) }));
      generatedAt = new Date().toLocaleString();
    } catch (e) {
      error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable');
    } finally { loading = false; }
  }

  onMount(async () => {
    try {
      const [pl, s] = await Promise.all([api.printers().catch(() => []), api.engineSettings().catch(() => ({}))]);
      printers = (Array.isArray(pl) ? pl : (pl?.printers || pl?.items || [])).map((p) => ({ id: p.id ?? p.printer_id, name: p.name || ('Printer ' + p.id) }));
      settings = { currency: s.currency || 'USD', default_filament_cost: s.default_filament_cost ?? 25 };
    } catch { /* ignore */ }
    await run();
  });

  const isDone = (s) => /complete|finish|success|done/i.test(String(s || ''));
  const isFail = (s) => /fail|cancel|error|stop/i.test(String(s || ''));

  const summary = $derived.by(() => {
    let total = rows.length, done = 0, fail = 0, grams = 0, fil = 0, energy = 0, kwh = 0, dur = 0;
    for (const r of rows) {
      if (isDone(r.status)) done++; else if (isFail(r.status)) fail++;
      grams += r._c.grams; fil += r._c.filCost; energy += r._c.energyCost;
      kwh += Number(r.energy_kwh) || 0; dur += Number(r.duration_seconds) || 0;
    }
    return { total, done, fail, grams, fil, energy, kwh, dur, cost: fil + energy, rate: total ? (done / total) * 100 : 0 };
  });

  const byPrinter = $derived.by(() => {
    const m = new Map();
    for (const r of rows) {
      const k = r.printer_name || ('Printer ' + (r.printer_id ?? '?'));
      const g = m.get(k) || { name: k, prints: 0, grams: 0, cost: 0, kwh: 0 };
      g.prints++; g.grams += r._c.grams; g.cost += r._c.total; g.kwh += Number(r.energy_kwh) || 0;
      m.set(k, g);
    }
    return [...m.values()].sort((a, b) => b.cost - a.cost);
  });

  function fmtDur(s) { s = Number(s) || 0; const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h}h ${m}m` : `${m}m`; }
  function fmtDate(v) { if (!v) return ''; const d = new Date(v); return isNaN(d) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); }
  const anyEstimated = $derived(rows.some((r) => r._c.estimated));
</script>

<PageTitle page="Reports" />

<div class="controls no-print">
  <div class="head">
    <div><h1>Reports</h1><p class="muted">Print-cost &amp; billing summary for any period.</p></div>
    <button class="btn btn-primary" onclick={() => window.print()} disabled={loading || !!error}>⭳ Download PDF</button>
  </div>
  <div class="filters card card-pad">
    <div class="fld">
      <label for="pd">Period</label>
      <select id="pd" class="input" bind:value={period} onchange={run}>
        <option value="this_month">This month</option>
        <option value="last_month">Last month</option>
        <option value="last_30">Last 30 days</option>
        <option value="last_90">Last 90 days</option>
        <option value="ytd">Year to date</option>
        <option value="custom">Custom range…</option>
      </select>
    </div>
    {#if period === 'custom'}
      <div class="fld"><label for="cf">From</label><input id="cf" class="input" type="date" bind:value={customFrom} onchange={run} /></div>
      <div class="fld"><label for="ct">To</label><input id="ct" class="input" type="date" bind:value={customTo} onchange={run} /></div>
    {/if}
    <div class="fld">
      <label for="pf">Printer</label>
      <select id="pf" class="input" bind:value={printerFilter} onchange={run}>
        <option value="">All printers</option>
        {#each printers as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
      </select>
    </div>
    <button class="btn btn-ghost" onclick={run} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
  </div>
</div>

{#if error === 'no-instance'}
  <div class="card card-pad no-print"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad no-print"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={run}>Retry</button></div>
{:else}
  <div class="report" id="report">
    <div class="rhead">
      <div class="brand"><span class="logo">{$branding.siteName}</span><span class="doc">Print-cost report</span></div>
      <div class="meta">
        <div class="pl">{periodLabel}</div>
        <div class="muted sm">{printerFilter ? (printers.find((p) => String(p.id) === String(printerFilter))?.name || 'Printer') : 'All printers'}{#if generatedAt} · generated {generatedAt}{/if}</div>
      </div>
    </div>

    {#if loading}
      <div class="card card-pad muted">Building report…</div>
    {:else}
      <div class="cards">
        <div class="stat"><span class="k">Total prints</span><span class="v">{summary.total}</span></div>
        <div class="stat"><span class="k">Success rate</span><span class="v">{summary.rate.toFixed(0)}%</span><span class="sub muted">{summary.done} ok · {summary.fail} failed</span></div>
        <div class="stat"><span class="k">Filament used</span><span class="v">{(summary.grams / 1000).toFixed(2)} kg</span></div>
        <div class="stat hero"><span class="k">Total cost</span><span class="v">{money(summary.cost)}</span><span class="sub muted">{money(summary.fil)} filament · {money(summary.energy)} energy</span></div>
      </div>

      {#if byPrinter.length}
        <h3 class="sec">By printer</h3>
        <table class="tbl">
          <thead><tr><th>Printer</th><th class="r">Prints</th><th class="r">Filament</th><th class="r">Energy</th><th class="r">Cost</th></tr></thead>
          <tbody>
            {#each byPrinter as p (p.name)}
              <tr><td>{p.name}</td><td class="r">{p.prints}</td><td class="r">{(p.grams / 1000).toFixed(2)} kg</td><td class="r">{p.kwh ? p.kwh.toFixed(2) + ' kWh' : '—'}</td><td class="r">{money(p.cost)}</td></tr>
            {/each}
          </tbody>
          <tfoot><tr><td>Total</td><td class="r">{summary.total}</td><td class="r">{(summary.grams / 1000).toFixed(2)} kg</td><td class="r">{summary.kwh ? summary.kwh.toFixed(2) + ' kWh' : '—'}</td><td class="r">{money(summary.cost)}</td></tr></tfoot>
        </table>
      {/if}

      <h3 class="sec">Jobs ({rows.length})</h3>
      {#if rows.length === 0}
        <p class="muted">No prints recorded in this period.</p>
      {:else}
        <table class="tbl jobs">
          <thead><tr><th>Date</th><th>File</th><th>Printer</th><th>Status</th><th class="r">Time</th><th class="r">Filament</th><th class="r">Cost</th></tr></thead>
          <tbody>
            {#each rows as r (r.id ?? (r.created_at + r.print_name))}
              <tr>
                <td class="nowrap">{fmtDate(r.created_at)}</td>
                <td class="fn">{r.print_name || '—'}</td>
                <td>{r.printer_name || '—'}</td>
                <td><span class="badge {isDone(r.status) ? 'ok' : isFail(r.status) ? 'bad' : ''}">{r.status}</span></td>
                <td class="r nowrap">{fmtDur(r.duration_seconds)}</td>
                <td class="r nowrap">{r._c.grams ? r._c.grams.toFixed(0) + ' g' : '—'}</td>
                <td class="r nowrap">{money(r._c.total)}{#if r._c.estimated}<span class="est" title="Estimated from filament weight">*</span>{/if}</td>
              </tr>
            {/each}
          </tbody>
        </table>
        {#if anyEstimated}
          <p class="foot muted">* Cost estimated from filament weight × default filament cost ({money(settings.default_filament_cost)}/kg). Set spool costs and an electricity rate in Settings for exact figures.</p>
        {/if}
      {/if}
    {/if}
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.1rem; }
  .head h1 { margin: 0; }
  .filters { display: flex; align-items: flex-end; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.4rem; }
  .fld { display: flex; flex-direction: column; gap: 0.3rem; }
  .fld label { font-size: 0.78rem; color: var(--ophq-text-2); }
  .fld .input { min-width: 150px; }

  .report { background: var(--ophq-surface); }
  .rhead { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid var(--ophq-primary); padding-bottom: 0.9rem; margin-bottom: 1.3rem; }
  .brand { display: flex; flex-direction: column; }
  .logo { font-weight: 800; font-size: 1.2rem; letter-spacing: -0.01em; }
  .doc { font-size: 0.82rem; color: var(--ophq-text-2); }
  .meta { text-align: right; }
  .pl { font-weight: 600; }
  .sm { font-size: 0.78rem; }

  .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.8rem; margin-bottom: 1.6rem; }
  .stat { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.8rem 0.9rem; display: flex; flex-direction: column; gap: 0.2rem; }
  .stat.hero { border-color: var(--ophq-primary); background: var(--ophq-primary-dim); }
  .stat .k { font-size: 0.74rem; color: var(--ophq-text-2); text-transform: uppercase; letter-spacing: 0.04em; }
  .stat .v { font-size: 1.4rem; font-weight: 700; }
  .stat .sub { font-size: 0.74rem; }

  .sec { margin: 1.5rem 0 0.6rem; font-size: 1rem; }
  .tbl { width: 100%; border-collapse: collapse; font-size: 0.86rem; }
  .tbl th, .tbl td { text-align: left; padding: 0.42rem 0.6rem; border-bottom: 1px solid var(--ophq-border); }
  .tbl th { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--ophq-muted); }
  .tbl .r { text-align: right; }
  .tbl tfoot td { font-weight: 700; border-top: 2px solid var(--ophq-border); border-bottom: none; }
  .jobs .fn { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .nowrap { white-space: nowrap; }
  .badge { font-size: 0.72rem; padding: 0.1rem 0.45rem; border-radius: 999px; border: 1px solid var(--ophq-border); color: var(--ophq-text-2); }
  .badge.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .badge.bad { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .est { color: var(--ophq-muted); }
  .foot { font-size: 0.76rem; margin-top: 0.8rem; }

  @media print {
    :global(.side) { display: none !important; }
    :global(.shell) { display: block !important; }
    :global(.content) { padding: 0 !important; max-width: none !important; }
    :global(body), .report { background: #fff !important; color: #111 !important; }
    .no-print { display: none !important; }
    .report { color: #111; }
    .stat, .tbl th, .tbl td { border-color: #ccc !important; }
    .stat.hero { background: #eef !important; }
    .rhead { border-color: #333 !important; }
    .muted, .stat .k, .stat .sub, .tbl th { color: #555 !important; }
    .cards { grid-template-columns: repeat(4, 1fr) !important; }
    .badge { border-color: #bbb !important; color: #333 !important; }
    .badge.ok { color: #1a7f37 !important; background: #e9f7ee !important; }
    .badge.bad { color: #b3261e !important; background: #fdeceb !important; }
    table, tr, .stat { break-inside: avoid; }
    @page { margin: 1.4cm; }
  }
</style>
