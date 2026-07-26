<script>
  import Logo from './Logo.svelte';
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import { branding } from '$lib/stores/appearance';

  let { children } = $props();

  // Owner-only nav (the Instances admin tab) is hidden entirely for non-owners.
  let isOwner = $state(false);
  onMount(() => { api.me().then((m) => { isOwner = !!m?.isOwner; }).catch(() => {}); });

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
</script>

<div class="shell">
  <aside class="side">
    <a href="/app" class="brand"><Logo size={26} /></a>
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
  </aside>

  <div class="main">
    <div class="content">
      {@render children()}
    </div>
  </div>
</div>

<style>
  .shell { display: grid; grid-template-columns: 244px 1fr; min-height: 100vh; }
  .side { background: var(--ophq-bg-2); border-right: 1px solid var(--ophq-border); padding: 1.1rem 0.9rem; display: flex; flex-direction: column; position: sticky; top: 0; height: 100vh; }
  .brand { padding: 0.3rem 0.5rem 1.2rem; display: block; }
  nav { display: flex; flex-direction: column; gap: 0.15rem; flex: 1; }
  .navitem { display: flex; align-items: center; gap: 0.7rem; padding: 0.6rem 0.7rem; border-radius: var(--radius-sm); color: var(--ophq-text-2); font-size: 0.93rem; font-weight: 500; }
  .navitem:hover { background: var(--ophq-surface); color: var(--ophq-text); }
  .navitem.active { background: var(--ophq-primary-dim); color: var(--ophq-primary-2); box-shadow: inset 2px 0 0 var(--ophq-primary); }
  .logout { margin-top: 0.5rem; color: var(--ophq-muted); }
  .logout:hover { background: color-mix(in srgb, var(--ophq-danger) 12%, transparent); color: var(--ophq-danger); }
  .ic { width: 1.2rem; text-align: center; opacity: 0.9; }
  .side-foot { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.6rem 0.5rem 0.2rem; }
  .tm { font-size: 0.72rem; line-height: 1.35; }
  .foot-links { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
  a.chip.ok { text-decoration: none; }
  .src { font-size: 0.8rem; }

  .main { display: flex; flex-direction: column; }
  .content { padding: 1.8rem; max-width: 1200px; width: 100%; }

  @media (max-width: 820px) { .shell { grid-template-columns: 1fr; } .side { position: static; height: auto; flex-direction: row; flex-wrap: wrap; } nav { flex-direction: row; flex-wrap: wrap; } }
</style>
