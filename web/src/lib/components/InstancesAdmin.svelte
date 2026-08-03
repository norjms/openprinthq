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

  // Deployment mode (local | remote | both).
  let deploymentMode = $state('both');
  let modeBusy = $state(false);
  async function setMode(m) {
    if (modeBusy || m === deploymentMode) return;
    modeBusy = true;
    try { const r = await api.saveAdminSettings({ deployment_mode: m }); deploymentMode = r.deployment_mode || m; msg = `Deployment mode set to ${deploymentMode}.`; }
    catch (e) { err = e.message || 'could not change mode'; }
    finally { modeBusy = false; }
  }

  // Cloudflare TURN credentials for remote camera (WebRTC) relay. The token is
  // write-only: the server never returns it, so the field starts blank and an
  // empty submit means "leave unchanged".
  let turn = $state({ configured: false, key_id_hint: null });
  let turnKeyId = $state('');
  let turnToken = $state('');
  let turnBusy = $state(false);
  let turnMsg = $state('');
  async function saveTurn() {
    if (turnBusy) return;
    turnBusy = true; turnMsg = ''; err = '';
    try {
      const body = {};
      if (turnKeyId.trim()) body.cf_turn_key_id = turnKeyId.trim();
      if (turnToken.trim()) body.cf_turn_api_token = turnToken.trim();
      if (!Object.keys(body).length) { turnMsg = 'Nothing to save.'; return; }
      const r = await api.saveAdminSettings(body);
      turn = r.cf_turn || turn;
      turnKeyId = ''; turnToken = '';
      turnMsg = 'Saved. Credentials are encrypted at rest and never sent back to the browser.';
    } catch (e) { err = e.message || 'could not save TURN credentials'; }
    finally { turnBusy = false; }
  }
  async function clearTurn() {
    if (turnBusy) return;
    turnBusy = true; turnMsg = ''; err = '';
    try {
      const r = await api.saveAdminSettings({ cf_turn_key_id: '', cf_turn_api_token: '' });
      turn = r.cf_turn || { configured: false, key_id_hint: null };
      turnMsg = 'TURN credentials removed. Remote cameras will fall back to STUN only.';
    } catch (e) { err = e.message || 'could not clear TURN credentials'; }
    finally { turnBusy = false; }
  }
  async function testTurn() {
    if (turnBusy) return;
    turnBusy = true; turnMsg = ''; err = '';
    try {
      const r = await api.testTurn();
      turnMsg = `Cloudflare issued a credential with ${r.relay_urls} relay URL(s). TURN is working.`;
    } catch (e) { err = e.message || 'TURN test failed'; }
    finally { turnBusy = false; }
  }

  async function loadAll() {
    const [inv, us, inst, feat, settings] = await Promise.all([
      api.adminInvites().catch(() => ({ invites: [] })),
      api.adminUsers().catch(() => ({ users: [] })),
      api.adminInstances().catch(() => ({ instances: [] })),
      api.adminFeatures().catch(() => ({ features: [] })),
      api.adminSettings().catch(() => ({ deployment_mode: 'both' }))
    ]);
    invites = inv.invites || [];
    users = us.users || [];
    instances = inst.instances || [];
    featureCatalog = feat.features || [];
    deploymentMode = settings.deployment_mode || 'both';
    turn = settings.cf_turn || { configured: false, key_id_hint: null };
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

  <!-- DEPLOYMENT MODE -->
  <section class="card card-pad blk">
    <h3>Printer hosting mode</h3>
    <p class="muted small">Controls which kinds of printers this deployment can manage, and how the Printers page presents adding them.</p>
    <ul class="muted small modelist">
      <li><b>Local</b> — printers live on the <em>same network as OpenPrintHQ</em> and are reached directly. The Cloud Client download and connectors UI are hidden. (You can still reveal them from the Printers page if you have a printer on another network.)</li>
      <li><b>Remote</b> — printers live on a <em>different network</em>, reached through a Cloud Client connector installed on that LAN. Adding printers is disabled until at least one client has paired; once one does, the page unlocks.</li>
      <li><b>Both</b> — manage local and remote printers together. Everything is available: add directly on this network <em>and</em> add via a connector.</li>
    </ul>
    <div class="modetoggle">
      <button class="modebtn" class:on={deploymentMode === 'local'} disabled={modeBusy} onclick={() => setMode('local')}>🏠 Local</button>
      <button class="modebtn" class:on={deploymentMode === 'remote'} disabled={modeBusy} onclick={() => setMode('remote')}>☁️ Remote</button>
      <button class="modebtn" class:on={deploymentMode === 'both'} disabled={modeBusy} onclick={() => setMode('both')}>🔄 Both</button>
    </div>
  </section>

  <!-- REMOTE CAMERA RELAY (TURN) -->
  <section class="card card-pad blk">
    <h3>Remote camera relay (TURN)</h3>
    <p class="muted small">
      Remote camera video goes straight from the viewer's browser to the Cloud Client on the
      printer's network, so it never passes through this server. When both ends sit behind
      restrictive NAT — which is normal on CGNAT connections — that direct path can't be
      established and the video needs a relay to fall back to.
    </p>
    <p class="muted small">
      Cloudflare's STUN service is used always and costs nothing. Adding Cloudflare Realtime
      TURN credentials below enables the relay fallback, which also works over TLS on port 443
      for networks that block UDP. Without it, cameras will work on permissive networks and
      fail on strict ones.
    </p>
    <p class="small">
      Status:
      {#if turn.configured}
        <b class="ok">configured</b> <span class="muted">(key {turn.key_id_hint})</span>
      {:else}
        <b class="warn">not configured</b> <span class="muted">— STUN only</span>
      {/if}
    </p>
    <div class="turnform">
      <label class="small">
        TURN key ID
        <input type="text" bind:value={turnKeyId} placeholder={turn.configured ? 'unchanged' : 'Cloudflare Realtime TURN key ID'} autocomplete="off" />
      </label>
      <label class="small">
        API token
        <input type="password" bind:value={turnToken} placeholder={turn.configured ? 'unchanged' : 'Cloudflare API token'} autocomplete="off" />
      </label>
    </div>
    <div class="turnbtns">
      <button class="btn" disabled={turnBusy} onclick={saveTurn}>Save</button>
      <button class="btn" disabled={turnBusy || !turn.configured} onclick={testTurn}>Test</button>
      <button class="btn danger" disabled={turnBusy || !turn.configured} onclick={clearTurn}>Remove</button>
    </div>
    {#if turnMsg}<p class="small ok">{turnMsg}</p>{/if}
    <p class="muted small">
      The token is stored encrypted and is never returned by the API, so it cannot be read back
      here once saved — rotate it in Cloudflare and re-enter it if it is ever exposed.
    </p>
  </section>

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
        <thead><tr><th>Owner</th><th>Status</th><th>Printers</th><th>Online</th><th>Active</th><th>Features</th><th>Storage quota</th><th>Created</th></tr></thead>
        <tbody>
          {#each instances as i}
            <tr>
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
  .modelist { margin: 0.3rem 0 0.5rem 1rem; padding: 0; line-height: 1.5; }
  .modelist li { margin-bottom: 0.25rem; }
  .modetoggle { display: inline-flex; gap: 0; border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; margin: 0.3rem 0; }
  .modebtn { padding: 0.4rem 1.1rem; font-size: 0.85rem; font-weight: 600; border: none; background: var(--ophq-surface); color: var(--ophq-text-2); cursor: pointer; }
  .modebtn.on { background: var(--ophq-primary); color: #fff; }
  .modebtn:disabled { opacity: 0.6; cursor: default; }
  .note { margin-top: 0.4rem; }
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
  .turnform { display: grid; gap: .6rem; margin: .6rem 0; max-width: 32rem; }
  .turnform label { display: grid; gap: .25rem; }
  .turnform input { width: 100%; }
  .turnbtns { display: flex; gap: .5rem; flex-wrap: wrap; }
  .ok { color: var(--ok, #3aa657); }
  .warn { color: var(--warn, #b8860b); }
</style>
