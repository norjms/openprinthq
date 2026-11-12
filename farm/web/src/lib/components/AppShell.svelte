<script>
  // The signed-in app's frame: a glass sidebar with the grouped navigation, a
  // header (instance, breadcrumb, account) and the page, which scrolls inside
  // its own area so the browser page never does.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import Icon from '$lib/components/AppIcon.svelte';
  import Logo from './Logo.svelte';
  import JumpTo from './JumpTo.svelte';
  import { api } from '$lib/api';
  import { branding, appearance } from '$lib/stores/appearance';
  import { crumbDetail } from '$lib/stores/crumb';
  import { liveFeed } from '$lib/stores/feed';
  import { NAV_ITEMS, GENFILAMENT_ITEM, mergeNav, groupNav, currentItem } from '$lib/nav';

  let { children } = $props();

  // Pages that are not in the navigation still get a breadcrumb.
  const EXTRA_CRUMBS = [
    { prefix: '/app/printers/add', group: 'Farm', label: 'Printers', href: '/app/printers', detail: 'Add a printer' },
    { prefix: '/app/printers/groups', group: 'Farm', label: 'Printers', href: '/app/printers', detail: 'Printer groups' },
    { prefix: '/app/timelapses', group: 'Farm', label: 'Timelapses' },
    { prefix: '/app/instances', group: 'Instance', label: 'Instances' }
  ];

  let features = $state({});
  let instance = $state(null);
  let me = $state(null);
  let stats = $state(null);
  onMount(() => {
    api.myInstance().then((i) => { instance = i; features = i?.features || {}; }).catch(() => {});
    api.me().then((m) => (me = m)).catch(() => {});
    api.stats().then((s) => (stats = s)).catch(() => {});
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
        f.src = '/api/me?ka=' + Date.now();
      }, 15 * 60 * 1000);
    } catch { /* */ }
    function onkey(e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); jumpOpen = !jumpOpen; }
    }
    window.addEventListener('keydown', onkey);
    return () => { if (ka) clearInterval(ka); window.removeEventListener('keydown', onkey); };
  });

  // On phones the sidebar collapses to a top bar with a menu.
  let menuOpen = $state(false);
  let accountOpen = $state(false);
  let jumpOpen = $state(false);

  const builtins = $derived(features.genfilament ? [...NAV_ITEMS, GENFILAMENT_ITEM] : NAV_ITEMS);
  const navItems = $derived(mergeNav(builtins, $appearance?.nav));
  const groups = $derived(groupNav(navItems));

  const path = $derived(page.url.pathname);
  const cur = $derived(currentItem(navItems, path));
  const extra = $derived(EXTRA_CRUMBS.find((c) => path === c.prefix || path.startsWith(c.prefix + '/')));
  const crumbs = $derived.by(() => {
    if (extra) return [extra.group, extra.href ? { label: extra.label, href: extra.href } : extra.label, extra.detail].filter(Boolean);
    if (!cur) return [];
    const isRoot = cur.exact ? true : path === cur.href;
    const detail = isRoot || $crumbDetail.path !== path ? '' : $crumbDetail.label;
    return [cur.group, detail ? { label: cur.label, href: cur.href } : cur.label, detail].filter(Boolean);
  });

  const badges = $derived({
    '/app/printers': stats?.printersTotal || null,
    '/app/queue': stats?.queued || null
  });

  const initials = $derived.by(() => {
    const n = (me?.displayName || me?.email || '').trim();
    if (!n) return '';
    const parts = n.split(/[\s@._-]+/).filter(Boolean);
    return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase();
  });
  const instName = $derived(instance?.subdomain || $branding.siteName);
  const instStatus = $derived(instance?.status ? `Instance, ${instance.status}` : 'Instance');

  $effect(() => { void path; menuOpen = false; accountOpen = false; });
</script>

<div class="ah-root ah-page ah-shell ophq-shell" class:menu-open={menuOpen} class:on-feed={$liveFeed}>
  <div class="ah-shell__side">
    <aside class="ah-glass ah-sidebar">
      <div class="side-head">
        <a href="/app" class="ah-sidebar__brand brand" aria-label="{$branding.siteName} overview"><Logo /></a>
        <button type="button" class="menu-toggle ah-round-btn" aria-label={menuOpen ? 'Close menu' : 'Open menu'}
                aria-expanded={menuOpen} onclick={() => (menuOpen = !menuOpen)}>
          <Icon name={menuOpen ? 'chevron-down' : 'list'} size={16} />
        </button>
      </div>
      <div class="side-body">
        <button type="button" class="ah-input jump" onclick={() => (jumpOpen = true)}>
          <Icon name="search" size={16} />Search or jump to<span class="ah-muted kbd">Ctrl K</span>
        </button>
        <nav class="ah-sidebar__nav" aria-label="{$branding.siteName} navigation">
          {#each groups as g (g.label)}
            <div class="ah-group-label">{g.label}</div>
            {#each g.items as it (it.href)}
              {#if it.external}
                <a class="ah-nav" href={it.href} target="_blank" rel="noopener noreferrer"><Icon name={it.icon} size={17} />{it.label}</a>
              {:else}
                <a class="ah-nav" href={it.href} aria-current={cur?.href === it.href ? 'page' : undefined}>
                  <Icon name={it.icon} size={17} />{it.label}
                  {#if badges[it.href]}<span class="ah-badge">{badges[it.href]}</span>{/if}
                </a>
              {/if}
            {/each}
          {/each}
        </nav>
        <div class="side-foot">
          {#if $branding.trademark}<span class="ah-muted">{$branding.trademark}</span>{/if}
          <a class="ah-muted src" href="/legal">Source code, AGPL-3.0</a>
        </div>
      </div>
    </aside>
  </div>

  <main class="ah-shell__main">
    <header class="ah-header">
      <div class="inst ah-glass">
        <span class="inst-tile" aria-hidden="true">{(instName || 'O').slice(0, 2).toUpperCase()}</span>
        <span class="inst-text"><b>{instName}</b><span class="ah-muted">{instStatus}</span></span>
      </div>
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
      <div class="account">
        <button type="button" class="avatar" aria-haspopup="menu" aria-expanded={accountOpen}
                aria-label="Account menu{me?.displayName ? ', ' + me.displayName : ''}" onclick={() => (accountOpen = !accountOpen)}>
          {#if initials}{initials}{:else}<Icon name="users" size={17} />{/if}
        </button>
        {#if accountOpen}
          <div class="menu" role="menu">
            {#if me?.email}<div class="menu-who"><b>{me.displayName || me.email}</b><span class="ah-muted">{me.email}</span></div>{/if}
            <a role="menuitem" href="/app/settings">Settings</a>
            <a role="menuitem" href="/legal">Source code and license</a>
            <a role="menuitem" href="/logout" data-sveltekit-preload-data="off">Sign out</a>
          </div>
        {/if}
      </div>
    </header>
    <div class="content">
      {@render children()}
    </div>
  </main>
</div>

<JumpTo open={jumpOpen} items={navItems} onclose={() => (jumpOpen = false)} />

<style>
  .ophq-shell { font-size: 14px; position: relative; z-index: 1; }
  /* Over a live feed the page background stays clear and the feed shows through the glass. */
  .ophq-shell.on-feed { background: transparent; }
  .ophq-shell :global(a.ah-nav[aria-current='page'] .ah-badge) { background: rgba(29, 42, 52, 0.12); color: var(--ah-text-on-selected); }
  .side-head { display: flex; align-items: center; gap: 8px; }
  .brand { flex: 1; min-width: 0; padding-bottom: 12px; }
  .menu-toggle { display: none; }
  .side-body { display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 4px; }
  .jump { display: flex; align-items: center; gap: 8px; color: var(--ah-text-3); font-size: 13px; cursor: pointer; text-align: left; }
  .kbd { margin-left: auto; }
  .side-foot { display: flex; flex-direction: column; gap: 4px; padding: 10px 10px 0; border-top: 1px solid var(--ah-row-line); }
  .ophq-shell .src { font-size: 12px; color: var(--ah-text-3); }

  .inst { display: flex; align-items: center; gap: 10px; height: 44px; padding: 0 14px 0 6px; border-radius: 14px; min-width: 0; max-width: 32%; }
  .inst-tile { width: 30px; height: 30px; flex: none; border-radius: 9px; background: var(--ah-accent); color: var(--ah-on-accent); display: grid; place-items: center; font-weight: 700; font-size: 12px; }
  .inst-text { display: flex; flex-direction: column; min-width: 0; line-height: 1.25; }
  .inst-text b { font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ah-header__crumbs { display: flex; justify-content: center; align-items: center; gap: 8px; min-width: 0; white-space: nowrap; }
  .ophq-shell .ah-header__crumbs a { color: var(--ah-text-2); text-decoration: none; }
  .ophq-shell .ah-header__crumbs a:hover { color: var(--ah-text); }
  .sep { font-size: 14px; }

  .account { position: relative; }
  .avatar { width: 40px; height: 40px; border-radius: 20px; border: 1px solid var(--ah-field-line); background: var(--ah-glass); color: var(--ah-text); font: 600 14px var(--ah-font-sans); cursor: pointer; display: grid; place-items: center; }
  .menu { position: absolute; right: 0; top: calc(100% + 8px); z-index: 80; min-width: 240px; display: flex; flex-direction: column; padding: 8px; border-radius: var(--ah-radius-choice); background: var(--ah-solid-panel); border: 1px solid var(--ah-glass-line); }
  .menu-who { display: flex; flex-direction: column; padding: 6px 10px 10px; border-bottom: 1px solid var(--ah-row-line); margin-bottom: 6px; }
  .ophq-shell .menu a { padding: 8px 10px; border-radius: 10px; color: var(--ah-text); text-decoration: none; }
  .ophq-shell .menu a:hover { background: var(--ah-segment); }

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
    .brand { padding-bottom: 0; }
    .side-body { display: none; }
    .ophq-shell.menu-open .side-body { display: flex; padding-top: 10px; }
    .ophq-shell :global(.ah-sidebar) { height: auto; padding: 10px 12px; }
    .inst { display: none; }
    .ah-header__crumbs { justify-content: flex-start; }
  }
</style>
