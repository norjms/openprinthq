<script>
  // Public Light / Dark / Accessible switcher. Uses applyLocal (client-side +
  // cookie), so it works for unauthenticated visitors on the landing page and
  // for signed-in users alike (preserving any custom overrides on the config).
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { appearance, applyLocal } from '$lib/stores/appearance';

  const modes = [
    { id: 'light', label: 'Light', icon: '☀' },
    { id: 'dark', label: 'Dark', icon: '☾' },
    { id: 'accessible', label: 'Accessible', icon: '◉' }
  ];

  // Sync the store to whatever the no-FOUC bootstrap already applied (cookie),
  // so the correct button shows as active on first paint.
  onMount(() => {
    const cur = document.documentElement.getAttribute('data-theme');
    if (cur && modes.some((m) => m.id === cur) && get(appearance).mode !== cur) {
      applyLocal({ ...get(appearance), mode: cur });
    }
  });

  function set(mode) { applyLocal({ ...get(appearance), mode }); }
</script>

<div class="theme-switch" role="group" aria-label="Colour theme">
  {#each modes as m}
    <button
      type="button"
      class="tbtn"
      class:active={$appearance.mode === m.id}
      aria-pressed={$appearance.mode === m.id}
      title={m.label + ' mode'}
      onclick={() => set(m.id)}
    >
      <span aria-hidden="true">{m.icon}</span><span class="lbl">{m.label}</span>
    </button>
  {/each}
</div>

<style>
  .theme-switch { display: inline-flex; gap: 2px; padding: 3px; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-bg-2); }
  .tbtn { display: inline-flex; align-items: center; gap: 0.35rem; padding: 0.34rem 0.55rem; border: 0; background: transparent; color: var(--ophq-text-2); border-radius: calc(var(--radius-sm) - 2px); font-size: 0.82rem; font-weight: 500; cursor: pointer; line-height: 1; }
  .tbtn:hover { color: var(--ophq-text); background: var(--ophq-surface); }
  .tbtn.active { background: var(--ophq-primary-dim); color: var(--ophq-primary-2); }
  @media (max-width: 700px) { .tbtn .lbl { display: none; } }
</style>
