<script>
  import PanelStub from '$lib/components/PanelStub.svelte';
</script>
<PanelStub title="Slicer"
  blurb="OrcaSlicer, built in. Slice STL and 3MF in the browser and send straight to a printer."
  roadmap={[
    'Server-side OrcaSlicer engine (headless) with job queue',
    'Per-printer + per-filament Orca profiles',
    'Multi-plate handling and G-code preview',
    'Slice → queue in one action'
  ]} />
