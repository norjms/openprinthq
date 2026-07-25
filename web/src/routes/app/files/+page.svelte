<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PresetSelect from '$lib/components/PresetSelect.svelte';

  let loading = $state(true);
  let error = $state(null);
  let files = $state([]);

  const SLICEABLE = new Set(['STL', '3MF', 'OBJ']);

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.files || d?.items || d?.results || []);
    return arr.map((f) => {
      const name = f.name ?? f.filename ?? f.display_name ?? 'file';
      const lower = name.toLowerCase();
      const kind = (name.split('.').pop() || '').toUpperCase();
      // Already-sliced output ends in .gcode or .gcode.3mf — printable, not sliceable.
      const queueable = /\.gcode(\.3mf)?$/.test(lower);
      const sliceable = SLICEABLE.has(kind) && !lower.includes('.gcode.') && !queueable;
      return { id: f.id ?? f.file_id, name, size: f.size ?? f.file_size ?? null, kind, sliceable, queueable };
    });
  }

  // ---- add to queue (existing sliced files) ----
  let toast = $state(null);       // { kind: 'ok'|'err', text }
  let queuingId = $state(null);
  let toastTimer = null;
  function showToast(kind, text) {
    toast = { kind, text };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast = null), 6000);
  }
  async function addToQueue(f) {
    queuingId = f.id;
    try {
      const r = await api.addToQueue([f.id]);
      if (r?.errors?.length) showToast('err', r.errors[0].error || 'could not queue file');
      else showToast('ok', `“${f.name}” added to the print queue.`);
    } catch (e) {
      showToast('err', e.detail?.detail || e.message || 'could not queue file');
    } finally {
      queuingId = null;
    }
  }
  function human(n) {
    if (!n && n !== 0) return '';
    const u = ['B', 'KB', 'MB', 'GB']; let i = 0; let v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(v < 10 && i > 0 ? 1 : 0) + ' ' + u[i];
  }
  async function load() {
    loading = true; error = null;
    try { files = norm(await api.files()); }
    catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(load);

  // ---- upload ----
  let uploading = $state(false);
  let upErr = $state(null);
  let fileInput;
  async function onUpload(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    uploading = true; upErr = null;
    try { await api.uploadFile(f); await load(); }
    catch (x) { upErr = x.message || 'upload failed'; }
    finally { uploading = false; e.target.value = ''; }
  }

  // ---- slice dialog ----
  let sliceFile = $state(null);
  let presetsLoading = $state(false);
  let printerPresets = $state([]);
  let processPresets = $state([]);
  let filamentPresets = $state([]);
  let selPrinter = $state('');
  let selProcess = $state('');
  let selFilament = $state('');
  let slicing = $state(false);
  let sliceErr = $state(null);
  let sliceMsg = $state(null);
  let sliceProgress = $state(null);      // 0-100 while a job is running
  let addQueueWhenDone = $state(true);   // one-click: slice → queue

  function flatten(cats, kind) {
    const out = [];
    for (const c of ['standard', 'local', 'cloud', 'orca_cloud']) {
      const arr = cats?.[c]?.[kind];
      if (Array.isArray(arr)) for (const p of arr) if (p?.id && p?.source) out.push({ source: p.source, id: p.id, name: p.name || p.id });
    }
    return out;
  }
  function key(p) { return `${p.source}::${p.id}`; }
  function fromKey(list, k) { return list.find((p) => key(p) === k) || null; }

  const toOpts = (list) => list.map((p) => ({ value: key(p), label: p.name }));
  const printerOpts = $derived(toOpts(printerPresets));

  // Real compatibility: the control-plane joins OrcaSlicer's compatible_printers
  // data, so process/filament are filtered to what actually fits the printer
  // (182→~10, 974→~69), not a name heuristic. `null` = not loaded yet / failed.
  let compat = $state({ process: null, filament: null });
  let showAllPresets = $state(false);
  let compatLoading = $state(false);

  const selPrinterName = $derived.by(() => {
    const p = fromKey(printerPresets, selPrinter);
    return p ? p.name : '';
  });

  function compatFilter(list, set) {
    // Keep non-standard (user local/cloud) presets always; filter stock ones.
    if (showAllPresets || !set || set.size === 0) return list;
    return list.filter((p) => p.source !== 'standard' || set.has(p.name));
  }
  const processOpts = $derived(toOpts(compatFilter(processPresets, compat.process)));
  const filamentOpts = $derived(toOpts(compatFilter(filamentPresets, compat.filament)));
  const compatCount = $derived(
    compat.process && compat.filament && !showAllPresets
      ? { proc: processOpts.length, fil: filamentOpts.length } : null
  );

  function repickDefaults() {
    const badNozzle = /0\.[2368]\s*nozzle/i;
    const procList = compatFilter(processPresets, compat.process);
    const dp = procList.find((p) => /0\.20mm.*standard/i.test(p.id) && !badNozzle.test(p.id))
      || procList.find((p) => /standard/i.test(p.id) && !badNozzle.test(p.id))
      || procList[0];
    selProcess = dp ? key(dp) : '';
    const filList = compatFilter(filamentPresets, compat.filament);
    const df = filList.find((p) => /pla\s*basic/i.test(p.id))
      || filList.find((p) => /\bpla\b/i.test(p.id))
      || filList[0];
    selFilament = df ? key(df) : '';
  }

  // Load compatibility whenever the chosen printer changes, then re-default
  // process + filament to compatible picks.
  $effect(() => {
    const name = selPrinterName;
    if (!name) { compat = { process: null, filament: null }; return; }
    let cancelled = false;
    compatLoading = true;
    api.compatiblePresets(name)
      .then((c) => {
        if (cancelled) return;
        compat = { process: new Set(c.process || []), filament: new Set(c.filament || []) };
        repickDefaults();
      })
      .catch(() => { if (!cancelled) compat = { process: null, filament: null }; })
      .finally(() => { if (!cancelled) compatLoading = false; });
    return () => { cancelled = true; };
  });

  async function openSlice(f) {
    sliceFile = f; sliceErr = null; sliceMsg = null;
    presetsLoading = true;
    try {
      const cats = await api.slicerPresets();
      printerPresets = flatten(cats, 'printer');
      processPresets = flatten(cats, 'process');
      filamentPresets = flatten(cats, 'filament');
      // Concrete machines (e.g. "… 0.4 nozzle") are the sliceable ones; bare
      // model presets are inherited bases the slicer rejects. Default to 0.4mm.
      const dfltPrinter =
        printerPresets.find((p) => /0\.4\s*nozzle/i.test(p.id)) ||
        printerPresets.find((p) => /nozzle/i.test(p.id)) ||
        printerPresets[0];
      selPrinter = dfltPrinter ? key(dfltPrinter) : '';
      // Process + filament defaults are chosen from the printer's COMPATIBLE
      // presets by the $effect (repickDefaults) once compatibility loads.
      selProcess = ''; selFilament = '';
    } catch (e) { sliceErr = e.message || 'could not load presets'; }
    finally { presetsLoading = false; }
  }
  function closeSlice() { sliceFile = null; }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function doSlice() {
    slicing = true; sliceErr = null; sliceMsg = null; sliceProgress = null;
    const body = {};
    const pr = fromKey(printerPresets, selPrinter);
    const pc = fromKey(processPresets, selProcess);
    const fl = fromKey(filamentPresets, selFilament);
    if (pr) body.printer_preset = { source: pr.source, id: pr.id };
    if (pc) body.process_preset = { source: pc.source, id: pc.id };
    if (fl) body.filament_preset = { source: fl.source, id: fl.id };
    try {
      const started = await api.slice(sliceFile.id, body);
      const jobId = started?.job_id;
      if (!addQueueWhenDone || !jobId) {
        // fire-and-forget: G-code just lands in the library
        sliceMsg = 'Slicing started — the G-code will appear in your library shortly.';
        setTimeout(load, 4000); setTimeout(load, 12000);
        setTimeout(() => { if (sliceFile) closeSlice(); }, 1500);
        return;
      }
      // One-click: poll the slice job to completion, then queue the output.
      sliceMsg = 'Slicing…';
      let result = null;
      for (let i = 0; i < 150; i++) { // ~5 min ceiling at 2s
        await sleep(2000);
        let job;
        try { job = await api.sliceJob(jobId); } catch { continue; }
        if (typeof job?.progress === 'number') sliceProgress = Math.round(job.progress);
        if (job?.status === 'completed') { result = job.result; break; }
        if (job?.status === 'failed') {
          throw new Error(job.error_detail || job.error_status || 'slicing failed');
        }
      }
      if (!result) throw new Error('slicing timed out — check the library shortly');
      const newId = result.library_file_id;
      sliceMsg = 'Sliced — adding to the print queue…';
      const q = await api.addToQueue([newId]);
      if (q?.errors?.length) {
        sliceErr = q.errors[0].error || 'sliced, but could not add to queue';
        showToast('err', sliceErr);
      } else {
        sliceMsg = 'Sliced and added to the print queue.';
        showToast('ok', 'Sliced and added to the print queue.');
      }
      load();
      setTimeout(() => { if (sliceFile) closeSlice(); }, 1600);
    } catch (e) {
      sliceErr = e.detail?.detail || e.message || 'slice failed';
      if (Array.isArray(sliceErr)) sliceErr = sliceErr.map((x) => x.msg || JSON.stringify(x)).join('; ');
      showToast('err', sliceErr);
    } finally { slicing = false; sliceProgress = null; }
  }
</script>

<svelte:head><title>Files · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div><h1>Files</h1><p class="muted">Your private 3MF / STL / G-code library.</p></div>
  <div class="flex gap">
    <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
    <input type="file" bind:this={fileInput} onchange={onUpload} hidden accept=".3mf,.stl,.gcode,.gco,.obj" />
    <button class="btn btn-primary btn-sm" onclick={() => fileInput.click()} disabled={uploading}>
      {uploading ? 'Uploading…' : '+ Upload'}
    </button>
  </div>
</div>
{#if upErr}<p class="uperr">{upErr}</p>{/if}
{#if toast}
  <div class="toast {toast.kind}">
    <span>{toast.text}</span>
    {#if toast.kind === 'ok'}<a href="/app/queue" class="tlink">View queue →</a>{/if}
  </div>
{/if}

{#if loading}
  <div class="card card-pad muted">Loading library…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else if files.length === 0}
  <div class="card card-pad empty">
    <div class="ic">🗀</div>
    <h3>No files yet</h3>
    <p class="muted">Upload 3MF, STL or G-code to your private library — they'll be sliceable and printable across your fleet.</p>
  </div>
{:else}
  <div class="grid files">
    {#each files as f}
      <div class="card card-pad file">
        <div class="kind mono">{f.kind}</div>
        <div class="fname">{f.name}</div>
        <div class="foot">
          {#if f.size}<span class="muted mono sz">{human(f.size)}</span>{/if}
          {#if f.sliceable}
            <button class="btn btn-ghost btn-sm slicebtn" onclick={() => openSlice(f)}>◈ Slice</button>
          {:else if f.queueable}
            <button class="btn btn-ghost btn-sm slicebtn" onclick={() => addToQueue(f)} disabled={queuingId === f.id}>
              {queuingId === f.id ? 'Queuing…' : '↳ Add to queue'}
            </button>
          {/if}
        </div>
      </div>
    {/each}
  </div>
{/if}

{#if sliceFile}
  <div class="overlay" role="presentation" onclick={closeSlice}>
    <div class="dialog card" role="dialog" onclick={(e) => e.stopPropagation()}>
      <div class="dhead">
        <div><span class="eyebrow">OrcaSlicer</span><h3>Slice “{sliceFile.name}”</h3></div>
        <button class="btn btn-ghost btn-sm" onclick={closeSlice}>✕</button>
      </div>
      {#if presetsLoading}
        <p class="muted">Loading presets…</p>
      {:else}
        <div class="field">
          <label for="pr">Printer</label>
          <PresetSelect id="pr" options={printerOpts} bind:value={selPrinter} placeholder="Search printers…" />
        </div>
        <div class="field">
          <label for="pc">Process / quality</label>
          <PresetSelect id="pc" options={processOpts} bind:value={selProcess} placeholder="Search processes…" />
        </div>
        <div class="field">
          <label for="fl">Filament</label>
          <PresetSelect id="fl" options={filamentOpts} bind:value={selFilament} placeholder="Search filaments…" />
        </div>
        <p class="muted hint">
          {#if compatLoading}Checking compatible presets…
          {:else if compatCount}Showing {compatCount.proc} process &amp; {compatCount.fil} filament presets compatible with this printer.
          {:else}Presets must match the printer's model &amp; nozzle.{/if}
        </p>
        <label class="q-opt">
          <input type="checkbox" bind:checked={showAllPresets} disabled={slicing} />
          <span>Show all presets (ignore printer compatibility)</span>
        </label>
        <label class="q-opt">
          <input type="checkbox" bind:checked={addQueueWhenDone} disabled={slicing} />
          <span>Add to print queue when slicing finishes</span>
        </label>
        {#if slicing && sliceProgress != null}
          <div class="bar"><div class="fill" style="width:{sliceProgress}%"></div></div>
        {/if}
        {#if sliceErr}<p class="err">{sliceErr}</p>{/if}
        {#if sliceMsg}<p class="ok-msg">{sliceMsg}{#if slicing && sliceProgress != null} {sliceProgress}%{/if}</p>{/if}
        <div class="flex gap dactions">
          <button class="btn btn-primary" onclick={doSlice} disabled={slicing}>
            {slicing ? 'Slicing…' : (addQueueWhenDone ? 'Slice & queue' : 'Slice')}
          </button>
          <button class="btn btn-ghost" onclick={closeSlice} disabled={slicing}>Cancel</button>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; }
  .head h1 { margin: 0; }
  .uperr { color: var(--ophq-danger); font-size: 0.88rem; margin: -0.8rem 0 1rem; }
  .empty { text-align: center; padding: 2.6rem; }
  .empty .ic { font-size: 1.8rem; margin-bottom: 0.3rem; }
  .empty p { max-width: 48ch; margin: 0.6rem auto 0; }
  .files { grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); }
  .file { display: flex; flex-direction: column; gap: 0.4rem; }
  .kind { font-size: 0.72rem; color: var(--ophq-primary-2); }
  .fname { font-weight: 500; word-break: break-word; font-size: 0.92rem; }
  .foot { display: flex; align-items: center; justify-content: space-between; margin-top: auto; padding-top: 0.5rem; }
  .sz { font-size: 0.8rem; }
  .slicebtn { padding: 0.3rem 0.6rem; font-size: 0.8rem; }

  .overlay { position: fixed; inset: 0; background: rgba(5,8,12,0.66); backdrop-filter: blur(3px); display: grid; place-items: center; z-index: 100; padding: 1.5rem; }
  .dialog { width: 100%; max-width: 460px; padding: 1.5rem; box-shadow: var(--shadow-glow); }
  .dhead { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1rem; }
  .dhead h3 { margin: 0.2rem 0 0; font-size: 1.1rem; word-break: break-word; }
  .dactions { margin-top: 1.2rem; }
  .hint { font-size: 0.8rem; margin: 0.2rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; }

  .toast { display: flex; align-items: center; gap: 1rem; justify-content: space-between;
    padding: 0.7rem 1rem; border-radius: var(--radius-sm); margin: -0.6rem 0 1.1rem; font-size: 0.9rem; border: 1px solid; }
  .toast.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .toast.err { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .toast .tlink { color: inherit; font-weight: 600; white-space: nowrap; }
  .q-opt { display: flex; align-items: center; gap: 0.5rem; margin: 0.6rem 0 0.2rem; font-size: 0.9rem; color: var(--ophq-text-2); cursor: pointer; }
  .q-opt input { width: auto; accent-color: var(--ophq-primary); }
  .bar { height: 8px; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; margin: 0.6rem 0 0.2rem; }
  .fill { height: 100%; background: linear-gradient(90deg, var(--ophq-primary), var(--ophq-primary-2)); transition: width 0.4s ease; }
</style>
