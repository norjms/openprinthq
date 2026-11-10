<script>
  // A printer's named cameras: its own, the engine's external camera, and any
  // added by URL (a chamber camera, say). Each is a go2rtc stream with a health
  // state; added ones can be renamed, switched off or removed.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let { printerId } = $props();

  let cams = $state(null);      // null = loading
  let error = $state(null);
  let busy = $state(null);
  let adding = $state(false);
  let name = $state('Chamber');
  let url = $state('');
  let formErr = $state(null);

  async function load() {
    error = null;
    try {
      const r = await api.cameras();
      cams = (r?.streams || []).filter((c) => String(c.printer_id) === String(printerId));
    } catch (e) {
      cams = [];
      error = e?.message || 'Could not load the cameras.';
    }
  }
  onMount(load);

  async function toggle(c) {
    busy = c.stream_name;
    try {
      if (c.kind === 'extra') await api.updateCamera(c.id, { enabled: !c.enabled });
      else await api.setOwnCamera(printerId, c.kind, { enabled: !c.enabled });
      await load();
    } catch (e) { error = e?.message || 'Could not change the camera.'; }
    finally { busy = null; }
  }

  async function remove(c) {
    busy = c.stream_name;
    try { await api.deleteCamera(c.id); await load(); }
    catch (e) { error = e?.message || 'Could not remove the camera.'; }
    finally { busy = null; }
  }

  async function add(e) {
    e.preventDefault();
    formErr = null;
    if (!name.trim()) { formErr = 'Give the camera a name.'; return; }
    if (!/^(rtsps?|rtmp|https?):\/\//i.test(url.trim())) { formErr = 'The address starts with rtsp://, rtsps://, http:// or https://.'; return; }
    busy = 'add';
    try {
      await api.addCamera(printerId, name.trim(), url.trim());
      adding = false; url = ''; name = 'Chamber';
      await load();
    } catch (err) { formErr = err?.message || 'Could not add the camera.'; }
    finally { busy = null; }
  }

  const tone = (s) => (s === 'ONLINE' ? 'ok' : s === 'OFFLINE' ? 'danger' : '');
  const label = (s) => ({ ONLINE: 'online', OFFLINE: 'offline', DISABLED: 'off', UNKNOWN: 'not checked yet' }[s] || s);
  const kindLabel = (k) => ({ builtin: 'printer', engine: 'external', extra: 'added' }[k] || k);
</script>

<div class="card cams">
  <div class="chead">
    <span class="ctitle">Cameras</span>
    <span class="sp"></span>
    {#if !adding}<button class="btn btn-ghost btn-sm" type="button" onclick={() => (adding = true)}>Add a camera</button>{/if}
  </div>

  {#if cams === null}
    <p class="muted pad">Loading…</p>
  {:else}
    {#if cams.length === 0}
      <p class="muted pad">No cameras for this printer yet. Add one by its stream address, such as a chamber camera.</p>
    {:else}
      <ul class="list">
        {#each cams as c (c.stream_name)}
          <li>
            <div class="who">
              <span class="nm">{c.camera_name}</span>
              <span class="src mono">{kindLabel(c.kind)}{c.source ? ' · ' + c.source : ''}</span>
            </div>
            <span class="chip {tone(c.state)}">{label(c.state)}</span>
            <button class="btn btn-ghost btn-sm" type="button" disabled={busy === c.stream_name} onclick={() => toggle(c)}>
              {c.enabled ? 'Switch off' : 'Switch on'}
            </button>
            {#if c.kind === 'extra'}
              <button class="btn btn-ghost btn-sm danger" type="button" disabled={busy === c.stream_name} onclick={() => remove(c)}>Remove</button>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}

    {#if adding}
      <form class="add" onsubmit={add}>
        <label>Name <input bind:value={name} maxlength="60" /></label>
        <label class="grow">Stream address
          <input bind:value={url} placeholder="rtsp://camera.local:554/stream1" autocomplete="off" spellcheck="false" />
        </label>
        <button class="btn btn-primary btn-sm" type="submit" disabled={busy === 'add'}>{busy === 'add' ? 'Adding…' : 'Add'}</button>
        <button class="btn btn-ghost btn-sm" type="button" onclick={() => { adding = false; formErr = null; }}>Cancel</button>
        {#if formErr}<p class="err full">{formErr}</p>{/if}
      </form>
    {/if}
  {/if}
  {#if error}<p class="err pad">{error}</p>{/if}
</div>

<style>
  .cams { padding: 0; overflow: hidden; margin-top: 0.8rem; }
  .chead { display: flex; align-items: center; gap: 0.6rem; padding: 0.55rem 0.75rem; border-bottom: 1px solid var(--ophq-border-soft); }
  .ctitle { font-size: 0.9rem; color: var(--ophq-text-2); font-weight: 600; }
  .sp { flex: 1; }
  .pad { padding: 0.6rem 0.75rem; margin: 0; font-size: 0.85rem; }
  .list { list-style: none; margin: 0; padding: 0; }
  .list li { display: flex; align-items: center; gap: 0.6rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--ophq-border-soft); }
  .list li:last-child { border-bottom: 0; }
  .who { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .nm { font-weight: 600; font-size: 0.88rem; }
  .src { font-size: 0.72rem; color: var(--ophq-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chip.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .chip.danger { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .danger { color: var(--ophq-danger); }
  .add { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.6rem; padding: 0.7rem 0.75rem; border-top: 1px solid var(--ophq-border-soft); }
  .add label { display: flex; flex-direction: column; gap: 0.2rem; font-size: 0.78rem; color: var(--ophq-text-2); }
  .add .grow { flex: 1; min-width: 220px; }
  .add input { font: inherit; font-size: 0.85rem; }
  .err { color: var(--ophq-danger); font-size: 0.82rem; }
  .full { width: 100%; margin: 0; }
</style>
