<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let items = $state([]);
  let printers = $state([]);
  let busyId = $state(null);      // queue item id with an action in flight
  let confirmDel = $state(null);  // id pending delete confirmation
  let toast = $state(null);
  let toastTimer = null;

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
      timeSec: q.print_time_seconds ?? null
    }));
  }
  function normPrinters(d) {
    const arr = Array.isArray(d) ? d : (d?.printers || d?.items || d?.results || []);
    return arr.map((p) => ({ id: p.id ?? p.printer_id, name: p.name ?? p.model ?? ('Printer ' + (p.id ?? '')) }));
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
  onMount(load);

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

<svelte:head><title>Print queue · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div><h1>Print queue</h1><p class="muted">One queue across your whole fleet.</p></div>
  <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
</div>

{#if toast}
  <div class="toast {toast.kind}">{toast.text}</div>
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
{:else}
  <div class="card list">
    <div class="row head-row muted mono"><span>#</span><span>Job</span><span>Printer</span><span>Status</span><span class="ar">Actions</span></div>
    {#each items as q, i (q.id)}
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
            <button class="ib" title="Move up" onclick={() => move(i, -1)} disabled={busyId === q.id || i === 0}>↑</button>
            <button class="ib" title="Move down" onclick={() => move(i, 1)} disabled={busyId === q.id || i === items.length - 1}>↓</button>
            <button class="btn btn-ghost btn-sm" onclick={() => act(q.id, () => api.queueStart(q.id), 'Print started.')} disabled={busyId === q.id}>Start</button>
          {/if}
          {#if isActive(q.status)}
            <button class="btn btn-ghost btn-sm danger-text" onclick={() => act(q.id, () => api.queueStop(q.id), 'Stopped.')} disabled={busyId === q.id}>Stop</button>
          {/if}
          {#if confirmDel === q.id}
            <button class="btn btn-danger btn-sm" onclick={() => act(q.id, () => api.queueDelete(q.id), 'Removed from queue.')} disabled={busyId === q.id}>Confirm</button>
            <button class="btn btn-ghost btn-sm" onclick={() => (confirmDel = null)}>Keep</button>
          {:else}
            <button class="ib" title="Remove" onclick={() => (confirmDel = q.id)} disabled={busyId === q.id}>✕</button>
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
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; }
  .head h1 { margin: 0; }
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
  .toast { padding: 0.7rem 1rem; border-radius: var(--radius-sm); margin-bottom: 1.1rem; font-size: 0.9rem; border: 1px solid; }
  .toast.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .toast.err { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  @media (max-width: 780px) {
    .row { grid-template-columns: 24px 1fr 120px; row-gap: 0.5rem; }
    .row > :nth-child(4), .head-row > :nth-child(4) { display: none; }
    .acts { grid-column: 1 / -1; justify-content: flex-start; }
  }
</style>
