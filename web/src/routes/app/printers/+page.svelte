<script>
  import PanelStub from '$lib/components/PanelStub.svelte';
</script>
<PanelStub title="Printers"
  blurb="Connect and control your Bambu Lab, Creality, Prusa, Snapmaker and Voron machines from one place."
  roadmap={[
    'Add-printer flow with per-brand adapters (Bambu MQTT/FTP, Klipper/Moonraker, PrusaLink, Snapmaker)',
    'Live status cards: temps, progress, camera, HMS/health',
    'Direct controls: start/pause/stop, jog, temps, fans, lights',
    'Per-printer AMS / material state'
  ]} />
