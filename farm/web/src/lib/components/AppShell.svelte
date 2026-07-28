<script>
  import Logo from './Logo.svelte';
  import ThemeSwitcher from './ThemeSwitcher.svelte';
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import { branding } from '$lib/stores/appearance';

  let { children } = $props();

  // Owner-only nav (the Instances admin tab) is hidden entirely for non-owners.
  let isOwner = $state(false);
  onMount(() => {
    api.me().then((m) => { isOwner = !!m?.isOwner; }).catch(() => {});
    // Session keep-alive: every 15 min, re-run Authentik forward-auth in a hidden
    // iframe so the proxy cookie is refreshed (via the still-valid SSO session)
    // before it expires. Background fetch/XHR can't follow the auth-refresh
    // redirect (cross-origin), but an iframe navigation can — this stops /api
    // (engine) calls from silently dying after the token TTL ("engine unreachable").
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
    return () => { if (ka) clearInterval(ka); };
  });

  // On phones the sidebar collapses to a top bar with a slide-down menu.
  let menuOpen = $state(false);

  const nav = [
    { href: '/app', label: 'Overview', icon: '▚' },
    { href: '/app/printers', label: 'Printers', icon: '🖨' },
    { href: '/app/cameras', label: 'Cameras', icon: '📷' },
    { href: '/app/timelapses', label: 'Timelapses', icon: '🎞' },
    { href: '/app/queue', label: 'Print queue', icon: '≣' },
    { href: '/app/files', label: 'Files', icon: '🗀' },
    { href: '/app/slicer', label: 'Slicer', icon: '◈' },
    { href: '/app/filament', label: 'Filament', icon: '🧵' },
    { href: '/app/projects', label: 'Projects', icon: '📁' },
    { href: '/app/statistics', label: 'Statistics', icon: '📈' },
    { href: '/app/reports', label: 'Reports', icon: '🧾' },
    { href: '/app/settings', label: 'Settings', icon: '⚙' }
  ];

  const current = $derived(page.url.pathname);
  // Close the mobile menu whenever the route changes (i.e. after tapping a link).
  $effect(() => { void current; menuOpen = false; });
</script>

<div class="shell" class:menu-open={menuOpen}>
  <aside class="side">
    <div class="side-head">
      <a href="/app" class="brand"><Logo size={26} /></a>
      <button
        type="button"
        class="menu-toggle"
        aria-label={menuOpen ? 'Close menu' : 'Open menu'}
        aria-expanded={menuOpen}
        onclick={() => (menuOpen = !menuOpen)}
      >
        <span class="mt-icon" aria-hidden="true">{menuOpen ? '✕' : '☰'}</span>
        <span class="mt-label">Menu</span>
      </button>
    </div>

    <div class="side-body">
      <nav>
        {#each nav as item}
          <a href={item.href}
             class="navitem"
             class:active={item.href === '/app' ? current === '/app' : current.startsWith(item.href)}>
            <span class="ic" aria-hidden="true">{item.icon}</span>{item.label}
          </a>
        {/each}
        {#if isOwner}
          <a href="/app/instances" class="navitem" class:active={current.startsWith('/app/instances')}>
            <span class="ic" aria-hidden="true">🛰</span>Instances
          </a>
        {/if}
      </nav>
      <a href="/logout" class="navitem logout" data-sveltekit-preload-data="off">
        <span class="ic" aria-hidden="true">⎋</span>Sign out
      </a>
      <div class="side-foot">
        {#if $branding.trademark}<span class="tm muted">{$branding.trademark}</span>{/if}
        <div class="foot-links">
          <a class="chip ok" href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noopener license">AGPL-3.0</a>
          <a class="muted src" href="/legal">source ↗</a>
        </div>
      </div>
    </div>
  </aside>

  <div class="main">
    <div class="appbar"><ThemeSwitcher /></div>
    <div class="content">
      {@render children()}
    </div>
  </div>
</div>

<style>
  .shell { display: grid; grid-template-columns: 244px 1fr; min-height: 100vh; }
  .side { background: var(--ophq-bg-2); border-right: 1px solid var(--ophq-border); padding: 1.1rem 0.9rem; display: flex; flex-direction: column; position: sticky; top: 0; height: 100vh; }
  .side-head { display: flex; align-items: center; justify-content: center; }
  .brand { flex: 1; padding: 0.3rem 0.5rem 1.2rem; display: block; text-align: center; }
  /* Hamburger: hidden on desktop, shown in the mobile top bar. */
  .menu-toggle { display: none; align-items: center; gap: 0.4rem; background: transparent; border: 1px solid var(--ophq-border); color: var(--ophq-text-2); border-radius: var(--radius-sm); padding: 0.4rem 0.6rem; font-size: 0.9rem; font-weight: 600; cursor: pointer; }
  .menu-toggle:hover { color: var(--ophq-text); background: var(--ophq-surface); }
  .menu-toggle .mt-icon { font-size: 1.1rem; line-height: 1; width: 1.1rem; text-align: center; }
  .side-body { display: flex; flex-direction: column; flex: 1; min-height: 0; }
  nav { display: flex; flex-direction: column; gap: 0.15rem; flex: 1; }
  .navitem { display: flex; align-items: center; gap: 0.7rem; padding: 0.6rem 0.7rem; border-radius: var(--radius-sm); color: var(--ophq-text-2); font-size: 0.93rem; font-weight: 500; }
  .navitem:hover { background: var(--ophq-surface); color: var(--ophq-text); }
  .navitem.active { background: var(--ophq-primary-dim); color: var(--ophq-primary-2); box-shadow: inset 2px 0 0 var(--ophq-primary); }
  /* Theme control anchored at the top of the content area (like the landing header). */
  .appbar { position: sticky; top: 0; z-index: 30; display: flex; align-items: center; justify-content: flex-end; gap: 0.6rem; height: 56px; padding: 0 1.8rem; background: var(--ophq-glass); backdrop-filter: blur(12px); border-bottom: 1px solid var(--ophq-border-soft); }
  .logout { margin-top: 0.3rem; color: var(--ophq-muted); }
  .logout:hover { background: color-mix(in srgb, var(--ophq-danger) 12%, transparent); color: var(--ophq-danger); }
  .ic { width: 1.2rem; text-align: center; opacity: 0.9; }
  .side-foot { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.6rem 0.5rem 0.2rem; }
  .tm { font-size: 0.72rem; line-height: 1.35; }
  .foot-links { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
  a.chip.ok { text-decoration: none; }
  .src { font-size: 0.8rem; }

  .main { display: flex; flex-direction: column; }
  .content { padding: 1.8rem; max-width: 1200px; width: 100%; }

  /* ---- Phone layout: the sidebar becomes a sticky top bar (logo + hamburger);
         the nav slides down as a menu only when opened, instead of the vertical
         nav being force-reflowed into a broken wrapping row. ---- */
  @media (max-width: 820px) {
    .shell { grid-template-columns: 1fr; }
    .side { position: sticky; top: 0; z-index: 40; height: auto; padding: 0.55rem 0.9rem; border-right: 0; border-bottom: 1px solid var(--ophq-border); }
    .side-head { justify-content: space-between; }
    .brand { flex: 0 1 auto; padding: 0.15rem 0.3rem; text-align: left; }
    .menu-toggle { display: inline-flex; }
    .side-body { display: none; }
    .shell.menu-open .side-body { display: flex; padding-top: 0.7rem; max-height: calc(100vh - 60px); overflow-y: auto; }
    .shell.menu-open nav { flex: 0 0 auto; }
    .navitem { padding: 0.7rem 0.7rem; font-size: 1rem; }
    .content { padding: 1.1rem; }
    /* On phones the sidebar is already a sticky top bar; keep the theme strip in
       normal flow (right-aligned) so two sticky bars don't overlap. */
    .appbar { position: static; height: auto; padding: 0.5rem 1.1rem; }
  }
</style>
