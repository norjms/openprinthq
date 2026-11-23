<script>
  // A craft's own page (docs/crafts.md): what it is, whether it is on, and for Laser Crafting whether the
  // OpenPrintHQ Laser app is installed at /apps/laser.
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.js';
  import Page from '$lib/Page.svelte';
  import { Icon } from 'openprinthq-ui';
  const INFO = {
    laser: { id: 'laser', title: 'Laser Crafting', icon: 'laser',
      text: 'Convert laser projects between Bambu Suite (.lac), xTool Studio (.xcs, .xs), Falcon Design Space (.fds) and SVG, so a model published for one maker’s laser opens in yours.' },
    sublimation: { id: 'sublimation', title: 'Sublimation', icon: 'shirt',
      text: 'Tools for sublimation printing are coming. There is nothing to set up yet.' },
    'graphic-art': { id: 'graphic_art', title: 'Graphic Art', icon: 'palette',
      text: 'Tools for graphic art are coming. There is nothing to set up yet.' }
  };
  const info = $derived(INFO[page.params.craft]);
  let crafts = $state(null);
  let laserApp = $state(null);   // true installed, false not, null not known yet
  onMount(async () => {
    try { crafts = await api.get('/api/crafts/v1'); } catch {}
    if (page.params.craft !== 'laser') return;
    try {
      const r = await fetch('/apps/laser/healthz', { credentials: 'same-origin' });
      laserApp = r.ok && (await r.json()).app === 'openprinthq-laser';
    } catch { laserApp = false; }
  });
  const on = $derived(crafts?.crafts?.find((c) => c.id === info?.id)?.enabled);
</script>

<Page title={info ? info.title : 'Crafts'} description={info ? 'An optional craft. Settings, Crafts chooses which ones this hub shows.' : ''}>
  <section class="ah-glass ah-card">
    {#if !info}
      <p>There is no craft called {page.params.craft}. <a href="/settings?tab=crafts">See the crafts</a>.</p>
    {:else}
      <h2 class="ah-card-title" style="display:flex;align-items:center;gap:10px"><Icon name={info.icon} size={20} />{info.title}</h2>
      <p>{info.text}</p>
      {#if crafts && !on}
        <p class="ah-muted">This craft is off, so the sidebar leaves it out. Turn it on in <a href="/settings?tab=crafts">Settings, Crafts</a>.</p>
      {/if}
      {#if info.id === 'laser'}
        {#if laserApp === true}
          <p><a class="btn primary" href="/apps/laser/" data-sveltekit-reload>Convert files</a></p>
        {:else if laserApp === false}
          <p class="ah-muted">The laser tools are not installed on this hub. Run the OpenPrintHQ installer again and choose Laser Crafting: it adds them at /apps/laser.</p>
        {/if}
      {/if}
    {/if}
  </section>
</Page>
