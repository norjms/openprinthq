<script>
  import { base as basePath } from '$app/paths';
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import PageHead from '$lib/components/PageHead.svelte';
  import Icon from '$lib/components/AppIcon.svelte';

  let loading = $state(true);
  let error = $state(null);
  let models = $state([]);
  let presetCats = $state([]);
  let connected = $state(false);

  // ArtisanHub Farm has one slicer, OrcaSlicer. The slicing service runs its
  // command line; when the ArtisanHub installer added the OrcaSlicer desktop,
  // the same version runs in the browser too, and info.desktop_url says where.
  let info = $state({ version: null, desktop_url: null, print_host: null, desktop_print_host: null, print_host_keys: 0 });
  let full = $state(false);
  let keyBusy = $state(false);
  let keyError = $state(null);
  let newKey = $state(null);

  async function load() {
    loading = true; error = null;
    try {
      const m = await api.slicerModels();
      models = m && typeof m === 'object' && !Array.isArray(m) ? Object.keys(m) : (Array.isArray(m) ? m : []);
      connected = models.length > 0;
      try {
        const p = await api.slicerPresets();
        presetCats = p && typeof p === 'object' ? Object.keys(p).filter((k) => !k.endsWith('_status')) : [];
      } catch { presetCats = []; }
    } catch (e) {
      error = e.status === 409 ? 'no-instance' : (e.message || 'slicer unreachable');
    } finally {
      loading = false;
    }
    try { info = await api.slicerInfo(); } catch { /* the page works without it */ }
  }

  // Shown once: the farm keeps only the key itself to check it, and making a new
  // one revokes the last.
  async function makeKey() {
    if (info.print_host_keys > 0 && !confirm('Make a new key? The one OrcaSlicer has now will stop working.')) return;
    keyBusy = true; keyError = null; newKey = null;
    try {
      newKey = await api.slicerPrintHostKey();
      info.print_host_keys = 1;
    } catch (e) { keyError = e.message || 'could not make a key'; }
    finally { keyBusy = false; }
  }

  function onKey(e) { if (e.key === 'Escape') full = false; }

  onMount(() => {
    load();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
</script>

<PageTitle page="Slicer" />

<PageHead title="Slicer" description="OrcaSlicer{info.version ? ' ' + info.version : ''}, built in: slice on the server, no desktop app needed.">
  {#snippet actions()}
    <button class="btn" onclick={load}><Icon name="refresh" size={16} />Refresh</button>
  {/snippet}
</PageHead>

{#if loading}
  <div class="card card-pad muted">Checking slicer</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="{basePath}/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Slicer unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else}
  {#if info.desktop_url}
    <div class="card workspace" class:full>
      <div class="wsbar">
        <div class="flex center gap">
          <span class="dot on"></span>
          <b>OrcaSlicer desktop</b>
          {#if info.version}<span class="muted small">{info.version}</span>{/if}
        </div>
        <div class="flex center gap">
          <a class="btn btn-ghost btn-sm" href={info.desktop_url} target="_blank" rel="noopener"><Icon name="external" size={15} />Open in a new tab</a>
          <button class="btn btn-ghost btn-sm" onclick={() => (full = !full)}><Icon name="expand" size={15} />{full ? 'Exit full screen' : 'Full screen'}</button>
        </div>
      </div>
      <iframe title="OrcaSlicer desktop" src={info.desktop_url} allow="clipboard-read; clipboard-write; fullscreen"></iframe>
    </div>
  {/if}

  <div class="card card-pad status">
    <div class="flex between center">
      <div class="flex center gap">
        <span class="dot" class:on={connected}></span>
        <div>
          <h3>OrcaSlicer {connected ? 'connected' : 'not reachable'}</h3>
          <p class="muted">Server-side slicing engine{connected ? ', ready to slice STL and 3MF' : ''}.</p>
        </div>
      </div>
      <span class="chip {connected ? 'ok' : 'accent'}">{connected ? 'Ready' : 'Offline'}</span>
    </div>
  </div>

  <div class="card card-pad send">
    <span class="eyebrow">Send plates from OrcaSlicer</span>
    <p class="muted">OrcaSlicer, in the browser above or on your own computer, can send a sliced plate
      straight into your <a href="{basePath}/app/queue">print queue</a>. In OrcaSlicer's printer
      settings, under <b>Connection</b>, choose <b>Octo/Klipper</b>, enter the address below as the
      host, and paste the key. One key serves both.</p>
    {#if info.desktop_print_host}
      <p class="muted small">From OrcaSlicer in the browser:</p>
      <p><code>{info.desktop_print_host}</code></p>
      {#if info.print_host}<p class="muted small">From OrcaSlicer on a computer:</p>{/if}
    {/if}
    {#if info.print_host}<p><code>{info.print_host}</code></p>{/if}
    {#if newKey}
      <p class="muted small">Your key, shown only now:</p>
      <p><code class="key">{newKey.api_key}</code></p>
    {/if}
    {#if keyError}<p class="muted">{keyError}</p>{/if}
    <button class="btn btn-sm" onclick={makeKey} disabled={keyBusy || !info.print_host}>
      <Icon name="key" size={15} />{keyBusy ? 'Making' : (info.print_host_keys > 0 ? 'Make a new key' : 'Make a key')}
    </button>
  </div>

  <div class="grid two">
    <div class="card card-pad">
      <span class="eyebrow">Printer models ({models.length})</span>
      <div class="chips">
        {#each models.slice(0, 24) as m}<span class="chip">{m}</span>{/each}
        {#if models.length > 24}<span class="chip">+{models.length - 24} more</span>{/if}
        {#if models.length === 0}<p class="muted">None yet</p>{/if}
      </div>
    </div>
    <div class="card card-pad">
      <span class="eyebrow">Preset sources</span>
      <div class="chips">
        {#each presetCats as c}<span class="chip primary">{c}</span>{/each}
        {#if presetCats.length === 0}<p class="muted">None yet</p>{/if}
      </div>
    </div>
  </div>

  <div class="card card-pad how">
    <span class="eyebrow">How to slice</span>
    <p class="muted">Upload a model in <a href="{basePath}/app/files">Files</a>, then use its <b>Slice</b> action. Pick a printer, process and filament (searchable, and filtered to what suits the printer). OrcaSlicer runs it on the server and, with "Slice and queue" on, the result drops straight into your <a href="{basePath}/app/queue">print queue</a>.</p>
  </div>
{/if}

<style>
  .workspace { margin-bottom: 1.2rem; overflow: hidden; }
  .wsbar { display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; padding: 0.7rem 1rem; border-bottom: 1px solid var(--ah-row-line); }
  .workspace iframe { display: block; width: 100%; height: 72vh; min-height: 460px; border: 0; background: var(--ah-solid-panel); }
  .workspace.full { position: fixed; inset: 0; z-index: 200; margin: 0; border-radius: 0; display: flex; flex-direction: column; }
  .workspace.full iframe { flex: 1; height: auto; min-height: 0; }
  .send { margin-bottom: 1.2rem; }
  .send p { margin: 0.4rem 0; }
  .send code { font-family: var(--ah-font-mono); font-size: 0.88em; padding: 0.1rem 0.35rem;
               border-radius: 6px; border: 1px solid var(--ah-panel-line); background: var(--ah-panel); word-break: break-all; }
  .send .btn { margin-top: 0.4rem; }
  .status { margin-bottom: 1.2rem; }
  .status h3 { margin: 0; font-size: 1.1rem; }
  .status p { margin: 0.2rem 0 0; font-size: 0.9rem; }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--ah-text-3); box-shadow: 0 0 0 4px rgba(127,140,161,0.15); flex: none; }
  .dot.on { background: var(--ophq-success); box-shadow: 0 0 0 4px rgba(155,226,143,0.18); }
  .two { grid-template-columns: 1fr 1fr; }
  .chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.7rem; }
  .chips p { margin: 0; }
  .how { margin-top: 1.2rem; }
  .how p { margin: 0.5rem 0 0; }
  @media (max-width: 820px) { .two { grid-template-columns: 1fr; } }
</style>
