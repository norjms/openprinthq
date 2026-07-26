<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let projects = $state([]);

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
    try { projects = norm(await api.projects()); }
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
    if (openId === id) { openId = null; detail = null; return; }
    openId = id; detail = null; detailLoading = true;
    try {
      const [archives, queue] = await Promise.all([
        api.projectArchives(id).catch(() => []),
        api.projectQueue(id).catch(() => [])
      ]);
      detail = {
        archives: Array.isArray(archives) ? archives : (archives?.items || archives?.archives || []),
        queue: Array.isArray(queue) ? queue : (queue?.items || queue?.queue || [])
      };
    } catch { detail = { archives: [], queue: [] }; }
    finally { detailLoading = false; }
  }

  const pct = (p) => (p.target && p.target > 0 ? Math.min(100, Math.round((p.completed / p.target) * 100)) : null);
  function stone(s) {
    s = (s || '').toLowerCase();
    if (/done|complete|finish/.test(s)) return 'ok';
    if (/hold|pause|archiv/.test(s)) return 'accent';
    return 'primary';
  }
</script>

<svelte:head><title>Projects · OpenPrintHQ</title></svelte:head>

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
</style>
