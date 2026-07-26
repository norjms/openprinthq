<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';

  let loading = $state(true);
  let error = $state(null);
  let projects = $state([]);

  let cur = $state('USD');
  const CUR_SYM = { USD: '$', EUR: '€', GBP: '£', CAD: '$', AUD: '$', NZD: '$', JPY: '¥', CNY: '¥', CHF: 'CHF ', SEK: 'kr ', NOK: 'kr ', DKK: 'kr ', PLN: 'zł ', INR: '₹', ZAR: 'R ', BRL: 'R$ ', MXN: '$' };
  const sym = $derived(CUR_SYM[cur] || (cur + ' '));
  const money = (v) => sym + (Number(v) || 0).toFixed(2);

  // create form
  let creating = $state(false);
  let nf = $state({ name: '', description: '', color: '#7c6cff', target_count: '' });
  let saving = $state(false);
  let cErr = $state(null);

  // detail
  let openId = $state(null);
  let detail = $state(null);       // { project, archives, queue }
  let detailLoading = $state(false);

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.projects || d?.items || []);
    return arr.map((p) => ({
      id: p.id, name: p.name, description: p.description, color: p.color || '#7c6cff',
      status: (p.status || 'active').toString(),
      target: p.target_count ?? p.target_parts_count ?? null,
      archives: p.archive_count ?? 0, total: p.total_items ?? 0, completed: p.completed_count ?? 0
    }));
  }
  async function load() {
    loading = true; error = null;
    try {
      const eng = await api.engineSettings().catch(() => null);
      if (eng?.currency) cur = eng.currency;
      projects = norm(await api.projects());
    }
    catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(load);

  async function create() {
    if (!nf.name.trim()) { cErr = 'Name is required.'; return; }
    saving = true; cErr = null;
    try {
      const body = { name: nf.name.trim(), description: nf.description || null, color: nf.color };
      if (nf.target_count) body.target_count = Number(nf.target_count);
      await api.createProject(body);
      creating = false; nf = { name: '', description: '', color: '#7c6cff', target_count: '' };
      await load();
    } catch (e) { cErr = e.message || 'could not create project'; }
    finally { saving = false; }
  }

  async function toggleOpen(id) {
    if (openId === id) { openId = null; detail = null; bom = []; return; }
    openId = id; detail = null; bom = []; detailLoading = true;
    try {
      const [archives, queue, bomList] = await Promise.all([
        api.projectArchives(id).catch(() => []),
        api.projectQueue(id).catch(() => []),
        api.projectBom(id).catch(() => [])
      ]);
      detail = {
        archives: Array.isArray(archives) ? archives : (archives?.items || archives?.archives || []),
        queue: Array.isArray(queue) ? queue : (queue?.items || queue?.queue || [])
      };
      bom = normBom(bomList);
    } catch { detail = { archives: [], queue: [] }; }
    finally { detailLoading = false; }
  }

  // ---- BOM / bill of materials (#22) ----
  let bom = $state([]);
  let bomForm = $state({ name: '', quantity_needed: 1, unit_price: '' });
  let bomBusy = $state(false);
  function normBom(d) {
    const arr = Array.isArray(d) ? d : (d?.items || d?.bom || []);
    return arr.map((b) => ({
      id: b.id, name: b.name, needed: b.quantity_needed ?? 1, acquired: b.quantity_acquired ?? 0,
      unitPrice: b.unit_price ?? null, url: b.sourcing_url || '', remarks: b.remarks || '',
      complete: b.is_complete ?? ((b.quantity_acquired ?? 0) >= (b.quantity_needed ?? 1))
    }));
  }
  const bomRollup = $derived.by(() => {
    let done = 0, cost = 0;
    for (const b of bom) { if (b.complete || b.acquired >= b.needed) done++; cost += (b.unitPrice || 0) * b.needed; }
    return { total: bom.length, done, cost };
  });
  async function addBom() {
    if (!bomForm.name.trim() || bomBusy) return;
    bomBusy = true;
    try {
      const body = { name: bomForm.name.trim(), quantity_needed: Math.max(1, Number(bomForm.quantity_needed) || 1) };
      if (bomForm.unit_price !== '') body.unit_price = Number(bomForm.unit_price);
      await api.addBomItem(openId, body);
      bomForm = { name: '', quantity_needed: 1, unit_price: '' };
      bom = normBom(await api.projectBom(openId).catch(() => []));
    } catch { /* ignore */ } finally { bomBusy = false; }
  }
  async function setAcquired(item, delta) {
    const next = Math.max(0, item.acquired + delta);
    try { await api.updateBomItem(openId, item.id, { quantity_acquired: next }); bom = normBom(await api.projectBom(openId).catch(() => [])); }
    catch { /* ignore */ }
  }
  async function delBom(item) {
    try { await api.deleteBomItem(openId, item.id); bom = bom.filter((b) => b.id !== item.id); }
    catch { /* ignore */ }
  }

  const pct = (p) => (p.target && p.target > 0 ? Math.min(100, Math.round((p.completed / p.target) * 100)) : null);
  function stone(s) {
    s = (s || '').toLowerCase();
    if (/done|complete|finish/.test(s)) return 'ok';
    if (/hold|pause|archiv/.test(s)) return 'accent';
    return 'primary';
  }
</script>

<PageTitle page="Projects" />

<div class="head">
  <div><h1>Projects</h1><p class="muted">Group prints into jobs — track parts, progress and cost per project.</p></div>
  <button class="btn btn-primary btn-sm" onclick={() => (creating = !creating)}>{creating ? 'Close' : '+ New project'}</button>
</div>

{#if creating}
  <div class="card card-pad form">
    <div class="frow">
      <div class="fld grow"><label for="pn">Name</label><input id="pn" class="input" bind:value={nf.name} placeholder="Client order #1024" /></div>
      <div class="fld"><label for="pt">Target parts</label><input id="pt" class="input sm" type="number" min="0" bind:value={nf.target_count} placeholder="—" /></div>
      <div class="fld"><label for="pc">Colour</label><input id="pc" class="input color" type="color" bind:value={nf.color} /></div>
    </div>
    <div class="fld"><label for="pd">Description</label><input id="pd" class="input" bind:value={nf.description} placeholder="Optional" /></div>
    {#if cErr}<p class="err">{cErr}</p>{/if}
    <div class="flex gap"><button class="btn btn-primary btn-sm" onclick={create} disabled={saving}>{saving ? 'Creating…' : 'Create project'}</button></div>
  </div>
{/if}

{#if loading}
  <div class="card card-pad muted">Loading projects…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else if projects.length === 0}
  <div class="card card-pad empty">
    <div class="ic">📁</div><h3>No projects yet</h3>
    <p class="muted">Create a project to group related prints — a client order, a multi-part assembly, a production run — and track its parts and progress.</p>
  </div>
{:else}
  <div class="plist">
    {#each projects as p (p.id)}
      <div class="card proj">
        <button class="phead" onclick={() => toggleOpen(p.id)}>
          <span class="swatch" style="background:{p.color}"></span>
          <span class="pmeta">
            <span class="pn">{p.name}</span>
            {#if p.description}<span class="muted pd">{p.description}</span>{/if}
          </span>
          <span class="pstats mono">
            <span class="chip {stone(p.status)}">{p.status}</span>
            <span class="muted">{p.archives} job{p.archives === 1 ? '' : 's'}</span>
            {#if p.target}<span class="muted">{p.completed}/{p.target}</span>{/if}
          </span>
        </button>
        {#if pct(p) != null}
          <div class="bar"><div class="fill" style="width:{pct(p)}%"></div></div>
        {/if}
        {#if openId === p.id}
          <div class="pdetail">
            {#if detailLoading}
              <p class="muted tiny">Loading…</p>
            {:else if detail}
              <div class="dcol">
                <span class="dh">Prints ({detail.archives.length})</span>
                {#if detail.archives.length}{#each detail.archives.slice(0, 8) as a}<div class="drow">{a.print_name || a.name || a.filename || 'Print'}<span class="muted mono">{(a.status || '').toString()}</span></div>{/each}{:else}<p class="muted tiny">No prints linked yet.</p>{/if}
              </div>
              <div class="dcol">
                <span class="dh">Queued ({detail.queue.length})</span>
                {#if detail.queue.length}{#each detail.queue.slice(0, 8) as q}<div class="drow">{q.library_file_name || q.name || 'Job'}<span class="muted mono">{(q.status || '').toString()}</span></div>{/each}{:else}<p class="muted tiny">Nothing queued.</p>{/if}
              </div>
            {/if}
          </div>
          {#if !detailLoading}
            <div class="bom">
              <div class="bomh">
                <span class="dh">Bill of materials{#if bomRollup.total} — {bomRollup.done}/{bomRollup.total} complete{#if bomRollup.cost > 0} · {money(bomRollup.cost)}{/if}{/if}</span>
              </div>
              {#if bom.length}
                <div class="bomlist">
                  {#each bom as b (b.id)}
                    <div class="bomrow" class:done={b.complete || b.acquired >= b.needed}>
                      <span class="bn">{b.name}{#if b.url} <a href={b.url} target="_blank" rel="noopener" class="src" aria-label="Open source link">↗</a>{/if}</span>
                      <span class="qty">
                        <button class="stp" onclick={() => setAcquired(b, -1)} disabled={b.acquired <= 0} aria-label="decrease">−</button>
                        <span class="qn mono">{b.acquired}/{b.needed}</span>
                        <button class="stp" onclick={() => setAcquired(b, 1)} aria-label="increase">+</button>
                      </span>
                      {#if b.unitPrice != null}<span class="up muted mono">{money(b.unitPrice)}</span>{:else}<span class="up"></span>{/if}
                      <button class="del" onclick={() => delBom(b)} aria-label="remove">✕</button>
                    </div>
                  {/each}
                </div>
              {:else}
                <p class="muted tiny">No parts listed. Add the components this assembly needs.</p>
              {/if}
              <div class="bomadd">
                <input class="input" placeholder="Part / component name" bind:value={bomForm.name} onkeydown={(e) => e.key === 'Enter' && addBom()} />
                <input class="input qi" type="number" min="1" bind:value={bomForm.quantity_needed} title="Quantity needed" />
                <input class="input pi" type="number" min="0" step="0.01" placeholder="unit $" bind:value={bomForm.unit_price} title="Unit price" />
                <button class="btn btn-ghost btn-sm" onclick={addBom} disabled={bomBusy || !bomForm.name.trim()}>Add part</button>
              </div>
            </div>
          {/if}
        {/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; gap: 1rem; }
  .head h1 { margin: 0; }
  .form { margin-bottom: 1.2rem; display: flex; flex-direction: column; gap: 0.7rem; }
  .frow { display: flex; gap: 0.8rem; flex-wrap: wrap; }
  .fld { display: flex; flex-direction: column; gap: 0.3rem; }
  .fld.grow { flex: 1; min-width: 200px; }
  .fld label { font-size: 0.8rem; color: var(--ophq-text-2); }
  .input.sm { max-width: 110px; } .input.color { max-width: 60px; padding: 0.2rem; height: 38px; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .empty { text-align: center; padding: 2.6rem; } .empty .ic { font-size: 1.8rem; }
  .empty p { max-width: 48ch; margin: 0.6rem auto 0; }
  .plist { display: flex; flex-direction: column; gap: 0.8rem; }
  .proj { overflow: hidden; }
  .phead { width: 100%; display: flex; align-items: center; gap: 0.9rem; padding: 0.9rem 1.1rem; background: none; border: none; cursor: pointer; text-align: left; color: var(--ophq-text); }
  .phead:hover { background: var(--ophq-surface-2); }
  .swatch { width: 14px; height: 14px; border-radius: 4px; flex-shrink: 0; }
  .pmeta { display: flex; flex-direction: column; gap: 0.1rem; flex: 1; }
  .pn { font-weight: 600; }
  .pd { font-size: 0.82rem; }
  .pstats { display: flex; align-items: center; gap: 0.8rem; font-size: 0.8rem; }
  .bar { height: 4px; background: var(--ophq-bg-2); }
  .fill { height: 100%; background: linear-gradient(90deg, var(--ophq-primary), var(--ophq-primary-2)); }
  .pdetail { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; padding: 0.9rem 1.1rem 1.1rem; border-top: 1px solid var(--ophq-border); }
  .dh { display: block; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); margin-bottom: 0.4rem; }
  .drow { display: flex; justify-content: space-between; gap: 0.6rem; padding: 0.3rem 0; border-bottom: 1px solid var(--ophq-border-soft); font-size: 0.86rem; }
  .tiny { font-size: 0.8rem; }
  .chip.primary { color: var(--ophq-primary-2); border-color: rgba(124,108,255,0.35); background: var(--ophq-primary-dim); }
  .chip.accent { color: var(--ophq-accent); border-color: rgba(255,176,32,0.3); background: rgba(255,176,32,0.08); }
  .chip.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  @media (max-width: 720px) { .pdetail { grid-template-columns: 1fr; } .pstats { display: none; } }

  .bom { padding: 0.4rem 1.1rem 1.1rem; border-top: 1px solid var(--ophq-border); }
  .bomh { margin: 0.6rem 0 0.5rem; }
  .bomlist { display: flex; flex-direction: column; gap: 0.3rem; margin-bottom: 0.7rem; }
  .bomrow { display: grid; grid-template-columns: 1fr auto auto auto; align-items: center; gap: 0.7rem; padding: 0.35rem 0.5rem; border: 1px solid var(--ophq-border-soft); border-radius: var(--radius-sm); background: var(--ophq-surface); font-size: 0.86rem; }
  .bomrow.done { border-color: rgba(53,196,107,0.35); background: rgba(53,196,107,0.06); }
  .bn { font-weight: 500; }
  .src { color: var(--ophq-primary-2); text-decoration: none; }
  .qty { display: flex; align-items: center; gap: 0.35rem; }
  .stp { width: 22px; height: 22px; border-radius: 5px; border: 1px solid var(--ophq-border); background: var(--ophq-bg-2); color: var(--ophq-text); cursor: pointer; line-height: 1; }
  .stp:disabled { opacity: 0.4; cursor: default; }
  .qn { min-width: 40px; text-align: center; font-size: 0.82rem; }
  .up { font-size: 0.8rem; min-width: 48px; text-align: right; }
  .del { border: none; background: none; color: var(--ophq-muted); cursor: pointer; font-size: 0.8rem; }
  .del:hover { color: var(--ophq-danger); }
  .bomadd { display: flex; gap: 0.4rem; align-items: center; }
  .bomadd .input { flex: 1; }
  .bomadd .qi { max-width: 70px; flex: none; }
  .bomadd .pi { max-width: 90px; flex: none; }
</style>
