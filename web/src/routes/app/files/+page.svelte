<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let files = $state([]);

  const SLICEABLE = new Set(['STL', '3MF', 'OBJ']);

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.files || d?.items || d?.results || []);
    return arr.map((f) => {
      const name = f.name ?? f.filename ?? f.display_name ?? 'file';
      const kind = (name.split('.').pop() || '').toUpperCase();
      return { id: f.id ?? f.file_id, name, size: f.size ?? f.file_size ?? null, kind };
    });
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

  async function openSlice(f) {
    sliceFile = f; sliceErr = null; sliceMsg = null;
    presetsLoading = true;
    try {
      const cats = await api.slicerPresets();
      printerPresets = flatten(cats, 'printer');
      processPresets = flatten(cats, 'process');
      filamentPresets = flatten(cats, 'filament');
      selPrinter = printerPresets[0] ? key(printerPresets[0]) : '';
      selProcess = processPresets[0] ? key(processPresets[0]) : '';
      selFilament = filamentPresets[0] ? key(filamentPresets[0]) : '';
    } catch (e) { sliceErr = e.message || 'could not load presets'; }
    finally { presetsLoading = false; }
  }
  function closeSlice() { sliceFile = null; }

  async function doSlice() {
    slicing = true; sliceErr = null; sliceMsg = null;
    const body = {};
    const pr = fromKey(printerPresets, selPrinter);
    const pc = fromKey(processPresets, selProcess);
    const fl = fromKey(filamentPresets, selFilament);
    if (pr) body.printer_preset = { source: pr.source, id: pr.id };
    if (pc) body.process_preset = { source: pc.source, id: pc.id };
    if (fl) body.filament_preset = { source: fl.source, id: fl.id };
    try {
      await api.slice(sliceFile.id, body);
      sliceMsg = 'Slicing started — the G-code will appear in your library shortly.';
      // pick up the sliced output as it lands
      setTimeout(load, 4000); setTimeout(load, 12000);
      setTimeout(() => { if (sliceFile) closeSlice(); }, 1500);
    } catch (e) {
      sliceErr = e.detail?.detail || e.message || 'slice failed';
      if (Array.isArray(sliceErr)) sliceErr = sliceErr.map((x) => x.msg || JSON.stringify(x)).join('; ');
    } finally { slicing = false; }
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
          {#if SLICEABLE.has(f.kind)}
            <button class="btn btn-ghost btn-sm slicebtn" onclick={() => openSlice(f)}>◈ Slice</button>
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
          <select id="pr" class="input" bind:value={selPrinter}>
            {#each printerPresets as p}<option value={`${p.source}::${p.id}`}>{p.name}</option>{/each}
          </select>
        </div>
        <div class="field">
          <label for="pc">Process / quality</label>
          <select id="pc" class="input" bind:value={selProcess}>
            {#each processPresets as p}<option value={`${p.source}::${p.id}`}>{p.name}</option>{/each}
          </select>
        </div>
        <div class="field">
          <label for="fl">Filament</label>
          <select id="fl" class="input" bind:value={selFilament}>
            {#each filamentPresets as p}<option value={`${p.source}::${p.id}`}>{p.name}</option>{/each}
          </select>
        </div>
        {#if sliceErr}<p class="err">{sliceErr}</p>{/if}
        {#if sliceMsg}<p class="ok-msg">{sliceMsg}</p>{/if}
        <div class="flex gap dactions">
          <button class="btn btn-primary" onclick={doSlice} disabled={slicing}>{slicing ? 'Slicing…' : 'Slice'}</button>
          <button class="btn btn-ghost" onclick={closeSlice}>Cancel</button>
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
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; }
</style>
