<script>
  import { farmName, FarmName } from '$lib/nav.svelte.js';
  import Page from '$lib/Page.svelte';
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api, fmtMoney } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { licenseChip } from '$lib/license.js';
  import SourceImport from '$lib/SourceImport.svelte';
  import { Icon } from 'openprinthq-ui';
  import Glyph from '$lib/Glyph.svelte';
  let byLink = $state(false);

  let items = $state([]);
  let total = $state(0);
  let pg = $state(1);
  let loading = $state(false);
  let done = $state(false);
  let cats = $state([]);
  let stats = $state(null);
  const sp = page.url.searchParams;
  let q = $state(sp.get('q') || '');
  let category = $state(sp.get('category') || '');
  let view = $state(sp.get('view') || 'all');
  let sort = $state(sp.get('sort') || 'recent');
  let size = $state(Number(globalThis.localStorage?.getItem('hub.size') || 260));
  let selecting = $state(false);
  let selected = $state(new Set());
  let sentinel;
  let timer;

  function params(p) {
    const u = new URLSearchParams({ page: String(p), per: '60', sort });
    if (q) u.set('q', q);
    if (category) u.set('category', category);
    if (view === 'live') u.set('live', 'true');
    if (view === 'hidden') u.set('live', 'false');
    if (view === 'supplies') { u.set('live', 'true'); u.set('attention', 'supplies'); }
    if (view === 'archived') u.set('status', 'archived');
    return u;
  }
  async function load(reset = false) {
    if (loading) return;
    if (reset) { pg = 1; done = false; }
    if (done) return;
    loading = true;
    try {
      const r = await api.get('/api/models?' + params(pg));
      items = reset ? r.items : [...items, ...r.items];
      total = r.total;
      done = items.length >= r.total || r.items.length === 0;
      pg += 1;
    } finally { loading = false; }
  }
  function refilter() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const u = new URLSearchParams();
      if (q) u.set('q', q); if (category) u.set('category', category);
      if (view !== 'all') u.set('view', view); if (sort !== 'recent') u.set('sort', sort);
      history.replaceState(history.state, '', u.toString() ? '?' + u : '/');
      load(true);
    }, 200);
  }
  // Widget column: the farm's printers and the open orders, as on the hub's home in the design.
  let farm = $state(null);
  let farmErr = $state('');
  let orders = $state(null);
  async function loadSide() {
    try { farm = await api.get('/api/farm'); farmErr = ''; } catch (e) { farm = null; farmErr = e.message || 'Not connected'; }
    try { orders = (await api.get('/api/orders')).filter((o) => !['completed', 'cancelled', 'refunded', 'failed'].includes(o.status)); } catch { orders = []; }
  }
  const pState = (p) => (p.out_of_service ? ['bad', 'Out of service'] : !p.online ? ['', 'Offline'] : p.error ? ['bad', 'Error'] : p.busy ? ['live', 'Printing'] : p.ready ? ['ok', 'Ready'] : ['warn', 'Needs the plate cleared']);
  onMount(() => {
    loadSide();
    load(true);
    api.get('/api/categories').then((c) => (cats = c));
    api.get('/api/stats').then((s) => (stats = s));
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) load(); }, { rootMargin: '800px' });
    io.observe(sentinel);
    return () => io.disconnect();
  });
  $effect(() => { try { localStorage.setItem('hub.size', String(size)); } catch {} });

  function toggleSel(id) {
    const s = new Set(selected); s.has(id) ? s.delete(id) : s.add(id); selected = s;
  }
  async function bulk(live) {
    const r = await attempt(() => api.post('/api/products/bulk-live', { model_ids: [...selected], live }));
    toast(`${live ? 'Going live' : 'Hiding'}: ${r.queued.length} queued${r.refused.length ? `, ${r.refused.length} need attention` : ''}`);
    if (r.refused.length) toast(r.refused.slice(0, 3).map((x) => `${x.code}: ${x.problems.join(', ')}`).join(' | '), 'bad', 9000);
    selected = new Set(); selecting = false; setTimeout(() => load(true), 1500);
  }
  async function bulkModels(action) {
    let confirm = null;
    if (action === 'delete') {
      confirm = prompt(`Permanently delete ${selected.size} model(s)? This removes their photos, files, store drafts, DAM photos and files in the farm, and stops them being re-imported. License records are kept. Type DELETE to confirm.`);
      if (confirm == null) return;
    }
    const r = await attempt(() => api.post('/api/models/bulk', { model_ids: [...selected], action, confirm }));
    toast(`${action === 'delete' ? 'Deleting' : action === 'archive' ? 'Archived' : 'Restored'}: ${r.done.length}${r.refused.length ? `, ${r.refused.length} skipped` : ''}`);
    if (r.refused.length) toast(r.refused.slice(0, 3).map((x) => `${x.code}: ${x.problem}`).join(' | '), 'bad', 10000);
    selected = new Set(); selecting = false; setTimeout(() => load(true), action === 'delete' ? 2500 : 300);
  }
  let profiles = $state([]);
  async function bulkSlice() {
    if (!profiles.length) { try { profiles = (await api.get('/api/slicer/status')).profiles.filter((p) => p.active); } catch {} }
    if (!profiles.length) { toast('Add a printer under Slicing, Printer profiles first', 'bad'); return; }
    const names = profiles.map((p, i) => `${i + 1}. ${p.name}${p.is_default ? ' (default)' : ''}`).join('\n');
    const pick = prompt(`Slice ${selected.size} model(s) for which printer?\n${names}`, '1');
    const prof = profiles[Number(pick) - 1];
    if (!prof) return;
    const r = await attempt(() => api.post('/api/models/bulk-slice', { model_ids: [...selected], profile_id: prof.id, make_production: false }));
    toast(`${r.queued} model(s) queued for slicing on ${prof.name}; results land on each model's Files tab`);
    selected = new Set(); selecting = false;
  }
  async function bulkPush() {
    const r = await attempt(() => api.post('/api/products/bulk-push', { model_ids: [...selected], live: false }));
    toast(`${r.queued} product(s) queued to push as drafts`);
    selected = new Set(); selecting = false;
  }
</script>

<Page title="Models" description="Every model as a product: photos, print settings, files, colours, parts and store listing.">
{#snippet actions()}
  <a href="/get-app" class="btn" data-sveltekit-reload title="Install the OpenPrintHQ app on an Android phone"><Icon name="download" size={16} />Get the app</a>
  <button class="btn" onclick={() => (byLink = true)} title="Paste a model link from Printables, Thingiverse, MakerWorld, Thangs, MyMiniFactory, Cults3D or any model file"><Glyph name="link" />Add by link</button>
  <a href="/new" class="btn primary"><Icon name="plus" size={16} />Add model</a>
{/snippet}
{#snippet widgets()}
  <section class="ah-glass ah-card">
    <div class="flex items-center gap-[10px]"><div class="ah-icon-tile"><Icon name="printer" /></div>
      <div style="flex:1;min-width:0"><h3 class="ah-widget-title">Printers</h3>
        <div class="ah-muted">{#if farm}{farm.printers.filter((p) => p.busy).length} printing, {farm.printers.filter((p) => p.online && !p.busy && !p.ready).length + farm.checks.length} need you{:else}{farmErr || 'Loading...'}{/if}</div></div>
      <a class="ah-round-btn" href="/farm?view=printers" aria-label="Open Printers"><Icon name="chevron-right" size={14} /></a></div>
    {#if farm}
      <div class="flex flex-col gap-2">
        {#each farm.printers as p (p.name)}
          {@const st = pState(p)}
          <div class="panel" style="padding:10px 12px">
            <div class="flex items-center gap-[10px]"><span style="font-weight:600">{p.name}</span><span class="ah-muted">{p.model || ''}</span><span class="chip {st[0]}" style="margin-left:auto">{st[1]}</span></div>
            <div class="flex items-center gap-[10px] ah-muted" style="margin:6px 0 4px"><span class="truncate">{p.job || (p.ready ? 'Ready for the next job' : '')}</span><span class="ah-num" style="margin-left:auto">{p.eta_minutes != null && p.busy ? `${Math.floor(p.eta_minutes / 60)}h ${p.eta_minutes % 60}m left` : ''}</span></div>
            <div class="ah-progress{st[0] === 'warn' ? ' ah-progress--attention' : ''}" role="progressbar" aria-label="Progress on {p.name}" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(p.progress || 0)}><i style="width:{p.busy ? Math.round(p.progress || 0) : 0}%"></i></div>
          </div>
        {:else}<p class="ah-muted">The farm reports no printers yet.</p>{/each}
      </div>
    {/if}
  </section>
  <section class="ah-glass ah-card">
    <div class="flex items-center gap-[10px]"><div class="ah-icon-tile"><Icon name="cart" /></div>
      <div style="flex:1;min-width:0"><h3 class="ah-widget-title">Open orders</h3><div class="ah-muted">{orders ? `${orders.length} from WooCommerce` : 'Loading...'}</div></div>
      <a class="ah-round-btn" href="/orders" aria-label="Open Orders"><Icon name="chevron-right" size={14} /></a></div>
    {#if orders?.length}
      <table class="grid"><thead><tr><th>Order</th><th>Items</th><th>Status</th></tr></thead><tbody>
        {#each orders.slice(0, 6) as o (o.number)}<tr><td>#{o.number}</td><td>{o.lines.map((l) => `${l.name}${l.qty > 1 ? ` x${l.qty}` : ''}`).join(', ')}</td><td><span class="chip {o.status === 'processing' ? 'warn' : ''}">{o.status}</span></td></tr>{/each}
      </tbody></table>
    {:else if orders}<p class="ah-muted">No open orders.</p>{/if}
  </section>
{/snippet}

{#if byLink}
  <div class="ah-dialog-back" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) byLink = false; }} onkeydown={(e) => { if (e.key === 'Escape') byLink = false; }}>
    <div class="ah-dialog" style="max-width:720px;position:relative" role="dialog" aria-modal="true" aria-label="Add a model by link">
      <button class="btn sm ghost" style="position:absolute;right:14px;top:14px;z-index:1" onclick={() => (byLink = false)} aria-label="Close"><Glyph name="x" /></button>
      <SourceImport />
    </div>
  </div>
{/if}

<div class="flex flex-wrap gap-[10px] items-center">
  <div class="ah-seg" role="group" aria-label="Which models">
    {#each [['all', 'All'], ['live', 'Live'], ['hidden', 'Not live'], ['supplies', 'Out of supplies'], ['archived', 'Archived']] as [v, l]}
      <button type="button" aria-pressed={view === v} onclick={() => { view = v; refilter(); }}>{l}{#if v === 'supplies' && stats?.live_out_of_supplies?.length} ({stats.live_out_of_supplies.length}){/if}</button>
    {/each}
  </div>
  <select class="input" style="width:200px" bind:value={category} onchange={refilter} aria-label="Category">
    <option value="">All categories</option>
    {#each cats as c}<option value={c.id}>{c.name} ({c.count})</option>{/each}
  </select>
  <input class="input" style="width:240px" placeholder="Search name, code, SKU" aria-label="Search models" bind:value={q} oninput={refilter} />
</div>

{#if selecting && selected.size}
  <div class="ah-banner ah-banner--live" style="flex-wrap:wrap">
    <b>{selected.size} selected</b>
    <button class="btn sm" onclick={bulkPush}>Push to store (draft)</button>
    <button class="btn sm" onclick={bulkSlice}>Slice for a printer...</button>
    <button class="btn sm primary" onclick={() => bulk(true)}>Go live</button>
    <button class="btn sm" onclick={() => bulk(false)}>Hide</button>
    {#if view === 'archived'}<button class="btn sm" onclick={() => bulkModels('restore')}>Restore</button>
    {:else}<button class="btn sm" onclick={() => bulkModels('archive')}>Archive</button>{/if}
    <button class="btn sm danger" onclick={() => bulkModels('delete')}>Delete...</button>
  </div>
{/if}

<section class="ah-glass ah-card ah-scroll" style="flex:1">
  <div class="flex items-center gap-[10px] flex-wrap">
    <span class="ah-card-title">{total} models</span>
    {#if stats}<span class="ah-muted">{stats.live} live on the store, {stats.on_woo} listed</span>{/if}
    <div class="ah-seg" style="margin-left:auto" role="group" aria-label="Sort">
      {#each [['recent', 'Recently added'], ['published', 'Designer date'], ['name', 'Name'], ['code', 'Code']] as [v, l]}
        <button type="button" aria-pressed={sort === v} onclick={() => { sort = v; refilter(); }}>{l}</button>
      {/each}
    </div>
    <label class="flex items-center gap-2 ah-muted"><span>Size</span><input type="range" min="160" max="460" step="20" bind:value={size} aria-label="Card size" /></label>
    <button class="btn sm" onclick={() => { selecting = !selecting; selected = new Set(); }}>{selecting ? 'Cancel' : 'Select'}</button>
  </div>
  <div class="grid gap-3" style="grid-template-columns: repeat(auto-fill, minmax({size}px, 1fr))">
    {#each items as m (m.id)}
      <a href={selecting ? undefined : `/models/${m.id}`} onclick={(e) => { if (selecting) { e.preventDefault(); toggleSel(m.id); } }}
         class="group panel overflow-hidden flex flex-col relative {selected.has(m.id) ? 'ring-2 ring-ah-accent' : ''}" style="padding:0">
        <div class="aspect-[4/5] overflow-hidden bg-ah-panel">
          {#if m.thumb}
            <img src={size > 300 ? m.large : m.thumb} alt={m.name} loading="lazy" class="w-full h-full object-cover transition duration-300 group-hover:scale-[1.03]" />
          {:else}
            <div class="w-full h-full grid place-items-center ah-muted">No photo yet</div>
          {/if}
        </div>
        {#if selecting}<div class="absolute top-2 right-2 w-6 h-6 rounded-md border-2 grid place-items-center bg-ah-solid-panel" style="border-color:var(--ah-text)">{#if selected.has(m.id)}<Icon name="check" size={14} />{/if}</div>{/if}
        <div class="flex flex-col gap-[6px]" style="padding:12px 14px">
          <div class="flex items-start gap-2">
            <span class="ah-card-title line-clamp-2" style="font-size:15px;flex:1;min-width:0">{m.name}</span>
            {#if m.live}<span class="chip ok">Live</span>{:else if m.on_woo}<span class="chip">Draft</span>{/if}
          </div>
          <div class="flex items-center gap-[10px] ah-muted"><span class="ah-mono" style="font-size:12px">{m.code}</span><span class="truncate">{m.category || 'Uncategorized'}</span>{#if m.price}<span class="ah-num" style="margin-left:auto;color:var(--ah-text)">{fmtMoney(m.price)}</span>{/if}</div>
          <div class="flex flex-wrap gap-1">
            {#if m.live && m.units === 0}<span class="chip bad">Out of supplies</span>{:else if m.units != null}<span class="chip {m.units === 0 ? 'bad' : m.units < 3 ? 'warn' : ''}">Can build {m.units}</span>{/if}
            {#if m.print_ready}<span class="chip ok">Print-ready</span>{/if}
            {#if !m.license_ok}<span class="chip {licenseChip(m.license_code).cls}">{licenseChip(m.license_code).label}</span>{:else if m.source_platform}<span class="chip">{({ makerworld: 'MakerWorld', printables: 'Printables', thingiverse: 'Thingiverse', creality: 'Creality' })[m.source_platform]}</span>{/if}
          </div>
        </div>
      </a>
    {/each}
  </div>
  <div bind:this={sentinel} class="h-10 grid place-items-center ah-muted" style="flex:none">{loading ? 'Loading...' : done && items.length > 12 ? 'That is everything.' : ''}</div>
  {#if !loading && !items.length}
    <div class="panel flex flex-col items-center gap-2" style="padding:32px">
      <div class="ah-card-title">No models yet</div>
      <p class="ah-muted">Import runs automatically from the folders assigned to your team, or add one now.</p>
      <a href="/new" class="btn primary"><Icon name="plus" size={16} />Add a model</a>
    </div>
  {/if}
</section>
</Page>
