<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let rows = $state([]);
  let loading = $state(true);
  let error = $state(null);
  let busy = $state(false);

  // add/edit form
  let fVendor = $state('bambu');
  let fCode = $state('');
  let fName = $state('');
  let fLocked = $state(false);
  let formError = $state(null);

  const VENDORS = ['bambu', 'klipper', 'prusalink', 'octoprint', 'duet', 'flashforge', 'mks', 'snapmaker'];

  async function load() {
    loading = true; error = null;
    try { const r = await api.adminModelNames(); rows = r.model_names || []; }
    catch (e) { error = e.message || 'could not load printer names'; }
    finally { loading = false; }
  }
  onMount(load);

  async function save() {
    formError = null;
    if (!fCode.trim()) { formError = 'Model code is required'; return; }
    if (!fName.trim()) { formError = 'Friendly name is required'; return; }
    busy = true;
    try {
      await api.saveModelName({ vendor: fVendor, code: fCode.trim(), friendly_name: fName.trim(), locked: fLocked });
      fCode = ''; fName = ''; fLocked = false;
      await load();
    } catch (e) { formError = e.message || 'could not save'; }
    finally { busy = false; }
  }
  async function toggleLock(r) {
    busy = true;
    try { await api.lockModelName(r.vendor, r.code, !r.locked); await load(); }
    catch (e) { error = e.message || 'could not change lock'; }
    finally { busy = false; }
  }
  async function edit(r) { fVendor = r.vendor; fCode = r.code; fName = r.friendly_name; fLocked = r.locked; }
  let confirmDel = $state(null);
  async function del(r) {
    busy = true;
    try { await api.deleteModelName(r.vendor, r.code); confirmDel = null; await load(); }
    catch (e) { error = e.message || 'could not delete'; }
    finally { busy = false; }
  }
  function fmt(v) { if (!v) return ''; const d = new Date(v); return isNaN(d) ? '' : d.toLocaleString(); }
</script>

<div class="card card-pad">
  <span class="eyebrow">Printer names</span>
  <p class="muted">Printers announce an internal model code (e.g. Bambu's <code>O1D</code>) rather than a marketing name (<code>H2D</code>). OpenPrintHQ learns the friendly name the first time someone types it when adding a printer, and shows it everywhere after. Lock a mapping to pin it — locked names can't be overwritten by what users type. Mappings are platform-wide.</p>

  <div class="addform">
    <select class="input v" bind:value={fVendor}>
      {#each VENDORS as v}<option value={v}>{v}</option>{/each}
    </select>
    <input class="input" placeholder="Model code (e.g. O1D)" bind:value={fCode} />
    <input class="input" placeholder="Friendly name (e.g. H2D)" bind:value={fName} />
    <label class="lockchk"><input type="checkbox" bind:checked={fLocked} /> Lock</label>
    <button class="btn btn-primary btn-sm" onclick={save} disabled={busy}>{busy ? 'Saving…' : 'Save mapping'}</button>
  </div>
  {#if formError}<p class="err" role="alert">{formError}</p>{/if}

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if rows.length === 0}
    <p class="muted">No mappings yet — they'll appear here as printers are named.</p>
  {:else}
    <table class="names">
      <thead><tr><th>Vendor</th><th>Code</th><th>Friendly name</th><th>Status</th><th></th></tr></thead>
      <tbody>
        {#each rows as r (r.vendor + ':' + r.code)}
          <tr>
            <td class="mono muted">{r.vendor}</td>
            <td class="mono">{r.code}</td>
            <td>{r.friendly_name}</td>
            <td>{#if r.locked}<span class="lock" title="Pinned by an admin; user input can't overwrite it.">🔒 locked</span>{:else}<span class="lock open" title="Users can update this by naming the model on add.">🔓 unlocked</span>{/if}</td>
            <td class="acts">
              <button class="btn btn-ghost btn-xs" onclick={() => toggleLock(r)} disabled={busy}>{r.locked ? 'Unlock' : 'Lock'}</button>
              <button class="btn btn-ghost btn-xs" onclick={() => edit(r)}>Edit</button>
              {#if confirmDel === r.vendor + ':' + r.code}
                <button class="btn btn-danger btn-xs" onclick={() => del(r)} disabled={busy}>Delete?</button>
                <button class="btn btn-ghost btn-xs" onclick={() => (confirmDel = null)}>✕</button>
              {:else}
                <button class="btn btn-ghost btn-xs" onclick={() => (confirmDel = r.vendor + ':' + r.code)}>Delete</button>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .addform { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; margin: 0.6rem 0; }
  .addform .v { max-width: 8rem; }
  .lockchk { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.85rem; color: var(--ophq-text-2); }
  .names { width: 100%; border-collapse: collapse; margin-top: 0.6rem; font-size: 0.9rem; }
  .names th { text-align: left; color: var(--ophq-muted); font-weight: 600; font-size: 0.78rem; padding: 0.3rem 0.5rem; border-bottom: 1px solid var(--ophq-border); }
  .names td { padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--ophq-border-soft); }
  .acts { display: flex; gap: 0.3rem; justify-content: flex-end; }
  .lock { font-size: 0.68rem; color: var(--ophq-success); border: 1px solid rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); padding: 0.05rem 0.4rem; border-radius: 999px; }
  .lock.open { color: var(--ophq-muted); border-color: var(--ophq-border); background: transparent; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; }
</style>
