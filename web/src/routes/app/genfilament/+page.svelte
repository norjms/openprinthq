<script>
  import { onMount } from 'svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';

  let mode = $state('light');
  let obs;
  onMount(() => {
    const read = () => { mode = document.documentElement.getAttribute('data-theme') || 'light'; };
    read();
    obs = new MutationObserver(read);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs && obs.disconnect();
  });
  const src = $derived('/genfilament/?embed=1&theme=' + mode);
</script>

<PageTitle page="GenFilament" />

<div class="gf-head">
  <div>
    <h1>GenFilament</h1>
    <p class="muted">AI-generated filament profiles for OrcaSlicer &amp; Bambu Studio — research specs, generate presets, import in one bundle.</p>
  </div>
  <span class="chip ok gf-badge">Paid feature</span>
</div>

<div class="gf-frame">
  <iframe title="GenFilament" src={src} allow="clipboard-write; clipboard-read"></iframe>
</div>
<p class="muted gf-note">Runs inside your private instance. If it doesn't load, GenFilament may still be starting or needs the owner to finish setup.</p>

<style>
  .gf-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
  .gf-head h1 { margin: 0 0 0.2rem; }
  .gf-head p { margin: 0; max-width: 64ch; }
  .gf-badge { flex-shrink: 0; }
  .gf-frame { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); overflow: hidden;
    background: var(--ophq-surface); height: calc(100vh - 210px); min-height: 500px; }
  .gf-frame iframe { width: 100%; height: 100%; border: 0; display: block; }
  .gf-note { font-size: 0.78rem; margin: 0.5rem 0 0; }
</style>
