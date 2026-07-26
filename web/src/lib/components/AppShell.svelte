<script>
  import Logo from './Logo.svelte';
  import { page } from '$app/state';

  let { children } = $props();

  const nav = [
    { href: '/app', label: 'Overview', icon: '▚' },
    { href: '/app/printers', label: 'Printers', icon: '🖨' },
    { href: '/app/queue', label: 'Print queue', icon: '≣' },
    { href: '/app/files', label: 'Files', icon: '🗀' },
    { href: '/app/slicer', label: 'Slicer', icon: '◈' },
    { href: '/app/filament', label: 'Filament', icon: '🧵' },
    { href: '/app/projects', label: 'Projects', icon: '📁' },
    { href: '/app/statistics', label: 'Statistics', icon: '📈' },
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
          <span class="ic">{item.icon}</span>{item.label}
        </a>
      {/each}
    </nav>
    <div class="side-foot">
      <span class="chip ok">AGPL-3.0</span>
      <a class="muted src" href="/legal">source ↗</a>
    </div>
  </aside>

  <div class="main">
    <header class="topbar">
      <div class="crumb mono">openprinthq / {current.replace('/app', '').replace('/', '') || 'overview'}</div>
      <div class="tb-actions">
        <span class="chip primary">your instance</span>
        <a href="/logout" class="btn btn-ghost btn-sm">Sign out</a>
      </div>
    </header>
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
  .ic { width: 1.2rem; text-align: center; opacity: 0.9; }
  .side-foot { display: flex; align-items: center; justify-content: space-between; padding: 0.6rem 0.5rem 0.2rem; }
  .src { font-size: 0.8rem; }

  .main { display: flex; flex-direction: column; }
  .topbar { height: 60px; border-bottom: 1px solid var(--ophq-border); display: flex; align-items: center; justify-content: space-between; padding: 0 1.6rem; position: sticky; top: 0; background: rgba(13,17,23,0.8); backdrop-filter: blur(10px); z-index: 10; }
  .crumb { color: var(--ophq-muted); font-size: 0.85rem; }
  .tb-actions { display: flex; align-items: center; gap: 0.7rem; }
  .content { padding: 1.8rem; max-width: 1200px; width: 100%; }

  @media (max-width: 820px) { .shell { grid-template-columns: 1fr; } .side { position: static; height: auto; flex-direction: row; flex-wrap: wrap; } nav { flex-direction: row; flex-wrap: wrap; } }
</style>
