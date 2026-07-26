<script>
  // OpenPrintHQ — API keys & webhooks (#15).
  // Create/scope/revoke API keys (engine /api-keys) that authenticate the
  // /webhook/* automation endpoints. The full key is shown exactly once at
  // creation. Docs the available webhook endpoints.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  const SCOPES = [
    { key: 'can_read_status', label: 'Read printer status', def: true },
    { key: 'can_queue', label: 'Add to / manage queue', def: true },
    { key: 'can_control_printer', label: 'Start / stop / cancel prints', def: false, danger: true },
    { key: 'can_manage_library', label: 'Manage files', def: true },
    { key: 'can_manage_inventory', label: 'Manage filament', def: true },
    { key: 'can_manage_maintenance', label: 'Manage maintenance', def: true },
    { key: 'can_manage_archives', label: 'Manage archives', def: true },
    { key: 'can_manage_projects', label: 'Manage projects', def: true },
    { key: 'can_access_cloud', label: 'Cloud access', def: false },
    { key: 'can_update_energy_cost', label: 'Update energy cost', def: false }
  ];

  let keys = $state([]);
  let printers = $state([]);
  let loading = $state(true);
  let error = $state(null);

  let showForm = $state(false);
  let saving = $state(false);
  let formErr = $state(null);
  let created = $state(null);   // { name, key } shown once
  let copied = $state(false);
  let confirmDel = $state(null);
  let form = $state(newForm());
  function newForm() {
    const f = { name: '', printer_ids: [], expires_at: '' };
    for (const s of SCOPES) f[s.key] = s.def;
    return f;
  }

  async function load() {
    loading = true; error = null;
    try {
      const [k, pl] = await Promise.all([api.apiKeys(), api.printers().catch(() => [])]);
      keys = Array.isArray(k) ? k : (k?.keys || k?.items || []);
      printers = (Array.isArray(pl) ? pl : (pl?.printers || pl?.items || [])).map((p) => ({ id: p.id ?? p.printer_id, name: p.name || ('Printer ' + p.id) }));
    } catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(load);

  function openForm() { form = newForm(); formErr = null; created = null; showForm = true; }
  function togglePrinter(id) {
    const s = new Set(form.printer_ids);
    if (s.has(id)) s.delete(id); else s.add(id);
    form.printer_ids = [...s];
  }
  async function save() {
    if (!form.name.trim()) { formErr = 'Give the key a name.'; return; }
    saving = true; formErr = null;
    const body = { name: form.name.trim() };
    for (const s of SCOPES) body[s.key] = !!form[s.key];
    if (form.printer_ids.length) body.printer_ids = form.printer_ids;
    if (form.expires_at) body.expires_at = new Date(form.expires_at).toISOString();
    try {
      const r = await api.createApiKey(body);
      created = { name: r.name || body.name, key: r.key || r.api_key || r.full_key };
      showForm = false;
      await load();
    } catch (e) { formErr = e.message || 'could not create key'; }
    finally { saving = false; }
  }
  async function copyKey() {
    try { await navigator.clipboard.writeText(created.key); copied = true; setTimeout(() => (copied = false), 2000); } catch { /* ignore */ }
  }
  async function toggleEnabled(k) {
    try { await api.updateApiKey(k.id, { enabled: !k.enabled }); await load(); } catch { /* ignore */ }
  }
  async function del(k) {
    try { await api.deleteApiKey(k.id); confirmDel = null; await load(); } catch { /* ignore */ }
  }

  function scopeList(k) { return SCOPES.filter((s) => k[s.key]).map((s) => s.label); }
  function fmt(v) { if (!v) return null; const d = new Date(v); return isNaN(d) ? null : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }

  const HOOKS = [
    { m: 'POST', p: '/webhook/queue/add', d: 'Add a library file to the print queue' },
    { m: 'POST', p: '/webhook/printer/{id}/start', d: 'Start the next / a specified print' },
    { m: 'POST', p: '/webhook/printer/{id}/stop', d: 'Stop the current print' },
    { m: 'POST', p: '/webhook/printer/{id}/cancel', d: 'Cancel the current print' },
    { m: 'GET', p: '/webhook/printer/{id}/status', d: 'Read a printer’s live status' },
    { m: 'GET', p: '/webhook/queue', d: 'Read the print queue' }
  ];
  let showHooks = $state(false);
</script>

<div class="card card-pad akeys">
  <div class="ah">
    <div><span class="eyebrow">API keys &amp; webhooks</span><p class="muted">Scoped keys for automating your farm over the <code>/webhook</code> endpoints (CI, scripts, home automation).</p></div>
    <button class="btn btn-primary btn-sm" onclick={openForm}>+ New key</button>
  </div>

  {#if created}
    <div class="newkey">
      <div class="nk-h">Key created — copy it now, it won’t be shown again.</div>
      <div class="nk-row"><code class="nk-code mono">{created.key}</code><button class="btn btn-ghost btn-sm" onclick={copyKey}>{copied ? 'Copied ✓' : 'Copy'}</button></div>
      <button class="btn btn-ghost btn-xs dismiss" onclick={() => (created = null)}>Dismiss</button>
    </div>
  {/if}

  {#if loading}
    <p class="muted">Loading keys…</p>
  {:else if error === 'no-instance'}
    <p class="muted">Provision your instance first.</p>
  {:else if error}
    <p class="err">{error} · <button class="btn btn-ghost btn-xs" onclick={load}>Retry</button></p>
  {:else if keys.length === 0}
    <p class="muted none">No API keys yet. Create one to drive OpenPrintHQ from scripts or other apps.</p>
  {:else}
    <div class="klist">
      {#each keys as k (k.id)}
        <div class="krow" class:off={!k.enabled}>
          <div class="kmain">
            <div class="kname">{k.name} <code class="mono kp">{k.key_prefix}…</code>{#if !k.enabled}<span class="chip">disabled</span>{/if}</div>
            <div class="kscopes">{scopeList(k).slice(0, 4).join(' · ')}{#if scopeList(k).length > 4} +{scopeList(k).length - 4}{/if}{#if k.printer_ids?.length} · {k.printer_ids.length} printer(s){/if}</div>
            <div class="kmeta muted mono">created {fmt(k.created_at) || '—'}{#if k.last_used} · last used {fmt(k.last_used)}{:else} · never used{/if}{#if k.expires_at} · expires {fmt(k.expires_at)}{/if}</div>
          </div>
          <div class="kacts">
            <button class="btn btn-ghost btn-xs" onclick={() => toggleEnabled(k)}>{k.enabled ? 'Disable' : 'Enable'}</button>
            {#if confirmDel === k.id}
              <button class="btn btn-danger btn-xs" onclick={() => del(k)}>Revoke</button><button class="btn btn-ghost btn-xs" onclick={() => (confirmDel = null)}>✕</button>
            {:else}
              <button class="btn btn-ghost btn-xs" onclick={() => (confirmDel = k.id)}>Revoke</button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <button class="btn btn-ghost btn-sm hooks-toggle" onclick={() => (showHooks = !showHooks)}>{showHooks ? '▾' : '▸'} Webhook endpoints</button>
  {#if showHooks}
    <div class="hooks">
      <p class="muted small">Send the key as <code>Authorization: Bearer &lt;key&gt;</code>. Reachable on your LAN / via the local connector agent at the engine API; public-internet exposure is a planned follow-up.</p>
      <table class="htbl">
        <tbody>
          {#each HOOKS as h}
            <tr><td><span class="verb {h.m === 'GET' ? 'get' : 'post'}">{h.m}</span></td><td class="mono hp">{h.p}</td><td class="muted hd">{h.d}</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

{#if showForm}
  <div class="overlay" role="presentation" onclick={() => (showForm = false)}>
    <div class="dialog card" role="dialog" onclick={(e) => e.stopPropagation()}>
      <div class="dhead"><div><span class="eyebrow">New API key</span><h3>Create key</h3></div><button class="btn btn-ghost btn-sm" onclick={() => (showForm = false)}>✕</button></div>
      <div class="fld"><label for="kn">Name</label><input id="kn" class="input" bind:value={form.name} placeholder="Home Assistant, CI runner…" /></div>
      <span class="gl">Permissions</span>
      <div class="scopes">
        {#each SCOPES as s}
          <label class="scope"><input type="checkbox" bind:checked={form[s.key]} /><span class:danger={s.danger}>{s.label}</span></label>
        {/each}
      </div>
      {#if printers.length}
        <span class="gl">Restrict to printers <span class="muted">(optional — none = all)</span></span>
        <div class="prs">
          {#each printers as p (p.id)}
            <label class="pr"><input type="checkbox" checked={form.printer_ids.includes(p.id)} onchange={() => togglePrinter(p.id)} /><span>{p.name}</span></label>
          {/each}
        </div>
      {/if}
      <div class="fld"><label for="exp">Expires <span class="muted">(optional)</span></label><input id="exp" class="input" type="date" bind:value={form.expires_at} /></div>
      {#if formErr}<p class="err">{formErr}</p>{/if}
      <div class="flex gap dactions">
        <button class="btn btn-primary" onclick={save} disabled={saving}>{saving ? 'Creating…' : 'Create key'}</button>
        <button class="btn btn-ghost" onclick={() => (showForm = false)} disabled={saving}>Cancel</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .akeys { margin-top: 1.2rem; }
  .ah { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
  .ah p { margin: 0.3rem 0 0; font-size: 0.88rem; max-width: 62ch; }
  code { font-family: var(--font-mono); font-size: 0.9em; background: var(--ophq-bg-2); padding: 0.05rem 0.3rem; border-radius: 4px; }
  .newkey { margin: 1rem 0; padding: 0.9rem; border: 1px solid var(--ophq-success); border-radius: var(--radius-sm); background: rgba(53,196,107,0.07); }
  .nk-h { font-size: 0.86rem; color: var(--ophq-success); margin-bottom: 0.5rem; }
  .nk-row { display: flex; gap: 0.5rem; align-items: center; }
  .nk-code { flex: 1; overflow-x: auto; white-space: nowrap; padding: 0.4rem 0.6rem; background: var(--ophq-bg-2); border-radius: var(--radius-sm); }
  .dismiss { margin-top: 0.5rem; }
  .none { padding: 0.6rem 0; }
  .klist { display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.9rem; }
  .krow { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 0.6rem 0.8rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); }
  .krow.off { opacity: 0.6; }
  .kname { font-weight: 600; display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
  .kp { font-weight: 400; font-size: 0.8rem; color: var(--ophq-text-2); }
  .kscopes { font-size: 0.78rem; color: var(--ophq-text-2); margin-top: 0.15rem; }
  .kmeta { font-size: 0.72rem; margin-top: 0.15rem; }
  .kacts { display: flex; gap: 0.35rem; flex: none; }
  .hooks-toggle { margin-top: 1.1rem; }
  .hooks { margin-top: 0.6rem; border-top: 1px solid var(--ophq-border); padding-top: 0.8rem; }
  .small { font-size: 0.8rem; }
  .htbl { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 0.5rem; }
  .htbl td { padding: 0.28rem 0.5rem; border-bottom: 1px solid var(--ophq-border-soft); vertical-align: middle; }
  .verb { font-size: 0.68rem; font-weight: 700; padding: 0.08rem 0.4rem; border-radius: 4px; }
  .verb.get { color: #4ea1ff; background: rgba(78,161,255,0.12); }
  .verb.post { color: var(--ophq-success); background: rgba(53,196,107,0.12); }
  .hp { color: var(--ophq-text); }
  .hd { font-size: 0.78rem; }
  .chip { font-size: 0.68rem; padding: 0.05rem 0.4rem; border: 1px solid var(--ophq-border); border-radius: 999px; color: var(--ophq-muted); }

  .overlay { position: fixed; inset: 0; background: rgba(5,8,12,0.66); backdrop-filter: blur(3px); display: grid; place-items: center; z-index: 100; padding: 1.5rem; }
  .dialog { width: 100%; max-width: 460px; padding: 1.5rem; box-shadow: var(--shadow-glow); max-height: 88vh; overflow-y: auto; }
  .dhead { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1rem; }
  .dhead h3 { margin: 0.2rem 0 0; font-size: 1.15rem; }
  .fld { display: flex; flex-direction: column; gap: 0.3rem; margin-bottom: 0.4rem; }
  .fld label { font-size: 0.8rem; color: var(--ophq-text-2); }
  .gl { display: block; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); margin: 0.9rem 0 0.5rem; }
  .scopes { display: grid; grid-template-columns: 1fr 1fr; gap: 0.4rem 0.8rem; }
  .scope, .pr { display: flex; align-items: center; gap: 0.4rem; font-size: 0.84rem; color: var(--ophq-text-2); }
  .scope input, .pr input { width: auto; accent-color: var(--ophq-primary); }
  .scope .danger { color: var(--ophq-warning, #e0a533); }
  .prs { display: flex; flex-wrap: wrap; gap: 0.7rem; }
  .dactions { margin-top: 1.2rem; }
  .err { color: var(--ophq-danger); font-size: 0.86rem; }
</style>
