<script>
  let { title, blurb, roadmap = [] } = $props();
</script>

<svelte:head><title>{title} · OpenPrintHQ</title></svelte:head>

<div class="head">
  <h1>{title}</h1>
  <span class="chip accent">scaffolded</span>
</div>
<p class="muted lead">{blurb}</p>

<div class="card card-pad">
  <span class="eyebrow">On the roadmap for this panel</span>
  <ul class="road">
    {#each roadmap as r}<li>{r}</li>{/each}
  </ul>
  <p class="muted small">This panel's framework is in place and wired to your instance's API. Functionality is being built in feature by feature.</p>
</div>

<style>
  .head { display: flex; align-items: center; gap: 0.8rem; }
  .head h1 { margin: 0; }
  .lead { margin: 0.4rem 0 1.4rem; max-width: 60ch; }
  .road { margin: 0.8rem 0 1rem; padding-left: 1.1rem; display: grid; gap: 0.4rem; }
  .road li { color: var(--ophq-text-2); }
  .small { font-size: 0.88rem; }
</style>
