<script>
  // People: everyone who can sign in to this hub and what they can do. Brings together what were the Team page and
  // the hub owner's People and teams page (/team and /platform redirect here; the owner's part is ?view=hub).
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { Icon, Toggle } from 'openprinthq-ui';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { session, loadMe, can, ROLE_LABEL, ROLE_HELP } from '$lib/session.svelte.js';
  import PermPicker from '$lib/PermPicker.svelte';
  import Page from '$lib/Page.svelte';

  const ROLES = ['viewer', 'member', 'admin', 'owner'];
  const owner = $derived(!!session.me?.user?.platform_admin);
  let view = $state(page.url.searchParams.get('view') === 'hub' ? 'hub' : 'team');
  function pick(v) { view = v; const u = new URL(location.href); if (v === 'hub') u.searchParams.set('view', 'hub'); else u.searchParams.delete('view'); history.replaceState(history.state, '', u); if (v === 'hub' && !data) loadHub(); }

  // ---- the team (what was /team)
  let t = $state(null);
  let name = $state('');
  let inv = $state({ email: '', name: '', role: 'member', custom: false, perms: [] });
  let sent = $state(null);
  let editing = $state(null);      // { member, role, perms, custom }
  const meId = $derived(session.me?.user?.user_id);
  const isOwner = $derived(t?.role === 'owner');
  const mine = $derived(t?.my_perms || []);
  // roles this person may hand out: never one that includes a permission they don't hold
  const grantable = $derived(t ? ROLES.filter((r) => (r === 'owner' ? isOwner : t.role_perms[r].every((x) => mine.includes(x)))) : []);

  async function load() {
    t = await api.get('/api/team'); name = t.name;
    if (t.role === 'owner') loadPrinters();   // owners hold every permission, People's included
  }

  // ---- printers: everyone shares the farm's printers; the owner may narrow them per person (all by default)
  let farmPrinters = $state(null);  // [{ id, name }] from the farm, null until loaded
  let choosing = $state(null);      // { member, all, ids }
  let printerBusy = $state(false);
  async function loadPrinters() {
    try { farmPrinters = (await api.get('/api/team/printers')).printers || []; } catch { farmPrinters = []; }
  }
  function printersText(m) {
    if (m.role === 'owner' || m.printers === 'all') return 'All';
    const n = m.printers.length;
    return n === 0 ? 'None' : `${n} of ${farmPrinters?.length ?? n}`;
  }
  function openPrinters(m) {
    choosing = { member: m, all: m.printers === 'all', ids: m.printers === 'all' ? (farmPrinters || []).map((x) => x.id) : [...m.printers] };
  }
  function togglePrinter(id, on) { choosing.ids = on ? [...new Set([...choosing.ids, id])] : choosing.ids.filter((x) => x !== id); }
  async function savePrinters() {
    printerBusy = true;
    const body = { printers: choosing.all ? 'all' : choosing.ids };
    try {
      await attempt(() => api.put(`/api/team/members/${choosing.member.user_id}/printers`, body), 'Printers saved');
      choosing = null;
      load();
    } catch { /* the toast said why */ } finally { printerBusy = false; }
  }
  async function rename() { await attempt(() => api.patch('/api/team', { name }), 'Renamed'); await loadMe(); load(); }
  function openPerms(m) { editing = { member: m, role: m.role, perms: [...m.perms], custom: m.custom }; }
  function roleChanged() { editing.perms = [...t.role_perms[editing.role]]; }
  async function saveMember() {
    const e = editing;
    const body = { role: e.role, ...(e.custom ? { perms: e.perms } : { reset_perms: true }) };
    await attempt(() => api.patch(`/api/team/members/${e.member.user_id}`, body), `Saved ${e.member.name}`);
    editing = null; load();
  }
  async function remove(m) {
    if (!confirm(`Remove ${m.name} from ${t.name}? Their API keys and connected apps for this team stop working.`)) return;
    const r = await attempt(() => api.del(`/api/team/members/${m.user_id}`));
    toast(r.hub_access_removed ? `Removed. ${m.name} is in no other team, so their hub sign-in was turned off.` : 'Removed');
    load();
  }
  function invRole() { inv.perms = [...t.role_perms[inv.role]]; }
  async function invite() {
    const body = { email: inv.email, name: inv.name || null, role: inv.role, ...(inv.custom && inv.role !== 'owner' ? { perms: inv.perms } : {}) };
    sent = await attempt(() => api.post('/api/team/invites', body));
    toast(sent.emailed ? `Invite emailed to ${inv.email}` : 'Invite ready: send them the link below');
    inv = { email: '', name: '', role: 'member', custom: false, perms: [] };
    load();
  }
  async function revoke(i) { await attempt(() => api.del(`/api/team/invites/${i.id}`), 'Invite cancelled'); load(); }
  async function leave() {
    if (!confirm(`Leave ${t.name}? You lose access to its models and settings.`)) return;
    await attempt(() => api.post('/api/team/leave')); location.href = '/';
  }
  async function copy(text) { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch { toast('Select the text and copy it', 'bad'); } }
  const permCount = (m) => `${m.perms.length} of ${Object.keys(t.perm_catalog).length}`;

  // ---- the whole hub, for its owner (what was /platform)
  let data = $state(null);
  let users = $state([]);
  let invites = $state([]);
  let edit = $state(null);
  // An installation has one team: everyone added joins it, with the role chosen.
  let add = $state({ email: '', name: '', role: 'member', custom: false, perms: [] });
  let hubSent = $state(null);
  async function loadHub() {
    data = await api.get('/api/platform/teams');
    users = await api.get('/api/platform/users');
    invites = await api.get('/api/platform/invites');
    await loadOrca();
    await loadApps();
  }
  // Every other desktop app the installation runs (docs/apps.md): who may open each, as for OrcaSlicer.
  let deskApps = $state([]);
  let appOn = $state({});        // by app id, then person
  let appBusy = $state(null);
  async function loadApps() {
    try { deskApps = (await api.get('/api/platform/apps')).apps || []; } catch { deskApps = []; }
    appOn = Object.fromEntries(deskApps.map((a) => [a.id, Object.fromEntries(users.map((u) => [u.id, (a.allowed || []).includes(u.id)]))]));
  }
  async function setApp(a, u, on) {
    appBusy = `${a.id}:${u.id}`;
    try {
      await attempt(() => api.put(`/api/platform/users/${u.id}/apps/${a.id}`, { allowed: on }),
        on ? `${u.name} can now open ${a.title}` : `${u.name} can no longer open ${a.title}`);
    } catch { /* attempt shows the error */ }
    appBusy = null;
    await loadApps();
  }
  // OrcaSlicer in the browser, when this installation runs it: who may open it. The hub's owners always may; anyone
  // else when the owner turns it on here (they join its group in the hub's user directory).
  let orca = $state({ available: false });
  let orcaBusy = $state(null);
  let orcaOn = $state({});       // the switches, by person: rebuilt from the hub's answer after every change
  async function loadOrca() {
    try { orca = await api.get('/api/platform/apps/orcaslicer'); } catch { orca = { available: false }; }
    orcaOn = Object.fromEntries(users.map((u) => [u.id, (orca.allowed || []).includes(u.id)]));
    await loadDesks();
    await loadShares();
  }
  // With a desktop for each person, their files stay after they leave until the owner removes them here. The list
  // and the removal come from the installation itself, at the desktop's own address, as the signed-in owner.
  const DESKS = '/apps/orcaslicer/_desktop/homes';
  let desks = $state(null);
  let deskBusy = $state(null);
  async function loadDesks() {
    if (!orca.desktops) { desks = null; return; }
    try {
      const r = await fetch(DESKS, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const body = await r.json().catch(() => null);
      desks = r.ok && Array.isArray(body) ? { rows: body, error: null }
        : { rows: [], error: body?.error?.message || 'The installation did not list the desktops. Open OrcaSlicer once, then reload this page.' };
    } catch { desks = { rows: [], error: 'The installation did not answer about the desktops.' }; }
  }
  async function removeDesk(d) {
    if (!confirm(`Remove ${d.name}'s OrcaSlicer desktop and its files? Their printers, settings and files in OrcaSlicer are deleted for good.`)) return;
    deskBusy = d.person;
    try {
      await attempt(async () => {
        const r = await fetch(`${DESKS}/${encodeURIComponent(d.person)}`, { method: 'DELETE', credentials: 'same-origin',
          headers: { Accept: 'application/json', 'X-AHI-Desktop': '1' } });
        const body = await r.json().catch(() => null);
        if (!r.ok || !Array.isArray(body)) throw new Error(body?.error?.message || 'The installation did not remove the desktop.');
        desks = { rows: body, error: null };
      }, `${d.name}'s desktop files were removed`);
    } catch { /* attempt shows the error */ }
    deskBusy = null;
  }
  async function setOrca(u, on) {
    orcaBusy = u.id;
    try {
      await attempt(() => api.put(`/api/platform/users/${u.id}/orcaslicer`, { allowed: on }),
        on ? `${u.name} can now open OrcaSlicer in the browser` : `${u.name} can no longer open OrcaSlicer in the browser`);
    } catch { /* attempt shows the error */ }
    orcaBusy = null;
    await loadOrca();
  }
  // Shared folders: folders each person's own OrcaSlicer desktop opens under Files, Shared. The hub keeps the list
  // and who may read or write each one; the installation mounts them when a desktop starts. Removing one only marks
  // it: its files go when the owner confirms on the installer's summary.
  let shares = $state(null);
  let shareName = $state('');
  let renaming = $state(null);     // { slug, name }
  let shareBusy = $state(null);
  let picking = $state(null);      // the person whose folders are being chosen
  const liveShares = $derived((shares?.shares || []).filter((s) => !s.deleting));
  async function loadShares() {
    if (!orca.available) { shares = null; return; }
    try { shares = await api.get('/api/platform/shares?members=1'); } catch { shares = null; }
  }
  async function addShare() {
    const name = shareName.trim();
    await attempt(() => api.post('/api/platform/shares', { name }), `${name} added`);
    shareName = ''; await loadShares();
  }
  async function renameShare() {
    const r = renaming;
    await attempt(() => api.patch(`/api/platform/shares/${r.slug}`, { name: r.name }), 'Renamed');
    renaming = null; await loadShares();
  }
  async function markShare(s) {
    if (!confirm(`Remove ${s.name}? It leaves People and every desktop started from now on. Its files stay until you confirm on the installer's summary.`)) return;
    await attempt(() => api.post(`/api/platform/shares/${s.slug}/remove`), `${s.name} marked for removal`);
    await loadShares();
  }
  async function keepShare(s) { await attempt(() => api.post(`/api/platform/shares/${s.slug}/keep`), `${s.name} kept`); await loadShares(); }
  const accessOf = (u, s) => shares?.access?.[u.id]?.[s.slug] || 'none';
  function peopleText(s) {
    let r = 0, w = 0;
    for (const u of users) { if (u.platform_admin) continue; const a = accessOf(u, s); if (a === 'read') r++; if (a === 'write') w++; }
    return r || w ? [w ? `${w} can write` : '', r ? `${r} can read` : ''].filter(Boolean).join(', ') : 'Owners only';
  }
  function mineText(u) {
    const n = liveShares.filter((s) => accessOf(u, s) !== 'none').length;
    return n ? `${n} of ${liveShares.length}` : 'None';
  }
  const LEVEL = { none: 'no access', read: 'read only', write: 'read and write' };
  async function setAccess(u, s, access) {
    shareBusy = `${u.id}:${s.slug}`;
    try { await attempt(() => api.put(`/api/platform/users/${u.id}/shares/${s.slug}`, { access }), `${u.name}: ${s.name}, ${LEVEL[access]}`); } catch { /* attempt shows the error */ }
    shareBusy = null;
    await loadShares();
  }
  function openDirs(x) { edit = { id: x.id, name: x.name, deltaprints: x.source_dirs.deltaprints || '', inbox: x.source_dirs.inbox || '' }; }
  async function saveDirs() {
    await attempt(() => api.patch(`/api/platform/teams/${edit.id}`, { source_dirs: { deltaprints: edit.deltaprints, inbox: edit.inbox } }), 'Saved');
    edit = null; loadHub();
  }
  function addRoleChanged() { add.perms = [...(t?.role_perms?.[add.role] || [])]; }
  async function addPerson() {
    const b = { email: add.email, name: add.name || null, role: add.role };
    if (add.custom && add.role !== 'owner') b.perms = add.perms;
    hubSent = await attempt(() => api.post('/api/platform/people', b));
    toast(hubSent.emailed ? `Invite emailed to ${add.email}` : 'Invite ready: send them the link below');
    add = { email: '', name: '', role: 'member', custom: false, perms: [] };
    loadHub();
  }
  async function cancelHub(i) { await attempt(() => api.del(`/api/platform/invites/${i.id}`), 'Invite cancelled'); loadHub(); }
  async function removePerson(u) {
    if (!confirm(`Remove ${u.name} from every team and turn off their hub sign-in?`)) return;
    await attempt(() => api.post(`/api/platform/users/${u.id}/remove`), 'Removed'); loadHub();
  }
  // A one-time link for someone to choose a new password (logins in LLDAP), for the owner to pass on.
  let pwLink = $state(null);
  async function passwordLink(u) {
    const r = await attempt(() => api.post(`/api/account/password-link/${u.id}`));
    pwLink = { ...r, name: u.name };
  }
  const teamName = (id) => data?.teams.find((x) => x.id === id)?.name || `#${id}`;
  const canSubmit = $derived(!!add.email);

  onMount(() => { load(); if (view === 'hub') loadHub(); });
</script>

<Page title="People" description="Everyone who can sign in to this hub, and what they can do.">
  {#snippet actions()}
    {#if owner}
      <div class="ah-seg" role="group" aria-label="Whose people">
        <button type="button" aria-pressed={view === 'team'} onclick={() => pick('team')}>{t?.name || 'This team'}</button>
        <button type="button" aria-pressed={view === 'hub'} onclick={() => pick('hub')}>Whole hub</button>
      </div>
    {/if}
    {#if view === 'team' && t?.role}<button class="btn sm ghost" onclick={leave}>Leave team</button>{/if}
  {/snippet}
  {#snippet widgets()}
    <section class="ah-glass ah-card">
      <div class="flex items-center gap-[10px]"><div class="ah-icon-tile"><Icon name="shield" /></div>
        <div style="flex:1;min-width:0"><h3 class="ah-widget-title">Roles</h3><div class="ah-muted">What each role can do</div></div></div>
      <div class="ah-table" role="table" aria-label="Roles" style="grid-template-columns:minmax(0,0.6fr) minmax(0,1.6fr)">
        <div role="row"><div role="columnheader">Role</div><div role="columnheader">Can</div></div>
        {#each [...ROLES].reverse() as r}<div role="row"><div role="cell">{ROLE_LABEL[r]}</div><div role="cell">{ROLE_HELP[r][0].toUpperCase() + ROLE_HELP[r].slice(1)}</div></div>{/each}
      </div>
    </section>
    {#if view === 'team' && t && can('team')}
      <section class="ah-glass ah-card">
        <h3 class="ah-widget-title">Team name</h3>
        <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); rename(); }}>
          <input class="input" bind:value={name} maxlength="120" aria-label="Team name" />
          <button class="btn primary" disabled={!name.trim() || name === t.name}>Save</button></form>
        <p class="ah-muted">Everyone here shares this team's models, printers, licenses, settings and connected services.</p>
      </section>
    {/if}
  {/snippet}

  {#if view === 'team'}
    {#if t}
      {#if can('invite')}
        <section class="ah-glass ah-card" style="flex:none">
          <h2 class="ah-card-title">Invite someone</h2>
          <form class="flex flex-col gap-3" onsubmit={(e) => { e.preventDefault(); invite(); }}>
            <div class="flex gap-3 items-end">
              <div class="ah-field" style="flex:2"><label for="ie">Email</label><input id="ie" class="input" type="email" required placeholder="name@example.com" bind:value={inv.email} /></div>
              <div class="ah-field" style="flex:1.2"><label for="in">Name (optional)</label><input id="in" class="input" bind:value={inv.name} /></div>
              <div class="ah-field" style="flex:1"><label for="ir">Role</label>
                <select id="ir" class="input" bind:value={inv.role} onchange={invRole}>{#each grantable as r}<option value={r}>{ROLE_LABEL[r]}</option>{/each}</select></div>
              <button class="btn primary" type="submit">Send invite</button>
            </div>
            {#if inv.role !== 'owner'}
              <label class="flex items-center gap-2"><input type="checkbox" bind:checked={inv.custom} onchange={invRole} /> Choose permissions instead of the role's defaults</label>
              {#if inv.custom}<PermPicker catalog={t.perm_catalog} bind:value={inv.perms} allowed={mine} />{/if}
            {/if}
          </form>
          <p class="ah-muted">They get a link. If they don't have a login yet, the link lets them create one first. Invites last 14 days.</p>
          {#if sent}
            <div class="ah-banner ah-banner--live" style="flex-direction:column;align-items:flex-start">
              <div><b>Send them this link</b> <button class="btn sm" onclick={() => copy(sent.link)}>Copy</button></div><code style="word-break:break-all">{sent.link}</code>
              {#if sent.account === 'invited'}<span>They don't have a login yet; the link walks them through creating one.</span>
              {:else if sent.account === 'existing'}<span>They already have a login and can now sign in to the hub.</span>
              {:else if sent.note}<span>{sent.note}</span>{/if}
            </div>
          {/if}
        </section>
      {/if}
      <section class="ah-glass ah-card" style="flex:1 0 auto;min-height:220px">
        <h2 class="ah-card-title">{t.members.length} {t.members.length === 1 ? 'person' : 'people'}</h2>
        <table class="grid"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Permissions</th>{#if farmPrinters?.length}<th>Printers</th>{/if}<th>Signed in</th><th></th></tr></thead><tbody>
          {#each t.members as m}
            <tr>
              <td>{m.name}{#if m.user_id === meId} <span class="chip">You</span>{/if}</td>
              <td class="ah-secondary" style="word-break:break-all">{m.email || ''}</td>
              <td><span class="chip {m.role === 'owner' ? 'live' : ''}">{ROLE_LABEL[m.role]}</span></td>
              <td>{m.role === 'owner' ? 'All' : permCount(m)}{#if m.custom} <span class="chip warn">Custom</span>{/if}</td>
              {#if farmPrinters?.length}
                <td style="white-space:nowrap">{#if m.role === 'owner'}All{:else}<button class="btn sm ghost" onclick={() => openPrinters(m)} aria-label="Choose printers for {m.name}">{printersText(m)}</button>{/if}</td>
              {/if}
              <td class="ah-muted">{m.last_login_at ? ago(m.last_login_at) : 'Never'}</td>
              <td style="text-align:right">
                {#if can('people') && m.user_id !== meId && (isOwner || m.role !== 'owner')}
                  <button class="btn sm ghost" onclick={() => openPerms(m)}>Change role</button>
                  <button class="btn sm danger" onclick={() => remove(m)}>Remove</button>
                {/if}
              </td>
            </tr>
          {/each}
          {#each can('invite') ? t.invites || [] : [] as i}
            <tr><td>{i.name || i.email}</td><td class="ah-secondary">{i.email}</td><td><span class="chip warn">Invited</span></td>
              <td>{ROLE_LABEL[i.role]}{#if i.perms} <span class="chip warn">Custom</span>{/if}</td>
              {#if farmPrinters?.length}<td class="ah-muted">All</td>{/if}
              <td class="ah-muted">{i.expired ? 'Invite expired' : 'Sent ' + ago(i.created_at)}{i.needs_account ? ', no login yet' : ''}</td>
              <td style="text-align:right"><button class="btn sm ghost" onclick={() => revoke(i)}>Cancel invite</button></td></tr>
          {/each}
        </tbody></table>
      </section>
    {:else}<p class="ah-muted">Loading...</p>{/if}

  {:else if data}
    <section class="ah-glass ah-card" style="flex:none">
      <h2 class="ah-card-title">Add a person</h2>
      <p class="ah-muted" style="margin-top:-6px">Only the hub owner sees this. Adding someone here gives them a hub sign-in: an account in the hub's user directory (LLDAP), used through the Authelia sign-in, or access for the account they already have. Everyone added joins this hub's team, with the role you choose.</p>
      <form class="flex flex-col gap-3" onsubmit={(e) => { e.preventDefault(); addPerson(); }}>
        <div class="flex gap-3">
          <input class="input" type="email" required placeholder="name@example.com" bind:value={add.email} aria-label="Email" />
          <input class="input" placeholder="Name" bind:value={add.name} aria-label="Name" />
        </div>
        <div class="flex gap-3">
          <select class="input" style="width:12rem" bind:value={add.role} onchange={addRoleChanged} aria-label="Role">{#each ROLES as r}<option value={r}>{ROLE_LABEL[r]}</option>{/each}</select>
        </div>
        {#if add.role !== 'owner' && t}
          <label class="flex items-center gap-2"><input type="checkbox" bind:checked={add.custom} onchange={addRoleChanged} /> Choose permissions instead of the role's defaults</label>
          {#if add.custom}<PermPicker catalog={t.perm_catalog} bind:value={add.perms} allowed={Object.keys(t.perm_catalog)} />{/if}
        {/if}
        <button class="btn primary" style="align-self:flex-start" disabled={!canSubmit}>Add and create invite</button>
      </form>
      {#if hubSent}
        <div class="ah-banner ah-banner--live" style="flex-direction:column;align-items:flex-start">
          <div><b>Send them this link</b> <button class="btn sm" onclick={() => copy(hubSent.link)}>Copy</button></div><code style="word-break:break-all">{hubSent.link}</code>
          {#if hubSent.account === 'invited'}<span>No sign-in yet: the link lets them choose a password (it is tied to their email), then join.</span>
          {:else if hubSent.account === 'existing'}<span>They already had a sign-in; it now gives them access to the hub.</span>
          {:else if hubSent.note}<span>{hubSent.note}</span>{/if}
        </div>
      {/if}
    </section>
    {#if invites.length}
      <section class="ah-glass ah-card" style="flex:none">
        <h2 class="ah-card-title">Waiting to accept</h2>
        <table class="grid"><thead><tr><th>Person</th><th>Into</th><th>Role</th><th>Sent</th><th></th></tr></thead><tbody>
          {#each invites as i}
            <tr><td>{i.name || i.email}<div class="ah-muted">{i.email}{i.needs_account ? ', no login yet' : ''}</div></td>
              <td>{i.team}</td><td>{ROLE_LABEL[i.role]}</td>
              <td class="ah-muted">{i.expired ? 'Expired' : ago(i.created_at)}</td>
              <td style="text-align:right"><button class="btn sm ghost" onclick={() => cancelHub(i)}>Cancel invite</button></td></tr>
          {/each}
        </tbody></table>
      </section>
    {/if}
    <section class="ah-glass ah-card" style="flex:none">
      <h2 class="ah-card-title">The team</h2>
      <table class="grid"><thead><tr><th>Team</th><th>Members</th><th>Models</th><th>Server folders</th><th>Created</th><th></th></tr></thead><tbody>
        {#each data.teams as x}
          <tr><td>{x.name}<div class="ah-muted">{x.slug}</div></td>
            <td>{x.members}</td><td>{x.models}</td>
            <td>{#if x.source_dirs.deltaprints}<div>DeltaPrints: {x.source_dirs.deltaprints}</div>{/if}{#if x.source_dirs.inbox}<div>Inbox: {x.source_dirs.inbox}</div>{/if}{#if !x.source_dirs.deltaprints && !x.source_dirs.inbox}<span class="ah-muted">None</span>{/if}</td>
            <td class="ah-muted">{ago(x.created_at)}</td>
            <td style="text-align:right"><button class="btn sm ghost" onclick={() => openDirs(x)}>Set folders</button></td></tr>
        {/each}
      </tbody></table>
    </section>
    {#if shares?.available}
      <section class="ah-glass ah-card" style="flex:none" data-testid="shared-folders">
        <h2 class="ah-card-title">Shared folders</h2>
        <p class="ah-muted" style="margin-top:-6px">Folders each person's own OrcaSlicer desktop opens under Files, Shared. Choose who reads or writes each one in the Shared folders column below; hub owners can always write. A change takes effect the next time that person's desktop starts.</p>
        {#if shares.desktops !== 'per-person'}<div class="ah-banner" role="status">Everyone shares one OrcaSlicer desktop now, so it opens no shared folders. They appear once each person has a desktop of their own, which you turn on in the installer's summary.</div>{/if}
        {#if shares.error}<div class="ah-banner ah-banner--bad" role="alert">{shares.error}</div>{/if}
        <form class="flex gap-3" onsubmit={(e) => { e.preventDefault(); addShare(); }}>
          <input class="input" maxlength="60" placeholder="Folder name, such as Production" bind:value={shareName} aria-label="New shared folder's name" />
          <button class="btn primary" disabled={!shareName.trim()}>Add folder</button>
        </form>
        {#if shares.shares.length}
          <table class="grid"><thead><tr><th>Folder</th><th>Who</th><th></th></tr></thead><tbody>
            {#each shares.shares as s (s.slug)}
              <tr><td>{s.name}{#if s.deleting} <span class="chip warn">Marked for removal</span>{/if}</td>
                <td class="ah-secondary">{s.deleting ? "Its files go when you confirm on the installer's summary." : peopleText(s)}</td>
                <td style="text-align:right;white-space:nowrap">
                  {#if s.deleting}<button class="btn sm ghost" onclick={() => keepShare(s)}>Keep it</button>
                  {:else}<button class="btn sm ghost" onclick={() => (renaming = { slug: s.slug, name: s.name })}>Rename</button>
                    <button class="btn sm danger" onclick={() => markShare(s)}>Remove</button>{/if}</td></tr>
            {/each}
          </tbody></table>
        {:else}<p class="ah-muted">No shared folders yet.</p>{/if}
      </section>
    {/if}
    <section class="ah-glass ah-card" style="flex:none">
      <h2 class="ah-card-title">Everyone with a sign-in</h2>
      {#if orca.available}
        <p class="ah-muted" style="margin-top:-6px">OrcaSlicer in the browser opens at /apps/orcaslicer/. Hub owners can always open it; turn it on for anyone else who should.</p>
        {#if orca.error}<div class="ah-banner ah-banner--bad" role="alert">{orca.error}</div>{/if}
        {#if desks}
          <details data-testid="desktop-files">
            <summary>Desktop files ({desks.rows.length}): each person's own OrcaSlicer desktop</summary>
            <p class="ah-muted">A desktop's printers, settings and files stay after its person leaves the hub, until you remove them here.</p>
            {#if desks.error}<div class="ah-banner ah-banner--bad" role="alert">{desks.error}</div>{/if}
            {#if desks.rows.length}
              <table class="grid"><thead><tr><th>Desktop of</th><th>State</th><th></th></tr></thead><tbody>
                {#each desks.rows as d (d.person)}
                  <tr><td>{d.name}<div class="ah-muted">{d.person}</div></td>
                    <td>{#if d.running}<span class="chip live">Open now</span>{:else if !d.current}<span class="chip">Left the hub</span>{:else}<span class="ah-muted">Stopped</span>{/if}</td>
                    <td style="text-align:right"><button class="btn sm danger" disabled={deskBusy === d.person} onclick={() => removeDesk(d)}>Remove files</button></td></tr>
                {/each}
              </tbody></table>
            {:else if !desks.error}<p class="ah-muted">Nobody has opened a desktop yet.</p>{/if}
          </details>
        {/if}
      {/if}
      {#if pwLink}
        <div class="ah-banner ah-banner--live" style="flex-direction:column;align-items:flex-start">
          <div><b>Send {pwLink.name} this link</b> <button class="btn sm" onclick={() => copy(pwLink.link)}>Copy</button></div><code style="word-break:break-all">{pwLink.link}</code>
          <span>It lets them choose a new password for the login {pwLink.user}. It works once, for {pwLink.expires_in_minutes} minutes, and replaces any older link.</span>
        </div>
      {/if}
      <table class="grid"><thead><tr><th>Person</th><th>Teams</th>{#if orca.available}<th>OrcaSlicer in the browser</th>{/if}{#each deskApps as a (a.id)}<th>{a.title}</th>{/each}{#if liveShares.length}<th>Shared folders</th>{/if}<th>Last sign-in</th><th></th></tr></thead><tbody>
        {#each users as u}
          <tr><td>{u.name}{#if u.platform_admin} <span class="chip live">Hub owner</span>{/if}<div class="ah-muted">{u.email || ''}</div></td>
            <td>{u.teams.map((m) => `${teamName(m.team_id)} (${ROLE_LABEL[m.role]})`).join(', ') || 'None'}</td>
            {#if orca.available}
              <td><div class="flex items-center gap-2">
                {#if u.platform_admin}
                  <Toggle checked={true} disabled label="OrcaSlicer in the browser for {u.name}" /><span class="ah-muted">Hub owners always have it</span>
                {:else}
                  <Toggle bind:checked={orcaOn[u.id]} disabled={orcaBusy === u.id || !!orca.error} label="OrcaSlicer in the browser for {u.name}" onchange={(on) => setOrca(u, on)} />
                {/if}
              </div></td>
            {/if}
            {#each deskApps as a (a.id)}
              <td><div class="flex items-center gap-2">
                {#if u.platform_admin}
                  <Toggle checked={true} disabled label="{a.title} for {u.name}" /><span class="ah-muted">Hub owners always have it</span>
                {:else}
                  <Toggle bind:checked={appOn[a.id][u.id]} disabled={appBusy === `${a.id}:${u.id}` || !!a.error} label="{a.title} for {u.name}" onchange={(on) => setApp(a, u, on)} />
                {/if}
              </div></td>
            {/each}
            {#if liveShares.length}
              <td>{#if u.platform_admin}<span class="ah-muted">All, read and write</span>
                {:else}<div class="flex items-center gap-2"><span>{mineText(u)}</span><button class="btn sm ghost" onclick={() => (picking = u)} aria-label="Choose shared folders for {u.name}">Choose</button></div>{/if}</td>
            {/if}
            <td class="ah-muted">{u.last_login_at ? ago(u.last_login_at) : 'Never'}</td>
            <td style="text-align:right;white-space:nowrap">{#if u.email}<button class="btn sm ghost" onclick={() => passwordLink(u)}>Make password link</button>{/if}
              {#if !u.platform_admin}<button class="btn sm danger" onclick={() => removePerson(u)}>Remove from hub</button>{/if}</td></tr>
        {/each}
      </tbody></table>
    </section>
  {:else}<p class="ah-muted">Loading...</p>{/if}
</Page>

{#if editing}
  <div class="ah-dialog-back" role="presentation" onclick={(e) => e.target === e.currentTarget && (editing = null)}>
    <div class="ah-dialog" role="dialog" aria-modal="true" aria-label="Role and permissions">
      <h3 class="ah-card-title">{editing.member.name}</h3>
      <div class="ah-field"><label for="er">Role</label>
        <select id="er" class="input" bind:value={editing.role} onchange={roleChanged}>
          {#each grantable.length ? Array.from(new Set([...grantable, editing.member.role])) : [editing.member.role] as r}<option value={r}>{ROLE_LABEL[r]}: {ROLE_HELP[r]}</option>{/each}
        </select></div>
      {#if editing.role === 'owner'}
        <p class="ah-muted">Owners can do everything.</p>
      {:else}
        <label class="flex items-center gap-2"><input type="checkbox" bind:checked={editing.custom} onchange={roleChanged} /> Choose permissions instead of the role's defaults</label>
        <PermPicker catalog={t.perm_catalog} bind:value={editing.perms} allowed={mine} disabled={!editing.custom} />
      {/if}
      <div class="flex gap-2 justify-end">
        <button class="btn" onclick={() => (editing = null)}>Cancel</button>
        <button class="btn primary" onclick={saveMember}>Save</button>
      </div>
    </div>
  </div>
{/if}

{#if edit}
  <div class="ah-dialog-back" role="presentation" onclick={(e) => e.target === e.currentTarget && (edit = null)}>
    <form class="ah-dialog" role="dialog" aria-modal="true" aria-label="Server folders" onsubmit={(e) => { e.preventDefault(); saveDirs(); }}>
      <h3 class="ah-card-title">Server folders for {edit.name}</h3>
      <div class="ah-field"><label for="dp">DeltaPrints library (scanned every 15 min)</label><input id="dp" class="input" bind:value={edit.deltaprints} placeholder="{data.source_root}/..." /></div>
      <div class="ah-field"><label for="ib">Drop box (imported every 2 min)</label><input id="ib" class="input" bind:value={edit.inbox} placeholder="{data.source_root}/..." /></div>
      <div class="flex gap-2 justify-end"><button type="button" class="btn" onclick={() => (edit = null)}>Cancel</button><button class="btn primary">Save</button></div>
    </form>
  </div>
{/if}

{#if choosing}
  <div class="ah-dialog-back" role="presentation" onclick={(e) => e.target === e.currentTarget && (choosing = null)}>
    <form class="ah-dialog" role="dialog" aria-modal="true" aria-label="Printers for {choosing.member.name}" onsubmit={(e) => { e.preventDefault(); savePrinters(); }}>
      <h3 class="ah-card-title">Printers for {choosing.member.name}</h3>
      <p class="ah-muted">The printers they see and can send to, in the hub, the farm, the phone app and OrcaSlicer. Owners always have every printer.</p>
      <label class="flex items-center gap-2"><input type="radio" name="pall" checked={choosing.all} onchange={() => (choosing.all = true)} /> All printers, including ones added later</label>
      <label class="flex items-center gap-2"><input type="radio" name="pall" checked={!choosing.all} onchange={() => (choosing.all = false)} /> Only the printers chosen below</label>
      <div style="max-height:240px;overflow:auto;padding-left:24px">
        {#each farmPrinters || [] as pr (pr.id)}
          <label class="flex items-center gap-2"><input type="checkbox" disabled={choosing.all} checked={choosing.all || choosing.ids.includes(pr.id)}
            onchange={(e) => togglePrinter(pr.id, e.currentTarget.checked)} /> {pr.name}</label>
        {/each}
      </div>
      {#if !choosing.all && !choosing.ids.length}<p class="ah-muted">With none chosen they see no printers.</p>{/if}
      <div class="flex gap-2 justify-end">
        <button type="button" class="btn ghost" onclick={() => (choosing = null)}>Cancel</button>
        <button type="submit" class="btn primary" disabled={printerBusy}>Save printers</button>
      </div>
    </form>
  </div>
{/if}

{#if picking}
  <div class="ah-dialog-back" role="presentation" onclick={(e) => e.target === e.currentTarget && (picking = null)}>
    <div class="ah-dialog" role="dialog" aria-modal="true" aria-label="Shared folders for {picking.name}">
      <h3 class="ah-card-title">Shared folders for {picking.name}</h3>
      <p class="ah-muted">Takes effect the next time their OrcaSlicer desktop starts.</p>
      {#each liveShares as s (s.slug)}
        <div class="ah-field"><label for="sa-{s.slug}">{s.name}</label>
          <select id="sa-{s.slug}" class="input" value={accessOf(picking, s)} disabled={shareBusy === `${picking.id}:${s.slug}`}
            onchange={(e) => setAccess(picking, s, e.currentTarget.value)}>
            <option value="none">No access</option><option value="read">Read only</option><option value="write">Read and write</option>
          </select></div>
      {/each}
      <div class="flex gap-2 justify-end"><button class="btn primary" onclick={() => (picking = null)}>Done</button></div>
    </div>
  </div>
{/if}

{#if renaming}
  <div class="ah-dialog-back" role="presentation" onclick={(e) => e.target === e.currentTarget && (renaming = null)}>
    <form class="ah-dialog" role="dialog" aria-modal="true" aria-label="Rename shared folder" onsubmit={(e) => { e.preventDefault(); renameShare(); }}>
      <h3 class="ah-card-title">Rename shared folder</h3>
      <div class="ah-field"><label for="sr">Name</label><input id="sr" class="input" maxlength="60" bind:value={renaming.name} /></div>
      <p class="ah-muted">Its files stay. Desktops show the new name the next time they start.</p>
      <div class="flex gap-2 justify-end"><button type="button" class="btn" onclick={() => (renaming = null)}>Cancel</button><button class="btn primary" disabled={!renaming.name.trim()}>Rename</button></div>
    </form>
  </div>
{/if}
