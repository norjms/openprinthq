<script>
  const vendors = [
    { key: 'bambu', name: 'Bambu Lab', hint: 'Serial + access code (LAN) or cloud' },
    { key: 'klipper', name: 'Klipper / Voron', hint: 'Moonraker URL' },
    { key: 'octoprint', name: 'OctoPrint', hint: 'URL + API key' },
    { key: 'prusa', name: 'Prusa', hint: 'PrusaLink URL + key' },
    { key: 'snapmaker', name: 'Snapmaker', hint: 'LAN IP' }
  ];
</script>

<svelte:head><title>Add printer · OpenPrintHQ</title></svelte:head>

<div class="head">
  <h1>Add a printer</h1>
  <a href="/app/printers" class="btn btn-ghost btn-sm">← Back</a>
</div>
<p class="muted lead">Pick your printer's platform. OpenPrintHQ connects to it through your private engine — nothing leaves your network.</p>

<div class="grid vend">
  {#each vendors as v}
    <button class="card card-pad vendor" type="button">
      <h3>{v.name}</h3>
      <span class="muted">{v.hint}</span>
    </button>
  {/each}
</div>

<p class="muted small">The per-vendor connection forms wire into the engine's add-printer API next — the adapters (Bambu MQTT/FTP, Moonraker, PrusaLink, Snapmaker, Duet, FlashForge, MKS, Obico) are already in the engine.</p>

<style>
  .head { display: flex; align-items: center; justify-content: space-between; }
  .head h1 { margin: 0; }
  .lead { margin: 0.4rem 0 1.4rem; max-width: 58ch; }
  .vend { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
  .vendor { text-align: left; cursor: pointer; border-color: var(--ophq-border); transition: border 0.15s, transform 0.15s; }
  .vendor:hover { border-color: var(--ophq-primary); transform: translateY(-2px); }
  .vendor h3 { margin: 0 0 0.3rem; font-size: 1.05rem; }
  .small { margin-top: 1.4rem; font-size: 0.85rem; }
</style>
