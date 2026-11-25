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

  // OpenPrintHQ Farm has one slicer, OrcaSlicer, run headless: the slicing
  // service runs its command line. This page is that service and its settings:
  // how slicers send to the queue. When the installer added the OrcaSlicer
  // desktop (info.desktop_url), it opens in the hub, from the sidebar.
  let info = $state({ version: null, desktop_url: null, print_host: null, desktop_print_host: null, print_host_keys: 0,
                      addon: null, desktop: null });
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

  // Q9: OrcaSlicer prints to the queue through the OrcaSlicer add-on. The desktop in the browser is connected by
  // the farm; on a computer, the person installs the add-on and pastes a key.
  let addonKey = $state(null);
  let addonBusy = $state(false);
  let addonError = $state(null);
  async function makeAddonKey() {
    if (addonKey === null && !confirm('Make a key for the add-on? A key made here before stops working.')) return;
    addonBusy = true; addonError = null;
    try { addonKey = await api.slicerAddonKey(); }
    catch (e) { addonError = e.message || 'could not make a key'; }
    finally { addonBusy = false; }
  }
  let connectBusy = $state(false);
  let connectError = $state(null);
  async function connectDesktop() {
    if (info.desktop?.connected && !confirm('Connect OrcaSlicer in the browser again? It gets a new key and the farm\'s printers as they are now.')) return;
    connectBusy = true; connectError = null;
    try {
      const out = await api.slicerDesktopConnect();
      info.desktop = { ...(info.desktop || {}), pending: true, printers: out.printers, skipped: out.skipped, scope: out.scope };
    } catch (e) { connectError = e.message || 'could not connect it'; }
    finally { connectBusy = false; }
  }
  function when(at) {
    try { return new Date(at).toLocaleString(); } catch { return at; }
  }

  // The OrcaSlicer desktop itself is the hub's page for it, from the sidebar (the hub's docs/apps.md).
  const DESKTOP_PAGE = '/open/orcaslicer';

  onMount(load);
</script>

<PageTitle page="Slicer" />

<PageHead title="Slicer" description="OrcaSlicer{info.version ? ' ' + info.version : ''} on the server: the farm slices with it, no desktop app needed. Here are its settings and how slicers send to the queue.">
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

  {#if info.desktop}
    <div class="card card-pad send" data-testid="desktop-queue">
      <span class="eyebrow">Print from OrcaSlicer in the browser</span>
      {#if info.desktop.pending}
        <p><b>Almost connected.</b> Close OrcaSlicer in <a href={DESKTOP_PAGE} data-sveltekit-reload>its desktop</a> (File, Quit): it opens
          again, connected to your <a href="{basePath}/app/queue">print queue</a>.</p>
      {:else if info.desktop.connected}
        <p><b>Connected</b>{info.desktop.at ? ' since ' + when(info.desktop.at) : ''}. In OrcaSlicer, choose a printer
          ending in "OpenPrintHQ Farm", slice, then <b>Print</b> puts the plate first in line on that printer and
          <b>Send</b> adds it to the end of its queue. The farm starts it when the printer is ready.</p>
      {:else if info.desktop.error}
        <p><b>Not connected:</b> {info.desktop.error}. Connect it again; if it fails again, restart the desktop from
          the installer.</p>
      {:else}
        <p>Connect it to send plates from OrcaSlicer to your <a href="{basePath}/app/queue">print queue</a>, for every
          printer here, Bambu Lab included.</p>
      {/if}
      {#if info.desktop.printers?.length}
        <div class="chips">{#each info.desktop.printers as p}<span class="chip primary">{p.preset}</span>{/each}</div>
      {/if}
      {#if info.desktop.skipped?.length}
        <p class="muted small">No OrcaSlicer printer to match {info.desktop.skipped.map((p) => p.name).join(', ')}: in
          OrcaSlicer, choose its printer, name it with the farm printer's name, and set <b>Printer agent</b> to
          OpenPrintHQ (Printer settings, Basic information, advanced mode).</p>
      {/if}
      {#if info.desktop.scope === 'submit'}
        <p class="muted small">Everyone who opens this desktop uses its connection, so it can send work but not change
          the queue: reorder, cancel and printer controls stay here.</p>
      {/if}
      {#if connectError}<p class="muted">{connectError}</p>{/if}
      <div class="flex center gap wrap">
        <button class="btn btn-sm" onclick={connectDesktop} disabled={connectBusy} data-testid="desktop-connect">
          <Icon name="link" size={15} />{connectBusy ? 'Connecting' : (info.desktop.connected || info.desktop.pending ? 'Connect again' : 'Connect it')}
        </button>
        {#if info.desktop_url}
          <a class="btn btn-ghost btn-sm" href={DESKTOP_PAGE} data-sveltekit-reload data-testid="desktop-open"><Icon name="external" size={15} />Open OrcaSlicer</a>
        {/if}
      </div>
    </div>
  {/if}

  {#if info.addon}
    <div class="card card-pad send" data-testid="addon">
      <span class="eyebrow">Print from OrcaSlicer on your computer</span>
      <p class="muted">The OrcaSlicer add-on sends plates to your <a href="{basePath}/app/queue">print queue</a>
        instead of straight to a printer, for every printer here, Bambu Lab included, and adds a tab for the farm.
        OrcaSlicer 2.5.1 or later.</p>
      <ol class="steps muted">
        <li>Download the add-on, then in OrcaSlicer: <b>Plugins</b>, <b>Install local plugin</b>, and pick the file.
          Keep its name, <code>{info.addon.file}</code>.</li>
        <li>OrcaSlicer, <b>Preferences</b>: turn on <b>Use printer agents instead of print hosts</b>.</li>
        <li>The add-on's <b>Config</b> page: this address and a key made here.</li>
        <li>For each printer: in OrcaSlicer, a printer named with the farm printer's name, such as
          "H2D - OpenPrintHQ Farm", with <b>Printer agent</b> set to OpenPrintHQ and any print host.</li>
      </ol>
      <p><code>{info.addon.url}</code></p>
      {#if addonKey}
        <p class="muted small">Your key, shown only now:</p>
        <p><code class="key" data-testid="addon-key">{addonKey.api_key}</code></p>
      {/if}
      {#if addonError}<p class="muted">{addonError}</p>{/if}
      <div class="flex center gap wrap">
        <a class="btn btn-sm" href="{basePath}/api/slicer/addon" download={info.addon.file} data-testid="addon-download">
          <Icon name="download" size={15} />Download the add-on{info.addon.version ? ' ' + info.addon.version : ''}</a>
        <button class="btn btn-ghost btn-sm" onclick={makeAddonKey} disabled={addonBusy}>
          <Icon name="key" size={15} />{addonBusy ? 'Making' : 'Make a key'}</button>
      </div>
    </div>
  {/if}

  <details class="card card-pad send other">
    <summary><span class="eyebrow">Other slicers: an OctoPrint print host</span></summary>
    <p class="muted">PrusaSlicer, SuperSlicer and other slicers that upload to OctoPrint can send to the queue too:
      under <b>Physical printer</b> or <b>Connection</b>, choose OctoPrint, enter the address below as the host, and
      paste the key. Upload and Print adds the plate to the queue; Upload only adds it to Files.</p>
    {#if info.desktop_print_host}
      <p class="muted small">From the desktop in the browser:</p>
      <p><code>{info.desktop_print_host}</code></p>
      {#if info.print_host}<p class="muted small">From a computer:</p>{/if}
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
  </details>

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
  .wsbar { display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; padding: 0.7rem 1rem; border-bottom: 1px solid var(--ah-row-line); }
  .send { margin-bottom: 1.2rem; }
  .send p { margin: 0.4rem 0; }
  .send code { font-family: var(--ah-font-mono); font-size: 0.88em; padding: 0.1rem 0.35rem;
               border-radius: 6px; border: 1px solid var(--ah-panel-line); background: var(--ah-panel); word-break: break-all; }
  .send .btn { margin-top: 0.4rem; }
  .send .steps { margin: 0.5rem 0; padding-left: 1.3rem; }
  .send .steps li { margin: 0.25rem 0; }
  .send .wrap { flex-wrap: wrap; }
  .other summary { cursor: pointer; list-style: revert; }
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
