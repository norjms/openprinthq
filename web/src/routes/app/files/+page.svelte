<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PresetSelect from '$lib/components/PresetSelect.svelte';
  import { filterPresetsForConnected } from '$lib/models.js';
  import GcodeViewer from '$lib/components/GcodeViewer.svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';

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
      const is3mf = lower.endsWith('.3mf');
      return { id: f.id ?? f.file_id, name, size: f.size ?? f.file_size ?? null, kind, sliceable, queueable, is3mf };
    });
  }

  // ---- preview (#17): thumbnail + 3MF plates + g-code toolpath ----
  let preview = $state(null);        // the file being previewed
  let previewTab = $state('image');  // 'image' | 'toolpath'
  let plates = $state(null);         // [{ index, name, has_thumbnail, filament_requirements? }]
  let platesLoading = $state(false);
  let curPlate = $state(0);
  let thumbBroken = $state(false);

  async function openPreview(f) {
    preview = f; plates = null; curPlate = 0; thumbBroken = false;
    previewTab = f.queueable ? 'toolpath' : 'image';
    if (f.is3mf) {
      platesLoading = true;
      try {
        const r = await api.filePlates(f.id);
        const list = Array.isArray(r?.plates) ? r.plates : [];
        plates = list.length ? list : null;
      } catch { plates = null; }
      finally { platesLoading = false; }
    }
  }
  function closePreview() { preview = null; plates = null; }
  const curPlateInfo = $derived(plates && plates[curPlate] ? plates[curPlate] : null);
  function fmtFilament(p) {
    const reqs = p?.filament_requirements || p?.filaments || [];
    if (!Array.isArray(reqs) || !reqs.length) return null;
    return reqs.map((r) => {
      const t = r.type || r.filament_type || '';
      const g = r.weight ?? r.grams ?? r.used_g ?? null;
      const col = r.color || r.colour || null;
      return { t, g, col };
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
  // A running session cannot be handed a file: the container environment is
  // fixed at creation, so this has to be set before the session starts.
  function openInSlicer(f) {
    const q = new URLSearchParams({ file: String(f.id), name: f.name });
    window.location.href = '/app/slicer?' + q.toString();
  }

  async function addToQueue(f) {
    queuingId = f.id;
    try {
      const r = await api.addToQueue([f.id]);
      if (r?.errors?.length) showToast('err', r.errors[0].error || 'could not queue file');
      else showToast('ok', `“${f.name}” added to the print queue.`);
    } catch (e) {
      showToast('err', e.message || 'could not queue file');
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

  // ---- batch print dialog (temperature-staggered) ----
  let batchFile = $state(null);
  let batchPrinters = $state([]);      // [{id,name,connected,state,circuit,sel}]
  let batchLoading = $state(false);
  let batchStagger = $state(true);
  let batchMaxPreheat = $state(1);
  let batchBusy = $state(false);
  let batchErr = $state(null);

  async function openBatch(f) {
    batchFile = f; batchErr = null; batchLoading = true; batchPrinters = [];
    try {
      const [pl, circ] = await Promise.all([api.printers().catch(() => []), api.circuits().catch(() => ({}))]);
      const arr = Array.isArray(pl) ? pl : (pl?.printers || pl?.items || []);
      const withState = await Promise.all(arr.map(async (p) => {
        const id = p.id ?? p.printer_id;
        let st = null; try { st = await api.printerStatus(id); } catch { /* offline */ }
        const connected = !!st?.connected;
        const state = (st?.state || (connected ? 'idle' : 'offline')).toString().toLowerCase();
        const busy = /print|run|pause/.test(state);
        return { id, name: p.name || p.model || ('Printer ' + id), connected, state, busy,
                 circuit: circ[id] || '', sel: connected && !busy };
      }));
      batchPrinters = withState;
    } catch (e) { batchErr = e.message || 'could not load printers'; }
    finally { batchLoading = false; }
  }
  function closeBatch() { batchFile = null; }
  const batchSelCount = $derived(batchPrinters.filter((p) => p.sel).length);

  async function doBatch() {
    const chosen = batchPrinters.filter((p) => p.sel);
    if (chosen.length === 0) { batchErr = 'Select at least one printer.'; return; }
    batchBusy = true; batchErr = null;
    try {
      await api.batchStart({
        file_id: batchFile.id, file_name: batchFile.name,
        printers: chosen.map((p) => ({ id: p.id, name: p.name })),
        staggered: batchStagger,
        max_preheat: Math.max(1, Number(batchMaxPreheat) || 1)
      });
      showToast('ok', batchStagger
        ? `Batch started on ${chosen.length} printer${chosen.length > 1 ? 's' : ''} — heat-up is staggered per circuit.`
        : `Batch sent to ${chosen.length} printer${chosen.length > 1 ? 's' : ''}.`);
      closeBatch();
    } catch (e) {
      batchErr = e.message || 'could not start batch';
    } finally { batchBusy = false; }
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
  // Only offer profiles for the printers the user has connected (not OrcaSlicer's
  // full catalogue). A toggle reveals the full list for edge cases.
  let connectedPrinters = $state([]);
  let showAllPrinters = $state(false);
  const shownPrinterPresets = $derived(
    showAllPrinters ? printerPresets : filterPresetsForConnected(printerPresets, connectedPrinters)
  );
  const printerOpts = $derived(toOpts(shownPrinterPresets));
  const isPrinterFiltered = $derived(
    !showAllPrinters && connectedPrinters.length > 0 && shownPrinterPresets.length < printerPresets.length
  );

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
      const [cats, pl] = await Promise.all([
        api.slicerPresets(),
        api.printers().catch(() => [])
      ]);
      connectedPrinters = Array.isArray(pl) ? pl : (pl?.printers || pl?.items || []);
      printerPresets = flatten(cats, 'printer');
      processPresets = flatten(cats, 'process');
      filamentPresets = flatten(cats, 'filament');
      // Concrete machines (e.g. "… 0.4 nozzle") are the sliceable ones; bare
      // model presets are inherited bases the slicer rejects. Default to 0.4mm,
      // chosen from the user's connected-printer profiles.
      const pool = filterPresetsForConnected(printerPresets, connectedPrinters);
      const dfltPrinter =
        pool.find((p) => /0\.4\s*nozzle/i.test(p.id)) ||
        pool.find((p) => /nozzle/i.test(p.id)) ||
        pool[0];
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
      sliceErr = e.message || 'slice failed';
      showToast('err', sliceErr);
    } finally { slicing = false; sliceProgress = null; }
  }
</script>

<PageTitle page="Files" />

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
        <button class="thumb" onclick={() => openPreview(f)} title="Preview">
          <img src={api.fileThumbUrl(f.id)} alt="" loading="lazy" onerror={(e) => { e.currentTarget.style.display = 'none'; e.currentTarget.nextElementSibling.style.display = 'grid'; }} />
          <span class="thumb-fallback"><span class="tk mono">{f.kind}</span></span>
          <span class="peek mono">preview</span>
        </button>
        <div class="fname">{f.name}</div>
        <div class="foot">
          {#if f.size}<span class="muted mono sz">{human(f.size)}</span>{/if}
          {#if f.sliceable}
            <button class="btn btn-ghost btn-sm slicebtn" onclick={() => openSlice(f)}>◈ Slice</button>
            <!-- The slicer runs in a container, so its file dialog cannot see the
                 user's machine. Hand the session the file id and it fetches the
                 model itself before the app opens. -->
            <button class="btn btn-ghost btn-sm slicebtn" onclick={() => openInSlicer(f)}
                    title="Open this model in the in-browser slicer">◱ Open in slicer</button>
          {:else if f.queueable}
            <div class="qacts">
              <button class="btn btn-ghost btn-sm slicebtn" onclick={() => addToQueue(f)} disabled={queuingId === f.id}>
                {queuingId === f.id ? 'Queuing…' : '↳ Add to queue'}
              </button>
              <button class="btn btn-ghost btn-sm slicebtn" onclick={() => openBatch(f)} title="Print on multiple printers with staggered heat-up">⧉ Batch</button>
            </div>
          {/if}
        </div>
      </div>
    {/each}
  </div>
{/if}

{#if preview}
  <div class="overlay" role="presentation" onclick={closePreview}>
    <div class="dialog card preview-dialog" role="dialog" onclick={(e) => e.stopPropagation()}>
      <div class="dhead">
        <div><span class="eyebrow">Preview</span><h3>{preview.name}</h3></div>
        <button class="btn btn-ghost btn-sm" onclick={closePreview} aria-label="Close">✕</button>
      </div>

      {#if preview.queueable}
        <div class="tabs">
          <button class="tab" class:on={previewTab === 'toolpath'} onclick={() => (previewTab = 'toolpath')}>Toolpath</button>
          <button class="tab" class:on={previewTab === 'image'} onclick={() => (previewTab = 'image')}>Image</button>
        </div>
      {/if}

      {#if previewTab === 'toolpath' && preview.queueable}
        {#key preview.id}<GcodeViewer fileId={preview.id} name={preview.name} />{/key}
      {:else}
        <div class="pv-stage">
          {#if !thumbBroken}
            <img class="pv-img" src={curPlateInfo && curPlateInfo.has_thumbnail ? api.filePlateThumbUrl(preview.id, curPlateInfo.index) : api.fileThumbUrl(preview.id)}
                 alt={preview.name} onerror={() => (thumbBroken = true)} />
          {:else}
            <div class="pv-none"><span class="tk mono">{preview.kind}</span><p class="muted">No preview image available.</p></div>
          {/if}
        </div>

        {#if platesLoading}
          <p class="muted hint">Reading plates…</p>
        {:else if plates && plates.length > 1}
          <div class="plates">
            {#each plates as pl, i (pl.index)}
              <button class="plate" class:on={i === curPlate} onclick={() => { curPlate = i; thumbBroken = false; }}>
                {#if pl.has_thumbnail}<img src={api.filePlateThumbUrl(preview.id, pl.index)} alt="" loading="lazy" />{:else}<span class="mono">{pl.index}</span>{/if}
                <span class="pl-n mono">Plate {pl.index}</span>
              </button>
            {/each}
          </div>
        {/if}

        {#if curPlateInfo && fmtFilament(curPlateInfo)}
          <div class="fil-req">
            <span class="evh">Filament</span>
            <div class="fil-list">
              {#each fmtFilament(curPlateInfo) as fr}
                <span class="fchip">{#if fr.col}<span class="sw" style="background:{fr.col}"></span>{/if}{fr.t || 'filament'}{#if fr.g} · {Math.round(fr.g)} g{/if}</span>
              {/each}
            </div>
          </div>
        {/if}
      {/if}

      <div class="flex gap dactions">
        {#if preview.sliceable}
          <button class="btn btn-primary" onclick={() => { const f = preview; closePreview(); openSlice(f); }}>◈ Slice</button>
        {:else if preview.queueable}
          <button class="btn btn-primary" onclick={() => { const f = preview; addToQueue(f); }} disabled={queuingId === preview.id}>{queuingId === preview.id ? 'Queuing…' : '↳ Add to queue'}</button>
          <button class="btn btn-ghost" onclick={() => { const f = preview; closePreview(); openBatch(f); }}>⧉ Batch</button>
        {/if}
        <a class="btn btn-ghost" href={api.fileDownloadUrl(preview.id)} download>Download</a>
        <button class="btn btn-ghost" onclick={closePreview}>Close</button>
      </div>
    </div>
  </div>
{/if}

{#if batchFile}
  <div class="overlay" role="presentation" onclick={closeBatch}>
    <div class="dialog card" role="dialog" onclick={(e) => e.stopPropagation()}>
      <div class="dhead">
        <div><span class="eyebrow">Batch print</span><h3>Print “{batchFile.name}” on multiple printers</h3></div>
        <button class="btn btn-ghost btn-sm" onclick={closeBatch} aria-label="Close">✕</button>
      </div>
      {#if batchLoading}
        <p class="muted">Loading printers…</p>
      {:else if batchPrinters.length === 0}
        <p class="muted">No printers found. <a href="/app/printers/add">Add a printer</a> first.</p>
      {:else}
        <div class="plist">
          {#each batchPrinters as p (p.id)}
            <label class="prow" class:off={!p.connected}>
              <input type="checkbox" bind:checked={p.sel} disabled={!p.connected || p.busy} />
              <span class="pn">{p.name}</span>
              {#if p.circuit}<span class="cchip mono">{p.circuit}</span>{:else}<span class="cchip none mono">no circuit</span>{/if}
              <span class="pst mono {p.busy ? 'busy' : p.connected ? 'ok' : 'off'}">{p.busy ? p.state : p.connected ? 'idle' : 'offline'}</span>
            </label>
          {/each}
        </div>

        <label class="q-opt tog">
          <input type="checkbox" bind:checked={batchStagger} disabled={batchBusy} />
          <span><b>Temperature-staggered start</b> — wait for each printer's bed &amp; chamber to reach target before starting the next on the same circuit.</span>
        </label>

        {#if batchStagger}
          <div class="preheat">
            <label for="mp">Max printers preheating at once <span class="muted">(per circuit)</span></label>
            <input id="mp" class="input mp-in" type="number" min="1" max="20" bind:value={batchMaxPreheat} disabled={batchBusy} />
          </div>
          <p class="muted hint">Printers on different circuits preheat in parallel. Set circuits in <a href="/app/settings">Settings → Power circuits</a>. You can override the wait any time from the <a href="/app/queue">queue</a>.</p>
        {:else}
          <p class="muted hint">All selected printers will start at once (no heat-up staggering).</p>
        {/if}

        {#if batchErr}<p class="err">{batchErr}</p>{/if}
        <div class="flex gap dactions">
          <button class="btn btn-primary" onclick={doBatch} disabled={batchBusy || batchSelCount === 0}>
            {batchBusy ? 'Starting…' : `Start batch (${batchSelCount})`}
          </button>
          <button class="btn btn-ghost" onclick={closeBatch} disabled={batchBusy}>Cancel</button>
        </div>
      {/if}
    </div>
  </div>
{/if}

{#if sliceFile}
  <div class="overlay" role="presentation" onclick={closeSlice}>
    <div class="dialog card" role="dialog" onclick={(e) => e.stopPropagation()}>
      <div class="dhead">
        <div><span class="eyebrow">OrcaSlicer</span><h3>Slice “{sliceFile.name}”</h3></div>
        <button class="btn btn-ghost btn-sm" onclick={closeSlice} aria-label="Close">✕</button>
      </div>
      {#if presetsLoading}
        <p class="muted">Loading presets…</p>
      {:else}
        <div class="field">
          <label for="pr">Printer</label>
          <PresetSelect id="pr" options={printerOpts} bind:value={selPrinter} placeholder="Search printers…" />
          {#if connectedPrinters.length > 0}
            <p class="muted hint">
              {#if isPrinterFiltered}Showing profiles for your connected printers.{:else}Your connected printers.{/if}
              <button type="button" class="linkbtn" onclick={() => (showAllPrinters = !showAllPrinters)}>
                {showAllPrinters ? 'Show only my printers' : 'Show all OrcaSlicer printers'}
              </button>
            </p>
          {/if}
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
  .file { display: flex; flex-direction: column; gap: 0.4rem; padding: 0.7rem; }
  .kind { font-size: 0.72rem; color: var(--ophq-primary-2); }
  .fname { font-weight: 500; word-break: break-word; font-size: 0.92rem; }

  /* preview thumbnail on each card */
  .thumb { position: relative; width: 100%; aspect-ratio: 1/1; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-bg-2); padding: 0; overflow: hidden; cursor: pointer; display: block; }
  .thumb img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .thumb-fallback { position: absolute; inset: 0; display: none; place-items: center; }
  .tk { font-size: 0.85rem; color: var(--ophq-primary-2); letter-spacing: 0.04em; }
  .peek { position: absolute; bottom: 0.3rem; right: 0.4rem; font-size: 0.66rem; color: var(--ophq-text-2); background: rgba(6,10,16,0.7); padding: 0.1rem 0.35rem; border-radius: 999px; opacity: 0; transition: opacity 0.15s; }
  .thumb:hover .peek { opacity: 1; }

  /* preview modal */
  .preview-dialog { max-width: 560px; }
  .tabs { display: flex; gap: 0.3rem; margin-bottom: 0.8rem; }
  .tab { padding: 0.35rem 0.8rem; font-size: 0.82rem; border: 1px solid var(--ophq-border); background: var(--ophq-surface); color: var(--ophq-text-2); border-radius: 999px; cursor: pointer; }
  .tab.on { background: var(--ophq-primary-dim); color: var(--ophq-primary-2); border-color: transparent; }
  .pv-stage { width: 100%; aspect-ratio: 4/3; background: radial-gradient(circle at 50% 40%, #0f1622, #070b11); border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); display: grid; place-items: center; overflow: hidden; }
  .pv-img { max-width: 100%; max-height: 100%; object-fit: contain; }
  .pv-none { display: grid; place-items: center; gap: 0.5rem; text-align: center; }
  .pv-none .tk { font-size: 1.4rem; }
  .plates { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.7rem; }
  .plate { display: flex; flex-direction: column; align-items: center; gap: 0.2rem; padding: 0.25rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); cursor: pointer; width: 64px; }
  .plate.on { border-color: var(--ophq-primary); box-shadow: 0 0 0 1px var(--ophq-primary); }
  .plate img { width: 48px; height: 48px; object-fit: contain; }
  .pl-n { font-size: 0.64rem; color: var(--ophq-text-2); }
  .fil-req { margin-top: 0.8rem; }
  .evh { display: block; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); margin-bottom: 0.35rem; }
  .fil-list { display: flex; flex-wrap: wrap; gap: 0.4rem; }
  .fchip { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.78rem; padding: 0.2rem 0.5rem; border: 1px solid var(--ophq-border); border-radius: 999px; color: var(--ophq-text-2); }
  .sw { width: 0.7rem; height: 0.7rem; border-radius: 50%; border: 1px solid rgba(255,255,255,0.25); display: inline-block; }
  .foot { display: flex; align-items: center; justify-content: space-between; margin-top: auto; padding-top: 0.5rem; }
  .sz { font-size: 0.8rem; }
  .slicebtn { padding: 0.3rem 0.6rem; font-size: 0.8rem; }
  .qacts { display: flex; gap: 0.4rem; flex-wrap: wrap; }

  .plist { display: flex; flex-direction: column; gap: 0.35rem; max-height: 260px; overflow-y: auto; margin-bottom: 0.4rem; }
  .prow { display: flex; align-items: center; gap: 0.6rem; padding: 0.45rem 0.6rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); cursor: pointer; }
  .prow.off { opacity: 0.55; cursor: default; }
  .prow input { width: auto; accent-color: var(--ophq-primary); }
  .prow .pn { flex: 1; font-size: 0.9rem; }
  .cchip { font-size: 0.72rem; padding: 0.1rem 0.4rem; border-radius: 999px; border: 1px solid var(--ophq-border); color: var(--ophq-text-2); background: var(--ophq-bg-2); }
  .cchip.none { opacity: 0.6; }
  .pst { font-size: 0.72rem; }
  .pst.ok { color: var(--ophq-success); }
  .pst.busy { color: var(--ophq-primary-2); }
  .pst.off { color: var(--ophq-muted); }
  .tog { align-items: flex-start; }
  .tog span { line-height: 1.45; }
  .preheat { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin: 0.8rem 0 0.2rem; font-size: 0.9rem; }
  .preheat label { color: var(--ophq-text-2); }
  .mp-in { max-width: 90px; }

  .overlay { position: fixed; inset: 0; background: rgba(5,8,12,0.66); backdrop-filter: blur(3px); display: grid; place-items: center; z-index: 100; padding: 1.5rem; }
  .dialog { width: 100%; max-width: 460px; padding: 1.5rem; box-shadow: var(--shadow-glow); }
  .dhead { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1rem; }
  .dhead h3 { margin: 0.2rem 0 0; font-size: 1.1rem; word-break: break-word; }
  .dactions { margin-top: 1.2rem; }
  .hint { font-size: 0.8rem; margin: 0.2rem 0 0; }
  .linkbtn { background: none; border: 0; padding: 0; font: inherit; color: var(--ophq-primary-2);
    cursor: pointer; text-decoration: underline; }
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
