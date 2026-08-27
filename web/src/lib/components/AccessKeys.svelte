<script>
  // Durable access keys, used by the browser extension and by scripts.
  //
  // The secret is shown exactly once, at creation, and never again: the listing
  // endpoint deliberately returns only a prefix. That is worth saying in the UI
  // rather than leaving the user to discover it by reloading the page.
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let keys = $state([]);
  let label = $state('Browser extension');
  let expiresInDays = $state(0);
  let busy = $state(false);
  let err = $state(null);
  let fresh = $state(null);   // the one-time secret, cleared on dismiss
  let copied = $state(false);

  async function load() {
    try { keys = await api.accessKeys(); err = null; }
    catch (e) { err = e.message; }
  }
  onMount(load);

  async function mint() {
    busy = true; err = null;
    try {
      fresh = await api.createAccessKey({ label, expiresInDays: Number(expiresInDays) || 0 });
      copied = false;
      await load();
    } catch (e) { err = e.message; }
    finally { busy = false; }
  }

  async function revoke(id) {
    busy = true; err = null;
    try { await api.revokeAccessKey(id); await load(); }
    catch (e) { err = e.message; }
    finally { busy = false; }
  }

  async function copyKey() {
    try { await navigator.clipboard.writeText(fresh.token); copied = true; }
    catch { copied = false; }
  }

  const fmt = (d) => (d ? new Date(d).toLocaleDateString() : '\u2014');
</script>

<div class="card card-pad">
  <span class="eyebrow">Browser extension &amp; scripts</span>
  <p class="muted">
    An access key lets the OpenPrintHQ browser extension put downloaded models
    straight into this Files tab. It can add files and read what it added.
    It cannot control a printer.
  </p>

  {#if fresh}
    <div class="fresh">
      <p class="strong">Copy this now. It is not shown again.</p>
      <div class="tokrow">
        <input class="input mono tok" readonly value={fresh.token} />
        <button class="btn btn-ghost btn-sm" onclick={copyKey}>{copied ? 'Copied' : 'Copy'}</button>
        <button class="btn btn-ghost btn-sm" onclick={() => (fresh = null)}>Done</button>
      </div>
    </div>
  {/if}

  <div class="mintrow">
    <input class="input" bind:value={label} placeholder="What is this key for" maxlength="80" />
    <select class="input sel" bind:value={expiresInDays}>
      <option value={0}>No expiry</option>
      <option value={30}>Expires in 30 days</option>
      <option value={90}>Expires in 90 days</option>
      <option value={365}>Expires in a year</option>
    </select>
    <button class="btn btn-sm" onclick={mint} disabled={busy}>Create key</button>
  </div>

  {#if keys.length}
    <table class="keys">
      <thead>
        <tr><th>Label</th><th>Key</th><th>Created</th><th>Last used</th><th>Expires</th><th></th></tr>
      </thead>
      <tbody>
        {#each keys as k (k.id)}
          <tr>
            <td>{k.label || '\u2014'}{#if k.kind !== 'extension'}<span class="tag">{k.kind}</span>{/if}</td>
            <td class="mono">{k.prefix}...</td>
            <td>{fmt(k.createdAt)}</td>
            <td>{k.lastUsedAt ? fmt(k.lastUsedAt) : 'never'}</td>
            <td>{fmt(k.expiresAt)}</td>
            <td><button class="btn btn-ghost btn-sm" onclick={() => revoke(k.id)} disabled={busy}>Revoke</button></td>
          </tr>
        {/each}
      </tbody>
    </table>
    <p class="muted tiny">
      Keys marked <code>slicer</code> are issued automatically when you open a
      slicer session and are replaced each time. Revoking one only ends that
      session's upload access.
    </p>
  {:else}
    <p class="muted tiny">No keys yet.</p>
  {/if}

  {#if err}<p class="err">{err}</p>{/if}
</div>

<style>
  .mintrow { display: flex; gap: 0.6rem; align-items: center; margin: 0.9rem 0; flex-wrap: wrap; }
  .mintrow .input { flex: 1 1 14rem; }
  .sel { flex: 0 0 12rem; }
  .fresh { border: 1px solid var(--ophq-primary); border-radius: 8px; padding: 0.8rem; margin: 0.9rem 0; }
  .fresh .strong { margin: 0 0 0.5rem; font-weight: 600; }
  .tokrow { display: flex; gap: 0.5rem; align-items: center; }
  .tok { flex: 1 1 auto; }
  .keys { width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-top: 0.4rem; }
  .keys th { text-align: left; color: var(--ophq-faint); font-weight: 600; padding: 0.4rem 0.6rem 0.4rem 0; }
  .keys td { padding: 0.45rem 0.6rem 0.45rem 0; border-top: 1px solid var(--ophq-border-soft); }
  .tag { margin-left: 0.4rem; font-size: 0.75rem; color: var(--ophq-faint); }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .tiny { font-size: 0.82rem; }
  .err { color: var(--ophq-err); }
</style>
