<script>
  // The signed-in app's frame: the hub's glass sidebar (the hub's own navigation,
  // with the farm's pages inside it), a header (installation, breadcrumb, alerts,
  // account) and the page, which scrolls inside its own area so the browser page
  // never does.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { base as basePath } from '$app/paths';
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import Icon from '$lib/components/AppIcon.svelte';
  import JumpTo from './JumpTo.svelte';
  import { api } from '$lib/api';
  import { branding } from '$lib/stores/appearance';
  import { crumbDetail } from '$lib/stores/crumb';
  import { liveFeed } from '$lib/stores/feed';
  import { fallbackNav, loadHubNav, farmGroups, farmCurrent, FARM_PAGES, GENFILAMENT_PAGE } from '$lib/hubnav';

  let { children } = $props();

  // Pages under a sidebar item that are not in the navigation themselves.
  const SUB_PAGES = [
    { prefix: '/app/printers/add', parent: '/app/printers', detail: 'Add a printer' },
    { prefix: '/app/printers/groups', parent: '/app/printers', detail: 'Printer groups' }
  ];

  let nav = $state(fallbackNav());
  let features = $state({});
  let me = $state(null);
  let stats = $state(null);       // the farm's counts
  let hubStats = $state(null);    // the hub's counts, by the names its list gives each badge
  onMount(() => {
    loadHubNav().then((d) => (nav = d));
    api.myInstance().then((i) => { features = i?.features || {}; }).catch(() => {});
    api.me().then((m) => (me = m)).catch(() => {});
    api.stats().then((s) => (stats = s)).catch(() => {});
    fetch('/api/stats', { credentials: 'include', headers: { accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null)).then((s) => (hubStats = s)).catch(() => {});
    // Session keep-alive: every 15 min, re-run forward-auth in a hidden iframe so
    // the proxy cookie is refreshed (through the still-valid SSO session) before
    // it expires. Background fetches cannot follow the sign-in redirect, but an
    // iframe navigation can, so /api calls do not silently stop after the TTL.
    let ka;
    try {
      ka = setInterval(() => {
        let f = document.getElementById('ophq-keepalive');
        if (!f) {
          f = document.createElement('iframe');
          f.id = 'ophq-keepalive';
          f.setAttribute('aria-hidden', 'true');
          f.tabIndex = -1;
          f.style.cssText = 'position:absolute;left:-9999px;width:0;height:0;border:0';
          document.body.appendChild(f);
        }
        f.src = basePath + '/api/me?ka=' + Date.now();
      }, 15 * 60 * 1000);
    } catch { /* */ }
    function onkey(e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); jumpOpen = !jumpOpen; }
      if (e.key === 'Escape') { accountOpen = false; bellOpen = false; }
    }
    window.addEventListener('keydown', onkey);
    return () => { if (ka) clearInterval(ka); window.removeEventListener('keydown', onkey); };
  });

  // On phones the sidebar collapses to a top bar with a menu.
  let menuOpen = $state(false);
  let accountOpen = $state(false);
  let bellOpen = $state(false);
  let jumpOpen = $state(false);

  const groups = $derived(farmGroups(nav));
  const extraPages = $derived(features.genfilament ? [GENFILAMENT_PAGE] : []);

  // The path inside the farm, without the base path.
  const path = $derived(page.url.pathname.slice(basePath.length) || '/');
  const sub = $derived(SUB_PAGES.find((c) => path === c.prefix || path.startsWith(c.prefix + '/')));
  const here = $derived(farmCurrent(groups, sub ? sub.parent : path, extraPages));
  const crumbs = $derived.by(() => {
    if (!here) return [];
    const { group, item } = here;
    const first = group || 'Farm';
    if (!group && item.exact) return [first];
    const detail = sub ? sub.detail : (path !== item.path && $crumbDetail.path === page.url.pathname ? $crumbDetail.label : '');
    return [first, detail ? { label: item.label, href: item.href } : item.label, detail].filter(Boolean);
  });

  function count(key) {
    const v = hubStats?.[key];
    return Array.isArray(v) ? v.length : v || 0;
  }
  function badgeFor(it) {
    if (it.id === 'printers') return stats?.printersTotal || null;
    if (it.id === 'queue') return stats?.queued || null;
    return it.badge && count(it.badge) ? count(it.badge) : null;
  }
  // What needs someone's attention, for the bell: the hub's counts, with where to go.
  const alerts = $derived([
    [count('plate_checks'), 'plate checks waiting', '/farm'],
    [count('jobs_failed'), 'failed sync jobs', '/sync'],
    [count('quotes_waiting'), 'quotes waiting for a reply', '/quotes'],
    [count('licenses_attention'), 'licenses need attention', '/licenses'],
    [count('live_out_of_supplies'), 'live products out of supplies', '/planner']
  ].filter((a) => a[0]));
  const alertTotal = $derived(alerts.reduce((a, b) => a + b[0], 0));

  // Everything the palette can jump to: the sidebar, then the farm's own pages.
  const jumpItems = $derived([
    ...groups.flatMap((g) => g.items.map((i) => ({ href: i.href, label: i.label, icon: i.icon, group: g.label, reload: i.reload }))),
    ...[...FARM_PAGES, ...extraPages].filter((p) => !p.hidden).map((p) => ({ href: basePath + p.path, label: p.label, icon: p.icon, group: 'Farm' }))
  ]);

  const initials = $derived.by(() => {
    const n = (me?.displayName || me?.email || '').trim();
    if (!n) return '';
    const parts = n.split(/[\s@._-]+/).filter(Boolean);
    return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase();
  });
  const instName = $derived(nav.installation || 'ArtisanHub');
  // Signed in through the hub, signing out is the hub's; without it, the farm's own.
  const signOut = $derived(nav.fromHub ? '/auth/logout' : basePath + '/logout');

  $effect(() => { void path; menuOpen = false; accountOpen = false; bellOpen = false; });
</script>

<div class="ah-root ah-page ah-shell ophq-shell" class:menu-open={menuOpen} class:on-feed={$liveFeed}>
  <div class="ah-shell__side">
    <aside class="ah-glass ah-sidebar">
      <div class="side-head">
        <a href="/" class="ah-sidebar__brand brand" data-sveltekit-reload aria-label="ArtisanHub home">
          <span class="ah-icon-tile brand-tile" aria-hidden="true"><Icon name="cube" size={18} /></span>ArtisanHub
        </a>
        <button type="button" class="menu-toggle ah-round-btn" aria-label={menuOpen ? 'Close menu' : 'Open menu'}
                aria-expanded={menuOpen} onclick={() => (menuOpen = !menuOpen)}>
          <Icon name={menuOpen ? 'chevron-down' : 'list'} size={16} />
        </button>
      </div>
      <div class="side-body">
        <button type="button" class="ah-input jump" onclick={() => (jumpOpen = true)}>
          <Icon name="search" size={16} />Search or jump to<span class="ah-muted kbd">Ctrl K</span>
        </button>
        <nav class="ah-sidebar__nav" aria-label="ArtisanHub navigation">
          {#each groups as g (g.label)}
            <div class="ah-group-label">{g.label}</div>
            {#each g.items as it (it.id || it.href)}
              {@const b = badgeFor(it)}
              {#if it.farm}
                <a class="ah-nav" href={it.href} aria-current={here?.item?.path === it.path ? 'page' : undefined}>
                  <Icon name={it.icon} size={17} />{it.label}{#if b}<span class="ah-badge">{b}</span>{/if}
                </a>
              {:else}
                <a class="ah-nav" href={it.href} data-sveltekit-reload>
                  <Icon name={it.icon} size={17} />{it.label}{#if b}<span class="ah-badge">{b}</span>{/if}
                </a>
              {/if}
            {/each}
          {/each}
        </nav>
        <div class="side-foot">
          {#if $branding.trademark}<span class="ah-muted">{$branding.trademark}</span>{/if}
          <a class="ah-muted src" href="{basePath}/legal">ArtisanHub Farm source, AGPL-3.0</a>
        </div>
      </div>
    </aside>
  </div>

  <main class="ah-shell__main">
    <header class="ah-header">
      <div class="inst ah-glass" title="This installation">{instName}</div>
      <nav class="ah-header__crumbs" aria-label="Breadcrumb">
        {#each crumbs as c, i}
          {#if i}<span class="ah-muted sep" aria-hidden="true">/</span>{/if}
          {#if i === crumbs.length - 1}
            <b aria-current="page">{typeof c === 'string' ? c : c.label}</b>
          {:else if typeof c === 'string'}
            <span>{c}</span>
          {:else}
            <a href={c.href}>{c.label}</a>
          {/if}
        {/each}
      </nav>
      <div class="hright">
        <div class="pop">
          <button type="button" class="ah-round-btn bell" aria-label="Notifications" aria-haspopup="menu" aria-expanded={bellOpen}
                  onclick={() => { bellOpen = !bellOpen; accountOpen = false; }}>
            <Icon name="bell" size={17} />{#if alertTotal}<span class="ah-badge bell-n">{alertTotal}</span>{/if}
          </button>
          {#if bellOpen}
            <div class="menu" role="menu">
              {#each alerts as [n, label, href]}
                <a role="menuitem" {href} data-sveltekit-reload><span class="chip warn">{n}</span>{label}</a>
              {:else}
                <span class="ah-muted none">Nothing needs you right now.</span>
              {/each}
            </div>
          {/if}
        </div>
        <div class="pop">
          <button type="button" class="avatar" aria-haspopup="menu" aria-expanded={accountOpen}
                  aria-label="Account menu{me?.displayName ? ', ' + me.displayName : ''}" onclick={() => { accountOpen = !accountOpen; bellOpen = false; }}>
            {#if initials}{initials}{:else}<Icon name="users" size={17} />{/if}
          </button>
          {#if accountOpen}
            <div class="menu" role="menu">
              {#if me?.email}<div class="menu-who"><b>{me.displayName || me.email}</b><span class="ah-muted">{me.email}</span></div>{/if}
              <a role="menuitem" href="{basePath}/app/settings"><Icon name="cog" size={16} />Farm settings</a>
              <a role="menuitem" href="{basePath}/app/statistics"><Icon name="chart" size={16} />Farm statistics</a>
              <a role="menuitem" href="{basePath}/legal"><Icon name="file-text" size={16} />Source code and license</a>
              <a role="menuitem" class="out" href={signOut} data-sveltekit-reload data-sveltekit-preload-data="off">Sign out</a>
            </div>
          {/if}
        </div>
      </div>
    </header>
    <div class="content">
      {@render children()}
    </div>
  </main>
</div>

<JumpTo open={jumpOpen} items={jumpItems} onclose={() => (jumpOpen = false)} />

<style>
  .ophq-shell { font-size: 14px; position: relative; z-index: 1; }
  /* Over a live feed the page background stays clear and the feed shows through the glass. */
  .ophq-shell.on-feed { background: transparent; }
  .ophq-shell :global(a.ah-nav[aria-current='page'] .ah-badge) { background: rgba(29, 42, 52, 0.12); color: var(--ah-text-on-selected); }
  .side-head { display: flex; align-items: center; gap: 8px; }
  .brand { flex: 1; min-width: 0; text-decoration: none; color: var(--ah-text); }
  .brand-tile { background: var(--ah-accent); color: var(--ah-on-accent); }
  .menu-toggle { display: none; }
  .side-body { display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 4px; }
  .jump { display: flex; align-items: center; gap: 8px; color: var(--ah-text-3); font-size: 13px; cursor: pointer; text-align: left; }
  .kbd { margin-left: auto; }
  .side-foot { display: flex; flex-direction: column; gap: 4px; padding: 10px 10px 0; border-top: 1px solid var(--ah-row-line); }
  .ophq-shell .src { font-size: 12px; color: var(--ah-text-3); }

  .inst { display: flex; align-items: center; height: 38px; padding: 0 14px; border-radius: 12px; min-width: 0; max-width: 30%;
          font-family: var(--ah-font-display); font-weight: 500; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ah-header__crumbs { display: flex; justify-content: center; align-items: center; gap: 8px; min-width: 0; white-space: nowrap; }
  .ophq-shell .ah-header__crumbs a { color: var(--ah-text-2); text-decoration: none; }
  .ophq-shell .ah-header__crumbs a:hover { color: var(--ah-text); }
  .sep { font-size: 14px; }

  .hright { display: flex; align-items: center; gap: 12px; }
  .pop { position: relative; }
  .bell { width: 38px; height: 38px; margin: 0; position: relative; }
  .bell-n { position: absolute; top: -4px; right: -6px; margin: 0; }
  .avatar { width: 38px; height: 38px; border-radius: 50%; border: 0; background: var(--ah-accent); color: var(--ah-on-accent); font: 700 13px var(--ah-font-sans); cursor: pointer; display: grid; place-items: center; }
  .menu { position: absolute; right: 0; top: calc(100% + 8px); z-index: 80; min-width: 260px; display: flex; flex-direction: column; padding: 8px; border-radius: var(--ah-radius-choice); background: var(--ah-solid-panel); border: 1px solid var(--ah-glass-line); }
  .menu-who { display: flex; flex-direction: column; padding: 6px 10px 10px; border-bottom: 1px solid var(--ah-row-line); margin-bottom: 6px; }
  .ophq-shell .menu a { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 10px; color: var(--ah-text); text-decoration: none; }
  .ophq-shell .menu a:hover { background: var(--ah-segment); }
  .ophq-shell .menu a.out { margin-top: 6px; border-top: 1px solid var(--ah-row-line); border-radius: 0 0 10px 10px; }
  .none { padding: 8px 10px; }

  .content { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 14px; padding: 2px 4px 4px 2px; }
  /* Page sections keep their height and the page area scrolls; a section marked
     .fill takes the rest of the height and scrolls inside itself instead. */
  .content > :global(*) { flex-shrink: 0; }
  .content > :global(.fill) { flex: 1 1 auto; flex-shrink: 1; min-height: 0; }

  @media (max-width: 1099px) {
    .ophq-shell { grid-template-columns: 1fr; padding: 10px; }
    .content { overflow: visible; }
  }
  /* Phones: the sidebar becomes a bar with a menu button. */
  @media (max-width: 820px) {
    .menu-toggle { display: grid; margin: 0; }
    .side-body { display: none; }
    .ophq-shell.menu-open .side-body { display: flex; padding-top: 10px; }
    .ophq-shell :global(.ah-sidebar) { height: auto; padding: 10px 12px; }
    .inst { display: none; }
    .ah-header__crumbs { justify-content: flex-start; }
  }
</style>
