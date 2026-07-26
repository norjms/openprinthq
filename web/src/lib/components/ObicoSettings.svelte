<script>
  // OpenPrintHQ — Obico AI print-failure (spaghetti) detection config.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let cfg = $state({ obico_enabled: false, obico_ml_url: '', obico_sensitivity: 'medium', obico_action: 'notify', obico_poll_interval: 10, obico_enabled_printers: '' });
  let printers = $state([]);
  let status = $state(null);
  let busy = $state(false);
  let testing = $state(false);
  let msg = $state(null);

  onMount(async () => {
    try {
      const s = await api.engineSettings().catch(() => null);
      if (s) for (const k of Object.keys(cfg)) if (s[k] !== undefined && s[k] !== null) cfg[k] = s[k];
      const pl = await api.printers().catch(() => []);
      printers = (Array.isArray(pl) ? pl : (pl?.printers || pl?.items || [])).map((p) => ({ id: p.id ?? p.printer_id, name: p.name || ('Printer ' + p.id) }));
      status = await api.obicoStatus().catch(() => null);
    } catch { /* no instance */ }
  });

  const enabledSet = $derived(new Set(String(cfg.obico_enabled_printers || '').split(',').map((x) => x.trim()).filter(Boolean)));
  function togglePrinter(id) {
    const set = new Set(enabledSet);
    const k = String(id);
    if (set.has(k)) set.delete(k); else set.add(k);
    cfg.obico_enabled_printers = [...set].join(',');
  }
  async function save() {
    busy = true; msg = null;
    try {
      await api.updateEngineSettings({
        obico_enabled: cfg.obico_enabled, obico_ml_url: cfg.obico_ml_url,
        obico_sensitivity: cfg.obico_sensitivity, obico_action: cfg.obico_action,
        obico_poll_interval: Number(cfg.obico_poll_interval) || 10,
        obico_enabled_printers: cfg.obico_enabled_printers
      });
      status = await api.obicoStatus().catch(() => status);
      msg = { kind: 'ok', text: 'Saved.' };
    } catch (e) { msg = { kind: 'err', text: e.message || 'could not save' }; }
    finally { busy = false; }
  }
  async function test() {
    testing = true; msg = null;
    try { const r = await api.obicoTest(); msg = { kind: r?.ok === false ? 'err' : 'ok', text: r?.message || 'Connection ok.' }; }
    catch (e) { msg = { kind: 'err', text: e.message || 'test failed' }; }
    finally { testing = false; }
  }
</script>

<div class="card card-pad obico">
  <div class="oh">
    <span class="eyebrow">AI failure detection (Obico)</span>
    {#if status}<span class="chip {status.is_running && cfg.obico_enabled ? 'ok' : ''}">{cfg.obico_enabled ? (status.is_running ? 'active' : 'idle') : 'off'}</span>{/if}
  </div>
  <p class="muted">Watches the camera for spaghetti / detachment and can pause or alert. Point it at a self-hosted <a href="https://obico.io" target="_blank" rel="noopener">Obico</a> ML server (or the community server).</p>

  <label class="opt"><input type="checkbox" bind:checked={cfg.obico_enabled} /><span>Enable AI failure detection</span></label>

  <div class="grid2">
    <div class="fld"><label for="ml">ML server URL</label><input id="ml" class="input" bind:value={cfg.obico_ml_url} placeholder="http://obico-ml.lan:3333/p/" /></div>
    <div class="fld"><label for="sn">Sensitivity</label>
      <select id="sn" class="input" bind:value={cfg.obico_sensitivity}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select>
    </div>
    <div class="fld"><label for="ac">On detection</label>
      <select id="ac" class="input" bind:value={cfg.obico_action}><option value="notify">Notify only</option><option value="pause">Pause the print</option></select>
    </div>
    <div class="fld"><label for="pi">Poll interval (s)</label><input id="pi" class="input" type="number" min="3" max="120" bind:value={cfg.obico_poll_interval} /></div>
  </div>

  {#if printers.length}
    <span class="evh">Monitor printers</span>
    <div class="prs">
      {#each printers as p (p.id)}
        <label class="pr"><input type="checkbox" checked={enabledSet.has(String(p.id))} onchange={() => togglePrinter(p.id)} /><span>{p.name}</span></label>
      {/each}
    </div>
  {/if}

  <div class="flex gap acts">
    <button class="btn btn-primary btn-sm" onclick={save} disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
    <button class="btn btn-ghost btn-sm" onclick={test} disabled={testing || !cfg.obico_ml_url}>{testing ? 'Testing…' : 'Test connection'}</button>
  </div>
  {#if msg}<p class={msg.kind === 'ok' ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}
</div>

<style>
  .obico { margin-top: 1.2rem; }
  .oh { display: flex; align-items: center; justify-content: space-between; }
  .obico > p { margin: 0.3rem 0 0.9rem; font-size: 0.9rem; max-width: 72ch; }
  .opt { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; color: var(--ophq-text-2); margin-bottom: 0.9rem; }
  .opt input, .pr input { width: auto; accent-color: var(--ophq-primary); }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; }
  .fld { display: flex; flex-direction: column; gap: 0.3rem; }
  .fld label { font-size: 0.8rem; color: var(--ophq-text-2); }
  .evh { display: block; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); margin: 0.9rem 0 0.4rem; }
  .prs { display: flex; flex-wrap: wrap; gap: 0.8rem; }
  .pr { display: flex; align-items: center; gap: 0.4rem; font-size: 0.85rem; color: var(--ophq-text-2); }
  .acts { margin-top: 1rem; }
  .chip.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .ok-msg { color: var(--ophq-success); font-size: 0.88rem; margin: 0.7rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0.7rem 0 0; }
  @media (max-width: 700px) { .grid2 { grid-template-columns: 1fr; } }
</style>
