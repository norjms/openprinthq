<script>
  // Integration keys (control-plane /api/api-keys): one key per app that drives this farm, such as
  // Artisan Hub, n8n or a script. A key works on the whole engine API through OpenPrintHQ, limited by its
  // scope; account settings never take one. The secret is shown once, at creation.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  const SCOPES = [
    { key: 'read', label: 'Read', help: 'See printers, the queue, files, spools and history. Changes nothing.' },
    { key: 'submit', label: 'Submit', help: 'Read, plus upload files and add or reprint jobs.' },
    { key: 'control', label: 'Control', help: 'Submit, plus printer commands, queue changes and settings. What Artisan Hub needs.' }
  ];

  let keys = $state([]);
  let label = $state('Artisan Hub');
  let scope = $state('control');
  let expiresInDays = $state(0);
  let busy = $state(false);
  let err = $state(null);
  let fresh = $state(null);   // the one-time secret, cleared on Done
  let copied = $state(false);
  let confirmRevoke = $state(null);

  async function load() {
    try { keys = await api.integrationKeys(); err = null; }
    catch (e) { err = e.message; }
  }
  onMount(load);

  async function mint() {
    if (!label.trim()) { err = 'Name the key after the app that will use it.'; return; }
    busy = true; err = null;
    try {
      fresh = await api.createIntegrationKey({ label: label.trim(), scope, expiresInDays: Number(expiresInDays) || 0 });
      copied = false;
      await load();
    } catch (e) { err = e.message; }
    finally { busy = false; }
  }

  async function revoke(id) {
    busy = true; err = null;
    try { await api.revokeIntegrationKey(id); confirmRevoke = null; await load(); }
    catch (e) { err = e.message; }
    finally { busy = false; }
  }

  async function copyKey() {
    try { await navigator.clipboard.writeText(fresh.token); copied = true; }
    catch { copied = false; }
  }

  const fmt = (d) => (d ? new Date(d).toLocaleDateString() : 'never');
  const scopeLabel = (s) => SCOPES.find((x) => x.key === s)?.label || s;
</script>

<div class="card card-pad">
  <span class="eyebrow">Integration keys</span>
  <p class="muted">
    For apps that run this farm for you, such as Artisan Hub, n8n or your own scripts. The app sends the key as
    <code>Authorization: Bearer ophqk_…</code> and can use OpenPrintHQ's API as far as the key's scope allows.
    Sign-in, people, keys and backups always need you, never a key.
  </p>

  {#if fresh}
    <div class="fresh">
      <p class="strong">Copy this now. It is not shown again.</p>
      <div class="tokrow">
        <input class="input mono tok" readonly value={fresh.token} aria-label="New integration key" />
        <button class="btn btn-ghost btn-sm" onclick={copyKey}>{copied ? 'Copied' : 'Copy'}</button>
        <button class="btn btn-ghost btn-sm" onclick={() => (fresh = null)}>Done</button>
      </div>
      <p class="muted tiny">{fresh.label}, {scopeLabel(fresh.scope)}{fresh.expiresAt ? `, until ${fmt(fresh.expiresAt)}` : ', no expiry'}.
        In Artisan Hub: Settings, Connected services, OpenPrintHQ.</p>
    </div>
  {/if}

  <div class="mintrow">
    <input class="input" bind:value={label} placeholder="Which app is this for" maxlength="80" aria-label="Key name" />
    <select class="input sel" bind:value={expiresInDays} aria-label="Expiry">
      <option value={0}>No expiry</option>
      <option value={30}>Expires in 30 days</option>
      <option value={90}>Expires in 90 days</option>
      <option value={365}>Expires in a year</option>
    </select>
  </div>
  <div class="scopes" role="radiogroup" aria-label="Scope">
    {#each SCOPES as s}
      <label class="scope" class:on={scope === s.key}>
        <input type="radio" name="ik-scope" value={s.key} bind:group={scope} />
        <span><span class="strong">{s.label}</span><span class="muted tiny"> {s.help}</span></span>
      </label>
    {/each}
  </div>
  <button class="btn btn-sm" onclick={mint} disabled={busy}>Create key</button>

  {#if keys.length}
    <div class="tablewrap">
      <table class="keys">
        <thead>
          <tr><th>Name</th><th>Scope</th><th>Key</th><th>Created</th><th>Last used</th><th>Expires</th><th></th></tr>
        </thead>
        <tbody>
          {#each keys as k (k.id)}
            <tr>
              <td>{k.label || 'API key'}</td>
              <td>{scopeLabel(k.scope)}</td>
              <td class="mono">{k.prefix}…</td>
              <td>{fmt(k.createdAt)}</td>
              <td>{fmt(k.lastUsedAt)}</td>
              <td>{fmt(k.expiresAt)}</td>
              <td class="act">
                {#if confirmRevoke === k.id}
                  <button class="btn btn-sm danger" onclick={() => revoke(k.id)} disabled={busy}>Revoke now</button>
                  <button class="btn btn-ghost btn-sm" onclick={() => (confirmRevoke = null)}>Keep</button>
                {:else}
                  <button class="btn btn-ghost btn-sm" onclick={() => (confirmRevoke = k.id)} disabled={busy}>Revoke</button>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="muted tiny">Revoking a key stops the app that uses it straight away.</p>
  {:else}
    <p class="muted tiny">No integration keys yet.</p>
  {/if}

  {#if err}<p class="err">{err}</p>{/if}
</div>

<style>
  .mintrow { display: flex; gap: 0.6rem; align-items: center; margin: 0.9rem 0 0.6rem; flex-wrap: wrap; }
  .mintrow .input { flex: 1 1 14rem; }
  .sel { flex: 0 0 12rem; }
  .scopes { display: grid; gap: 0.4rem; margin: 0 0 0.8rem; }
  .scope { display: flex; gap: 0.6rem; align-items: flex-start; padding: 0.55rem 0.7rem; border: 1px solid var(--ophq-border-soft); border-radius: 8px; cursor: pointer; }
  .scope.on { border-color: var(--ophq-primary); }
  .scope input { margin-top: 0.2rem; }
  .fresh { border: 1px solid var(--ophq-primary); border-radius: 8px; padding: 0.8rem; margin: 0.9rem 0; }
  .fresh .strong { margin: 0 0 0.5rem; }
  .strong { font-weight: 600; }
  .tokrow { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
  .tok { flex: 1 1 16rem; }
  .tablewrap { overflow-x: auto; margin-top: 0.9rem; }
  .keys { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
  .keys th { text-align: left; color: var(--ophq-faint); font-weight: 600; padding: 0.4rem 0.6rem 0.4rem 0; white-space: nowrap; }
  .keys td { padding: 0.45rem 0.6rem 0.45rem 0; border-top: 1px solid var(--ophq-border-soft); }
  .act { white-space: nowrap; text-align: right; }
  .danger { background: var(--ophq-err); border-color: var(--ophq-err); }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .tiny { font-size: 0.82rem; }
  .err { color: var(--ophq-err); }
</style>
