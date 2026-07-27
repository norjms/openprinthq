<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';

  let loading = $state(true);
  let error = $state(null);
  let items = $state([]);
  let printers = $state([]);
  let busyId = $state(null);      // queue item id with an action in flight
  let confirmDel = $state(null);  // id pending delete confirmation
  let toast = $state(null);
  let toastTimer = null;
  let filterPrinter = $state('all');   // 'all' | 'unassigned' | printer id
  // Deep-link: /app/queue?printer=<id> pre-selects that printer's queue (used by
  // the per-printer "queue" quick action on the printers list).
  onMount(() => {
    try {
      const pid = new URLSearchParams(location.search).get('printer');
      if (pid) filterPrinter = pid;
    } catch { /* */ }
  });

  // ---- active temperature-staggered batch ----
  let batch = $state(null);
  let batchActing = $state(false);
  let batchTimer = null;
  async function loadBatch() {
    try {
      const b = await api.batchActive();
      batch = b && b.status === 'running' ? b : null;
    } catch { batch = null; }
  }
  async function advanceBatch() {
    if (!batch) return;
    batchActing = true;
    try { await api.batchAdvance(batch.id); await loadBatch(); showToast('ok', 'Started the next printer.'); }
    catch (e) { showToast('err', e.message || 'could not advance'); }
    finally { batchActing = false; }
  }
  async function cancelBatch() {
    if (!batch) return;
    batchActing = true;
    try { await api.batchCancel(batch.id); await loadBatch(); await load(); showToast('ok', 'Batch cancelled — held jobs removed.'); }
    catch (e) { showToast('err', e.message || 'could not cancel'); }
    finally { batchActing = false; }
  }
  function stepState(s) {
    if (!s.released) return { key: 'waiting', label: 'waiting' };
    if (s.timedOut) return { key: 'timeout', label: 'released (timed out)' };
    if (s.reachedTemp) return { key: 'ready', label: 'at temp · printing' };
    return { key: 'preheating', label: 'preheating' };
  }
  const batchNextIdx = $derived(batch ? (batch.steps || []).findIndex((s) => !s.released) : -1);

  const filtered = $derived(filterPrinter !== 'all');
  const shownItems = $derived(
    filterPrinter === 'all' ? items
      : filterPrinter === 'unassigned' ? items.filter((q) => q.printerId == null)
        : items.filter((q) => String(q.printerId) === String(filterPrinter))
  );

  function showToast(kind, text) {
    toast = { kind, text };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast = null), 5000);
  }

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.items || d?.queue || d?.results || []);
    return arr.map((q) => ({
      id: q.id ?? q.queue_id,
      name: q.library_file_name ?? q.archive_name ?? q.name ?? q.filename ?? 'Job',
      status: (q.status ?? q.state ?? 'queued').toString(),
      printerId: q.printer_id ?? null,
      printer: q.printer_name ?? q.printer ?? '',
      qty: q.quantity ?? q.amount ?? 1,
      position: q.position ?? 0,
      filament: q.filament_type ?? '',
      timeSec: q.print_time_seconds ?? null,
      targetModel: q.target_model ?? null,
      reqMaterials: Array.isArray(q.required_filament_types) ? q.required_filament_types.map((m) => String(m).toUpperCase()) : []
    }));
  }
  function normPrinters(d) {
    const arr = Array.isArray(d) ? d : (d?.printers || d?.items || d?.results || []);
    return arr.map((p) => ({ id: p.id ?? p.printer_id, name: p.name ?? p.model ?? ('Printer ' + (p.id ?? '')), model: (p.model ?? '').toString() }));
  }

  async function load() {
    loading = true; error = null;
    try {
      const [q, pr] = await Promise.all([api.queue(), api.printers().catch(() => [])]);
      items = norm(q).sort((a, b) => a.position - b.position);
      printers = normPrinters(pr);
    } catch (e) {
      error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable');
    } finally {
      loading = false;
    }
  }
  onMount(() => {
    load();
    loadBatch();
    batchTimer = setInterval(loadBatch, 5000);
    return () => clearInterval(batchTimer);
  });

  const isActive = (s) => /print|run|start/i.test(s) && !/pending|queued|wait/i.test(s);
  const isPending = (s) => /pending|queued|wait/i.test(s);

  function fmtTime(sec) {
    if (!sec) return '';
    const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }
  function tone(s) {
    const x = s.toLowerCase();
    if (/print|run/.test(x)) return 'primary';
    if (/pending|queued|wait/.test(x)) return '';
    if (/done|complete|finish/.test(x)) return 'ok';
    if (/error|fail|cancel|stop/.test(x)) return 'danger';
    return '';
  }

  async function act(id, fn, okMsg) {
    busyId = id;
    try {
      await fn();
      if (okMsg) showToast('ok', okMsg);
      await load();
    } catch (e) {
      showToast('err', e.message || 'action failed');
    } finally {
      busyId = null; confirmDel = null;
    }
  }

  async function assign(item, ev) {
    const v = ev.target.value;
    const printer_id = v === '' ? null : Number(v);
    await act(item.id, () => api.queueUpdate(item.id, { printer_id }),
      printer_id ? 'Assigned to printer.' : 'Set to unassigned.');
  }

  // ---- smart routing (#18): auto-assign unassigned jobs to the best printer ----
  let routing = $state(false);
  const unassignedCount = $derived(items.filter((q) => q.printerId == null && isPending(q.status)).length);

  function modelMatch(a, b) {
    const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    a = norm(a); b = norm(b);
    return !!a && !!b && (a === b || a.includes(b) || b.includes(a));
  }

  async function smartRoute() {
    if (routing) return;
    routing = true;
    try {
      // 1) Build each printer's capability profile from live status.
      const caps = await Promise.all(printers.map(async (p) => {
        let st = null; try { st = await api.printerStatus(p.id); } catch { /* offline */ }
        const connected = !!st?.connected;
        const state = String(st?.state || (connected ? 'idle' : 'offline')).toLowerCase();
        const busy = /print|run|pause/.test(state);
        const materials = new Set();
        for (const unit of (st?.ams || [])) for (const tray of (unit.tray || unit.trays || [])) {
          const m = String(tray.material || tray.tray_type || tray.type || '').toUpperCase(); if (m) materials.add(m);
        }
        const vt = String(st?.vt_tray?.material || st?.vt_tray?.tray_type || '').toUpperCase(); if (vt) materials.add(vt);
        return { id: p.id, name: p.name, model: p.model, connected, busy, materials };
      }));

      // 2) Current load per printer (assigned, not-yet-done work) for balancing.
      const load = {};
      for (const it of items) if (it.printerId != null && isPending(it.status)) load[it.printerId] = (load[it.printerId] || 0) + (it.timeSec || 1800) + 1;

      // 3) Route each unassigned pending item.
      const targets = items.filter((q) => q.printerId == null && isPending(q.status)).sort((a, b) => a.position - b.position);
      const assignments = [];
      const skipped = [];
      for (const it of targets) {
        let pool = caps.filter((c) => c.connected);
        if (it.targetModel) { const m = pool.filter((c) => modelMatch(c.model, it.targetModel)); if (m.length) pool = m; }
        if (it.reqMaterials.length) {
          const m = pool.filter((c) => it.reqMaterials.every((rm) => [...c.materials].some((cm) => cm.includes(rm) || rm.includes(cm))));
          if (m.length) pool = m;   // strong preference; fall back to model/connected pool if none loaded
        }
        if (pool.length === 0) { skipped.push(it); continue; }
        // Pick least-loaded; tie-break idle over busy.
        pool.sort((a, b) => (load[a.id] || 0) - (load[b.id] || 0) || (a.busy - b.busy));
        const chosen = pool[0];
        assignments.push({ it, printerId: chosen.id });
        load[chosen.id] = (load[chosen.id] || 0) + (it.timeSec || 1800) + 1;
      }

      if (assignments.length === 0) {
        showToast('err', skipped.length ? 'No connected printer matches the queued jobs.' : 'Nothing to route.');
        return;
      }
      for (const a of assignments) {
        try { await api.queueUpdate(a.it.id, { printer_id: a.printerId }); } catch { /* keep going */ }
      }
      await load2();
      const msg = `Routed ${assignments.length} job${assignments.length > 1 ? 's' : ''}` + (skipped.length ? `, ${skipped.length} left unassigned (no match).` : '.');
      showToast('ok', msg);
    } catch (e) {
      showToast('err', e.message || 'routing failed');
    } finally { routing = false; }
  }
  // load() is defined above; alias for clarity in async flows.
  const load2 = () => load();

  // Move an item up/down by swapping with its neighbor, then send the full order.
  async function move(idx, dir) {
    const j = idx + dir;
    if (j < 0 || j >= items.length) return;
    const reordered = items.slice();
    [reordered[idx], reordered[j]] = [reordered[j], reordered[idx]];
    const payload = reordered.map((it, i) => ({ id: it.id, position: i + 1 }));
    await act(items[idx].id, () => api.queueReorder(payload));
  }
</script>

<PageTitle page="Print queue" />

<div class="head">
  <div><h1>Print queue</h1><p class="muted">One queue across your whole fleet.</p></div>
  <div class="flex gap center">
    {#if !loading && !error && items.length > 0}
      <select class="input filt" bind:value={filterPrinter} aria-label="Filter by printer">
        <option value="all">All printers</option>
        <option value="unassigned">Unassigned</option>
        {#each printers as p}<option value={String(p.id)}>{p.name}</option>{/each}
      </select>
    {/if}
    {#if !loading && !error && unassignedCount > 0}
      <button class="btn btn-primary btn-sm" onclick={smartRoute} disabled={routing} title="Assign unassigned jobs to the best-matched printer (material, model, load)">
        {routing ? 'Routing…' : `⚡ Auto-assign (${unassignedCount})`}
      </button>
    {/if}
    <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
  </div>
</div>

{#if toast}
  <div class="toast {toast.kind}">{toast.text}</div>
{/if}

{#if batch}
  {@const steps = batch.steps || []}
  <div class="card batch">
    <div class="bhead">
      <div>
        <span class="eyebrow">Staggered batch</span>
        <h3>{batch.file_name || 'Batch print'}</h3>
        <p class="muted tiny">Heat-up staggered per circuit · max {batch.max_preheat} preheating at once. Printers on different circuits run in parallel.</p>
      </div>
      <div class="flex gap center">
        <button class="btn btn-primary btn-sm" onclick={advanceBatch} disabled={batchActing || batchNextIdx < 0}>Start next now</button>
        <button class="btn btn-ghost btn-sm danger-text" onclick={cancelBatch} disabled={batchActing}>Cancel batch</button>
      </div>
    </div>
    <div class="steps">
      {#each steps as s, i (s.printerId)}
        {@const ss = stepState(s)}
        {@const tm = batch.temps?.[s.printerId]}
        <div class="bstep {ss.key}">
          <span class="si mono">{i + 1}</span>
          <span class="sp">
            {s.printerName}
            {#if s.circuit}<span class="cchip mono">{s.circuit}</span>{/if}
          </span>
          <span class="sinfo">
            {#if ss.key === 'preheating' && tm}
              <span class="mono ttemp">bed {Math.round(tm.bed)}/{Math.round(tm.bedTarget) || '—'}°{#if tm.chamberTarget > 0} · chamber {Math.round(tm.chamber)}/{Math.round(tm.chamberTarget)}°{/if}</span>
            {/if}
          </span>
          <span class="schip {ss.key}">{ss.label}</span>
        </div>
      {/each}
    </div>
  </div>
{/if}

{#if loading}
  <div class="card card-pad muted">Loading queue…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else if items.length === 0}
  <div class="card card-pad empty">
    <div class="ic">≣</div>
    <h3>Queue is empty</h3>
    <p class="muted">Slice a model or add a G-code file to the queue — jobs across your whole fleet show up here.</p>
    <a class="btn btn-primary" href="/app/files">Go to files</a>
  </div>
{:else if shownItems.length === 0}
  <div class="card card-pad muted flt-empty">
    No queued jobs for this filter.
    <button class="btn btn-ghost btn-sm" onclick={() => (filterPrinter = 'all')}>Show all</button>
  </div>
{:else}
  <div class="card list">
    <div class="row head-row muted mono"><span>#</span><span>Job</span><span>Printer</span><span>Status</span><span class="ar">Actions</span></div>
    {#each shownItems as q, i (q.id)}
      <div class="row" class:busy={busyId === q.id}>
        <span class="mono muted pos">{i + 1}</span>
        <span class="name">
          {q.name}
          <span class="sub mono">
            {#if q.qty > 1}×{q.qty}{/if}
            {#if q.filament}· {q.filament}{/if}
            {#if fmtTime(q.timeSec)}· ~{fmtTime(q.timeSec)}{/if}
          </span>
        </span>
        <span>
          {#if isActive(q.status)}
            <span class="mono">{q.printer || '—'}</span>
          {:else}
            <select class="input sel" value={q.printerId ?? ''} onchange={(e) => assign(q, e)} disabled={busyId === q.id}>
              <option value="">Unassigned (auto)</option>
              {#each printers as p}<option value={p.id}>{p.name}</option>{/each}
            </select>
          {/if}
        </span>
        <span><span class="chip {tone(q.status)}">{q.status}</span></span>
        <span class="acts">
          {#if isPending(q.status)}
            <button class="ib" title={filtered ? 'Clear filter to reorder' : 'Move up'} onclick={() => move(i, -1)} disabled={busyId === q.id || i === 0 || filtered} aria-label="Move up">↑</button>
            <button class="ib" title={filtered ? 'Clear filter to reorder' : 'Move down'} onclick={() => move(i, 1)} disabled={busyId === q.id || i === shownItems.length - 1 || filtered} aria-label="Move down">↓</button>
            <button class="btn btn-ghost btn-sm" onclick={() => act(q.id, () => api.queueStart(q.id), 'Print started.')} disabled={busyId === q.id}>Start</button>
          {/if}
          {#if isActive(q.status)}
            <button class="btn btn-ghost btn-sm danger-text" onclick={() => act(q.id, () => api.queueStop(q.id), 'Stopped.')} disabled={busyId === q.id}>Stop</button>
          {/if}
          {#if confirmDel === q.id}
            <button class="btn btn-danger btn-sm" onclick={() => act(q.id, () => api.queueDelete(q.id), 'Removed from queue.')} disabled={busyId === q.id}>Confirm</button>
            <button class="btn btn-ghost btn-sm" onclick={() => (confirmDel = null)}>Keep</button>
          {:else}
            <button class="ib" title="Remove" onclick={() => (confirmDel = q.id)} disabled={busyId === q.id} aria-label="Remove">✕</button>
          {/if}
        </span>
      </div>
    {/each}
  </div>
  {#if printers.length === 0}
    <p class="muted hint">No printers yet — items stay unassigned until you <a href="/app/printers/add">add a printer</a>. The scheduler dispatches unassigned jobs to any compatible machine.</p>
  {/if}
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; gap: 1rem; }
  .head h1 { margin: 0; }
  .filt { width: auto; padding: 0.45rem 0.7rem; font-size: 0.85rem; }
  .flt-empty { display: flex; align-items: center; gap: 0.8rem; }
  .empty { text-align: center; padding: 2.6rem; }
  .empty .ic { font-size: 1.8rem; margin-bottom: 0.3rem; }
  .empty p { max-width: 46ch; margin: 0.6rem auto 1.4rem; }
  .list { overflow: visible; }
  .row { display: grid; grid-template-columns: 34px 1fr 190px 110px 200px; gap: 1rem; align-items: center; padding: 0.7rem 1.2rem; border-bottom: 1px solid var(--ophq-border); }
  .row:last-child { border-bottom: none; }
  .row.busy { opacity: 0.55; }
  .head-row { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .ar { text-align: right; }
  .name { font-weight: 500; display: flex; flex-direction: column; gap: 0.15rem; }
  .name .sub { font-size: 0.78rem; color: var(--ophq-muted); font-weight: 400; }
  .sel { padding: 0.4rem 0.5rem; font-size: 0.85rem; }
  .acts { display: flex; gap: 0.35rem; align-items: center; justify-content: flex-end; flex-wrap: wrap; }
  .ib { background: transparent; border: 1px solid var(--ophq-border); color: var(--ophq-text-2); border-radius: var(--radius-sm); width: 28px; height: 28px; cursor: pointer; font-size: 0.9rem; line-height: 1; }
  .ib:hover:not(:disabled) { border-color: var(--ophq-primary); color: var(--ophq-text); }
  .ib:disabled { opacity: 0.35; cursor: default; }
  .btn-danger { background: var(--ophq-danger); color: #fff; }
  .danger-text { color: var(--ophq-danger); }
  .chip.danger { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .chip.primary { color: var(--ophq-primary-2); border-color: rgba(124,108,255,0.35); background: var(--ophq-primary-dim); }
  .hint { margin-top: 1rem; font-size: 0.88rem; }
  .batch { padding: 1.1rem 1.2rem; margin-bottom: 1.2rem; border-color: rgba(124,108,255,0.35); }
  .bhead { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; margin-bottom: 0.9rem; }
  .bhead h3 { margin: 0.2rem 0 0.2rem; font-size: 1.05rem; }
  .bhead .tiny { font-size: 0.78rem; margin: 0; max-width: 60ch; }
  .steps { display: flex; flex-direction: column; gap: 0.4rem; }
  .bstep { display: grid; grid-template-columns: 26px 1fr auto auto; gap: 0.8rem; align-items: center; padding: 0.5rem 0.7rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); }
  .bstep .si { color: var(--ophq-muted); font-size: 0.82rem; }
  .bstep .sp { font-size: 0.9rem; display: flex; align-items: center; gap: 0.5rem; }
  .cchip { font-size: 0.7rem; padding: 0.08rem 0.4rem; border-radius: 999px; border: 1px solid var(--ophq-border); color: var(--ophq-text-2); background: var(--ophq-bg-2); }
  .ttemp { font-size: 0.76rem; color: var(--ophq-text-2); }
  .schip { font-size: 0.72rem; padding: 0.12rem 0.5rem; border-radius: 999px; border: 1px solid var(--ophq-border); color: var(--ophq-text-2); white-space: nowrap; }
  .schip.preheating { color: var(--ophq-accent); border-color: rgba(255,176,32,0.35); background: rgba(255,176,32,0.08); }
  .schip.ready { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .schip.waiting { color: var(--ophq-muted); }
  .schip.timeout { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .bstep.preheating { border-color: rgba(255,176,32,0.28); }
  .toast { padding: 0.7rem 1rem; border-radius: var(--radius-sm); margin-bottom: 1.1rem; font-size: 0.9rem; border: 1px solid; }
  .toast.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .toast.err { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  @media (max-width: 780px) {
    .row { grid-template-columns: 24px 1fr 120px; row-gap: 0.5rem; }
    .row > :nth-child(4), .head-row > :nth-child(4) { display: none; }
    .acts { grid-column: 1 / -1; justify-content: flex-start; }
  }
</style>
