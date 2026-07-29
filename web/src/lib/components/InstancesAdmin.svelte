<script>
  // Owner-only admin: mint invite codes, manage users + instances, see per-instance
  // usage, and set per-instance feature flags + storage quota. Every /api/admin/*
  // route is requireOwner-gated server-side; a non-owner who somehow reaches this
  // just gets "not authorized" and no data.
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let authorized = $state(false);
  let checked = $state(false);
  let invites = $state([]);
  let users = $state([]);
  let instances = $state([]);
  let featureCatalog = $state([]);
  let inviteEmail = $state('');
  let inviteNote = $state('');
  let busy = $state(false);
  let msg = $state('');
  let err = $state('');

  // Per-instance quota edits (MB), keyed by instance id. '' = unlimited.
  let quotaEdits = $state({});
  let quotaBusy = $state({});

  async function loadAll() {
    const [inv, us, inst, feat] = await Promise.all([
      api.adminInvites().catch(() => ({ invites: [] })),
      api.adminUsers().catch(() => ({ users: [] })),
      api.adminInstances().catch(() => ({ instances: [] })),
      api.adminFeatures().catch(() => ({ features: [] }))
    ]);
    invites = inv.invites || [];
    users = us.users || [];
    instances = inst.instances || [];
    featureCatalog = feat.features || [];
    const q = {};
    for (const i of instances) q[i.id] = i.storage_quota_mb == null ? '' : String(i.storage_quota_mb);
    quotaEdits = q;
  }

  async function toggleFeature(inst, f) {
    const enabled = !(inst.features && inst.features[f.key]);
    try { await api.setInstanceFeature(inst.id, f.key, enabled); await loadAll(); }
    catch (e) { err = e.message || 'toggle failed'; }
  }

  async function saveQuota(inst) {
    err = ''; msg = '';
    const raw = (quotaEdits[inst.id] ?? '').toString().trim();
    let quotaMb = null;
    if (raw !== '') {
      const n = Number(raw);
      if (!Number.isInteger(n) || n < 0) { err = 'Quota must be a whole number of MB (or blank for unlimited).'; return; }
      quotaMb = n;
    }
    quotaBusy = { ...quotaBusy, [inst.id]: true };
    try {
      await api.setInstanceQuota(inst.id, quotaMb);
      msg = `Quota for ${inst.subdomain} ${quotaMb == null ? 'set to unlimited' : 'set to ' + quotaMb + ' MB'}.`;
      await loadAll();
    } catch (e) { err = e.message || 'Could not save quota.'; }
    finally { quotaBusy = { ...quotaBusy, [inst.id]: false }; }
  }

  onMount(async () => {
    try {
      await api.adminSummary();
      authorized = true;
      await loadAll();
    } catch { authorized = false; }
    finally { checked = true; }
  });

  async function mintInvite(e) {
    e.preventDefault();
    err = ''; msg = ''; busy = true;
    try {
      const inv = await api.createInvite({ email: inviteEmail.trim() || undefined, note: inviteNote.trim() || undefined });
      msg = 'Invite created: ' + inv.code;
      inviteEmail = ''; inviteNote = '';
      await loadAll();
      try { await navigator.clipboard.writeText(inv.code); msg += ' (copied)'; } catch {}
    } catch (e2) { err = e2?.message || 'Could not create invite.'; }
    finally { busy = false; }
  }

  async function revoke(code) {
    try { await api.revokeInvite(code); await loadAll(); } catch (e) { err = e?.message || 'Revoke failed.'; }
  }

  function fmt(ts) { return ts ? new Date(ts).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—'; }
</script>

{#if !checked}
  <p class="muted">Loading…</p>
{:else if !authorized}
  <div class="card card-pad"><b>Not authorized.</b> This area is for owners only.</div>
{:else}
  {#if msg}<p class="ok">{msg}</p>{/if}
  {#if err}<p class="err" role="alert">{err}</p>{/if}

  <!-- INVITES -->
  <section class="card card-pad blk">
    <h3>Invite codes</h3>
    <p class="muted small">Single-use, valid for 2 days. Redeemed on the signup page.</p>
    <form onsubmit={mintInvite} class="row">
      <input bind:value={inviteEmail} placeholder="restrict to email (optional)" />
      <input bind:value={inviteNote} placeholder="note (optional)" />
      <button class="btn btn-primary" disabled={busy}>{busy ? 'Creating…' : 'Create invite'}</button>
    </form>
    {#if invites.length}
      <div class="tblwrap"><table>
        <thead><tr><th>Code</th><th>Status</th><th>Email</th><th>Note</th><th>Expires</th><th>Used by</th><th></th></tr></thead>
        <tbody>
          {#each invites as i}
            <tr>
              <td class="mono">{i.code}</td>
              <td><span class="chip {i.status}">{i.status}</span></td>
              <td>{i.email || '—'}</td>
              <td>{i.note || '—'}</td>
              <td>{fmt(i.expires_at)}</td>
              <td>{i.used_by_email || '—'}</td>
              <td>{#if i.status === 'active'}<button class="btn btn-ghost sm" onclick={() => revoke(i.code)}>Revoke</button>{/if}</td>
            </tr>
          {/each}
        </tbody>
      </table></div>
    {:else}<p class="muted small">No invite codes yet.</p>{/if}
  </section>

  <!-- INSTANCES -->
  <section class="card card-pad blk">
    <h3>Instances</h3>
    {#if instances.length}
      <div class="tblwrap"><table>
        <thead><tr><th>Subdomain</th><th>Owner</th><th>Status</th><th>Printers</th><th>Online</th><th>Active</th><th>Features</th><th>Storage quota</th><th>Created</th></tr></thead>
        <tbody>
          {#each instances as i}
            <tr>
              <td class="mono">{i.subdomain}</td>
              <td>{i.user_email}</td>
              <td><span class="chip {i.status === 'running' ? 'active' : ''}">{i.status}</span></td>
              <td>{i.stats?.printersTotal ?? 0}</td>
              <td>{i.stats?.printersOnline ?? 0}</td>
              <td>{i.stats?.activeJobs ?? 0}</td>
              <td class="feats">
                {#each featureCatalog as f}
                  <button type="button" class="ftog" class:on={i.features && i.features[f.key]} onclick={() => toggleFeature(i, f)} title={f.desc}>
                    {f.name}{#if f.paid}<span class="paidtag">PAID</span>{/if} · {i.features && i.features[f.key] ? 'ON' : 'OFF'}
                  </button>
                {/each}
              </td>
              <td class="quota">
                <input class="qin" type="number" min="0" step="1" placeholder="unlimited"
                       bind:value={quotaEdits[i.id]} aria-label={`Storage quota (MB) for ${i.subdomain}`} />
                <span class="qunit muted">MB</span>
                <button class="btn btn-ghost sm" onclick={() => saveQuota(i)} disabled={quotaBusy[i.id]}>{quotaBusy[i.id] ? '…' : 'Save'}</button>
              </td>
              <td>{fmt(i.created_at)}</td>
            </tr>
          {/each}
        </tbody>
      </table></div>
      <p class="muted small qhint">Storage quota is per instance in MB; leave blank for unlimited.</p>
    {:else}<p class="muted small">No instances yet.</p>{/if}
  </section>

  <!-- USERS -->
  <section class="card card-pad blk">
    <h3>Users</h3>
    {#if users.length}
      <div class="tblwrap"><table>
        <thead><tr><th>Email</th><th>Name</th><th>Instance</th><th>Status</th><th>Joined</th></tr></thead>
        <tbody>
          {#each users as u}
            <tr>
              <td>{u.email}</td>
              <td>{u.display_name || '—'}</td>
              <td class="mono">{u.subdomain || '—'}</td>
              <td>{u.instance_status || '—'}</td>
              <td>{fmt(u.created_at)}</td>
            </tr>
          {/each}
        </tbody>
      </table></div>
    {:else}<p class="muted small">No users yet.</p>{/if}
  </section>
{/if}

<style>
  .feats { white-space: nowrap; }
  .ftog { font-size: 0.74rem; padding: 0.2rem 0.55rem; border: 1px solid var(--ophq-border); border-radius: 999px; background: var(--ophq-surface); color: var(--ophq-text-2); cursor: pointer; }
  .ftog.on { border-color: var(--ophq-primary); color: var(--ophq-primary); background: var(--ophq-primary-dim); }
  .paidtag { font-size: 0.6rem; font-weight: 700; color: var(--ophq-primary); margin: 0 0.25rem; letter-spacing: 0.04em; }
  .quota { white-space: nowrap; }
  .qin { width: 96px; padding: 0.35rem 0.5rem; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); background: var(--ophq-bg-2); color: var(--ophq-text); font-size: 0.85rem; }
  .qunit { font-size: 0.78rem; margin: 0 0.4rem 0 0.35rem; }
  .qhint { margin-top: 0.5rem; }
  .blk { margin-top: 1.4rem; }
  .blk h3 { margin: 0 0 0.3rem; font-size: 1.1rem; }
  .row { display: flex; gap: 0.6rem; flex-wrap: wrap; margin: 0.9rem 0; }
  .row input { flex: 1; min-width: 180px; padding: 0.55rem 0.7rem; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); background: var(--ophq-bg-2); color: var(--ophq-text); }
  .tblwrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  table { width: 100%; border-collapse: collapse; margin-top: 0.8rem; font-size: 0.9rem; }
  td:last-child, th:last-child { white-space: nowrap; }
  th, td { text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--ophq-border); }
  th { color: var(--ophq-muted); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em; }
  .chip.active { background: color-mix(in srgb, var(--ophq-ok, #16a34a) 18%, transparent); color: var(--ophq-ok, #16a34a); }
  .chip.expired, .chip.used { opacity: 0.7; }
  .btn.sm { padding: 0.3rem 0.6rem; font-size: 0.82rem; }
  .ok { color: var(--ophq-ok, #16a34a); font-size: 0.9rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .small { font-size: 0.85rem; }
</style>
