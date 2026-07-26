<script>
  // Owner-only admin: mint invite codes, manage users + instances, see per-instance
  // usage. The whole page is gated server-side (every /api/admin/* route is
  // requireOwner); a non-owner who reaches the URL directly just gets "not
  // authorized" and no data.
  import { onMount } from 'svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import { api } from '$lib/api';

  let authorized = $state(false);
  let checked = $state(false);
  let invites = $state([]);
  let users = $state([]);
  let instances = $state([]);
  let inviteEmail = $state('');
  let inviteNote = $state('');
  let busy = $state(false);
  let msg = $state('');
  let err = $state('');

  async function loadAll() {
    const [inv, us, inst] = await Promise.all([
      api.adminInvites().catch(() => ({ invites: [] })),
      api.adminUsers().catch(() => ({ users: [] })),
      api.adminInstances().catch(() => ({ instances: [] }))
    ]);
    invites = inv.invites || [];
    users = us.users || [];
    instances = inst.instances || [];
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

  function fmt(ts) { return ts ? new Date(ts).toLocaleString() : '—'; }
</script>

<PageTitle page="Instances" />

<h1>Instances</h1>
<p class="muted sub">Owner console — invites, users, instances and usage.</p>

{#if !checked}
  <p class="muted">Loading…</p>
{:else if !authorized}
  <div class="card card-pad"><b>Not authorized.</b> This area is for owners only.</div>
{:else}
  {#if msg}<p class="ok">{msg}</p>{/if}
  {#if err}<p class="err" role="alert">{err}</p>{/if}

  <!-- INVITES -->
  <section class="card card-pad blk">
    <h2>Invite codes</h2>
    <p class="muted small">Single-use, valid for 2 days. Redeemed on the signup page.</p>
    <form onsubmit={mintInvite} class="row">
      <input bind:value={inviteEmail} placeholder="restrict to email (optional)" />
      <input bind:value={inviteNote} placeholder="note (optional)" />
      <button class="btn btn-primary" disabled={busy}>{busy ? 'Creating…' : 'Create invite'}</button>
    </form>
    {#if invites.length}
      <table>
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
      </table>
    {:else}<p class="muted small">No invite codes yet.</p>{/if}
  </section>

  <!-- INSTANCES -->
  <section class="card card-pad blk">
    <h2>Instances</h2>
    {#if instances.length}
      <table>
        <thead><tr><th>Subdomain</th><th>Owner</th><th>Status</th><th>Printers</th><th>Online</th><th>Active</th><th>Created</th></tr></thead>
        <tbody>
          {#each instances as i}
            <tr>
              <td class="mono">{i.subdomain}</td>
              <td>{i.user_email}</td>
              <td><span class="chip {i.status === 'running' ? 'active' : ''}">{i.status}</span></td>
              <td>{i.stats?.printersTotal ?? 0}</td>
              <td>{i.stats?.printersOnline ?? 0}</td>
              <td>{i.stats?.activeJobs ?? 0}</td>
              <td>{fmt(i.created_at)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {:else}<p class="muted small">No instances yet.</p>{/if}
  </section>

  <!-- USERS -->
  <section class="card card-pad blk">
    <h2>Users</h2>
    {#if users.length}
      <table>
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
      </table>
    {:else}<p class="muted small">No users yet.</p>{/if}
  </section>
{/if}

<style>
  .sub { margin-top: -0.4rem; }
  .blk { margin-top: 1.4rem; }
  .blk h2 { margin: 0 0 0.3rem; font-size: 1.1rem; }
  .row { display: flex; gap: 0.6rem; flex-wrap: wrap; margin: 0.9rem 0; }
  .row input { flex: 1; min-width: 180px; padding: 0.55rem 0.7rem; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); background: var(--ophq-bg-2); color: var(--ophq-text); }
  table { width: 100%; border-collapse: collapse; margin-top: 0.8rem; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--ophq-border); }
  th { color: var(--ophq-muted); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em; }
  .chip.active { background: color-mix(in srgb, var(--ophq-ok, #16a34a) 18%, transparent); color: var(--ophq-ok, #16a34a); }
  .chip.expired, .chip.used { opacity: 0.7; }
  .btn.sm { padding: 0.3rem 0.6rem; font-size: 0.82rem; }
  .ok { color: var(--ophq-ok, #16a34a); font-size: 0.9rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .small { font-size: 0.85rem; }
</style>
