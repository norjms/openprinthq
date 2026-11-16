<script>
  import '../app.css';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { AppShell, Sidebar, Icon } from 'artisanhub-ui';
  import { api } from '$lib/api.js';
  import { toasts } from '$lib/toast.svelte.js';
  import { session, loadMe, ROLE_LABEL } from '$lib/session.svelte.js';
  import { initTheme } from '$lib/theme.svelte.js';
  import { shell } from '$lib/shell.svelte.js';
  import { nav, loadNav, isCurrent, where } from '$lib/nav.svelte.js';

  let { children } = $props();
  const me = $derived(session.me);
  const bare = $derived(page.url.pathname.startsWith('/invite/') || page.url.pathname === '/setup');
  let stats = $state(null);
  let userMenu = $state(false);
  let bell = $state(false);
  let jump = $state(false);
  let jq = $state('');
  let jumpInput = $state();

  // Counts from /api/stats, by the name nav.json gives each badge.
  function count(key) {
    const v = stats?.[key];
    return Array.isArray(v) ? v.length : v || 0;
  }
  const groups = $derived(nav.groups.map((g) => ({
    label: g.label,
    items: g.items.filter((it) => !it.perm || me?.perms?.includes(it.perm)).map((it) => ({
      label: it.label, href: it.href, icon: it.icon, current: isCurrent(it, page.url),
      badge: it.badge && count(it.badge) ? count(it.badge) : undefined
    }))
  })));
  const here = $derived(where(page.url));
  const name = $derived(nav.installation || me?.team?.name || 'ArtisanHub');
  const initials = $derived((me?.user?.name || me?.user?.username || '?').split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase());
  // What needs someone's attention, for the bell: the same counts the sidebar badges carry, with where to go.
  const alerts = $derived([
    [count('alerts_open'), 'alerts open', '/alerts'],
    [count('plate_checks'), 'plate checks waiting', '/farm'],
    [count('jobs_failed'), 'failed sync jobs', '/sync'],
    [count('quotes_waiting'), 'quotes waiting for a reply', '/quotes'],
    [count('licenses_attention'), 'licenses need attention', '/licenses'],
    [count('live_out_of_supplies'), 'live products out of supplies', '/planner']
  ].filter((a) => a[0]));
  const jumpItems = $derived(groups.flatMap((g) => g.items.map((i) => ({ ...i, group: g.label })))
    .filter((i) => !jq || i.label.toLowerCase().includes(jq.toLowerCase())));

  async function refresh() {
    if (bare) return;
    try { stats = await api.get('/api/stats'); } catch {}
  }
  function openJump() { jump = true; jq = ''; setTimeout(() => jumpInput?.focus(), 0); }
  function go(href) {
    jump = false;
    if (/^https?:/.test(href)) location.href = href;
    else goto(href);
  }
  function jumpSubmit(e) {
    e.preventDefault();
    if (jumpItems.length && jq) go(jumpItems[0].href);
    else if (jq) go(`/?q=${encodeURIComponent(jq)}`);
  }
  function keys(e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && !bare) { e.preventDefault(); openJump(); }
    if (e.key === 'Escape') { jump = false; userMenu = false; bell = false; }
  }
  onMount(() => {
    initTheme();
    if (!bare) {
      loadMe().then(() => initTheme(session.me?.prefs));
      loadNav();
    }
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  });
</script>

<svelte:window onkeydown={keys} />

{#if bare}
  <div class="ah-root ah-page ah-bare">{@render children()}</div>
{:else if session.noTeam}
  <div class="ah-root ah-page ah-bare">
    <section class="ah-glass ah-card" style="max-width:460px;width:100%">
      <h1 class="ah-title">You're not in a team yet</h1>
      <p class="ah-secondary">Open the invite link you were sent to join a team. If you think you should have access, ask the person who invited you.</p>
      <a class="btn" style="align-self:flex-start" href="/auth/logout">Sign out</a>
    </section>
  </div>
{:else}
  <AppShell title={shell.title} description={shell.description} filters={shell.actions ?? undefined} widgets={shell.widgets ?? undefined}>
    {#snippet sidebar()}
      <Sidebar product="ArtisanHub" {groups} onsearch={openJump} />
    {/snippet}
    {#snippet header()}
      <div class="ah-header__name" title="This installation">{name}</div>
      <nav class="ah-header__crumbs" aria-label="Breadcrumb">
        {#if here}<span>{here.group}</span> <span class="ah-muted" style="font-size:14px">/</span>
          {#if shell.crumb}<a class="plain" href={here.item.href}>{here.item.label}</a> <span class="ah-muted" style="font-size:14px">/</span> <b>{shell.crumb}</b>{:else}<b>{here.item.label}</b>{/if}
        {:else}<b>{shell.crumb || shell.title}</b>{/if}
      </nav>
      <div style="position:relative">
        <button class="ah-round-btn" style="width:38px;height:38px;margin:0" aria-label="Notifications" aria-expanded={bell}
          onclick={() => { bell = !bell; userMenu = false; }}><Icon name="bell" size={17} />{#if alerts.length}<span class="ah-badge" style="position:absolute;top:-4px;right:-6px;margin:0">{alerts.reduce((a, b) => a + b[0], 0)}</span>{/if}</button>
        {#if bell}
          <div class="ah-glass ah-menu" role="menu">
            {#each alerts as [n, label, href]}<a {href} onclick={() => (bell = false)}><span class="chip warn">{n}</span>{label}</a>
            {:else}<span class="ah-muted" style="padding:8px 10px">Nothing needs you right now.</span>{/each}
            <a href="/alerts" onclick={() => (bell = false)}>All alerts</a>
          </div>
        {/if}
      </div>
      {#if me?.user}
        <div style="position:relative">
          <button class="ah-avatar" aria-label="Account" aria-expanded={userMenu} onclick={() => { userMenu = !userMenu; bell = false; }}>{initials}</button>
          {#if userMenu}
            <div class="ah-glass ah-menu" role="menu">
              <div style="padding:6px 10px"><b>{me.user.name || me.user.username}</b><div class="ah-muted">{ROLE_LABEL[me.team?.role] || 'API key'}{me.user.email ? `, ${me.user.email}` : ''}</div></div>
              <a href="/settings" onclick={() => (userMenu = false)}><Icon name="cog" size={16} />Appearance and settings</a>
              <!-- B35: the one password for everything signed in to with ArtisanHub, changed on the hub's own page. -->
              <a href="/account/password/change" data-sveltekit-reload>Change password</a>
              {#if me.links && Object.values(me.links).some(Boolean)}
                <div class="ah-group-label" style="margin:10px 10px 4px">Systems</div>
                {#each [['models', 'Models'], ['woo', 'WooCommerce'], ['farm', 'ArtisanHub Farm'], ['dam', 'DAM'], ['manyfold', 'Manyfold'], ['inventree', 'InvenTree'], ['spoolman', 'Spoolman']] as [k, l]}
                  {#if me.links[k]}<a target="_blank" rel="noopener" href={me.links[k]}>{l}</a>{/if}
                {/each}
              {/if}
              <a href="/auth/logout" style="margin-top:6px;border-top:1px solid var(--ah-row-line)">Sign out</a>
            </div>
          {/if}
        </div>
      {/if}
    {/snippet}
    {#if me?.team?.role === 'viewer'}
      <div class="ah-banner ah-banner--live" style="flex:none">You have view access to {me.team.name}. Ask an admin if you need to make changes.</div>
    {/if}
    {@render children()}
  </AppShell>
{/if}

{#if jump}
  <div class="ah-dialog-back" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) jump = false; }}>
    <form class="ah-dialog" role="dialog" aria-modal="true" aria-label="Search or jump to" onsubmit={jumpSubmit}>
      <input class="input" bind:this={jumpInput} bind:value={jq} placeholder="Type a page, or search models" aria-label="Search or jump to" />
      <div style="display:flex;flex-direction:column;gap:2px;max-height:50vh;overflow:auto">
        {#each jumpItems as it (it.href)}
          <button type="button" class="ah-nav" style="border:0;background:transparent;cursor:pointer;text-align:left" onclick={() => go(it.href)}><Icon name={it.icon} size={17} />{it.label}<span class="ah-muted" style="margin-left:auto">{it.group}</span></button>
        {/each}
        {#if jq}<button type="submit" class="ah-nav" style="border:0;background:transparent;cursor:pointer;text-align:left"><Icon name="search" size={17} />Search models for "{jq}"</button>{/if}
      </div>
    </form>
  </div>
{/if}

<div class="ah-toasts" aria-live="polite">
  {#each toasts.items as t (t.id)}
    <div class="ah-banner {t.kind === 'bad' ? 'ah-banner--bad' : t.kind === 'warn' ? '' : 'ah-banner--live'}" style="background-color:var(--ah-solid-panel)">{t.message}</div>
  {/each}
</div>
