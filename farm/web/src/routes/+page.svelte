<script>
  import Header from '$lib/components/Header.svelte';
  import Footer from '$lib/components/Footer.svelte';
  import { branding } from '$lib/stores/appearance';

  const printers = [
    { name: 'Bambu Lab', models: 'X1 · P1 · A1 · H2D', note: 'AMS, HMS, LAN & cloud' },
    { name: 'Creality', models: 'K1 · K2 · Ender', note: 'Klipper & CFS' },
    { name: 'Prusa', models: 'MK4 · XL · CORE One', note: 'PrusaLink / Buddy' },
    { name: 'Snapmaker', models: 'Artisan · J1 · U1', note: 'multi-function' },
    { name: 'Voron', models: '2.4 · Trident · 0', note: 'Klipper / Moonraker' }
  ];

  const features = [
    { icon: '🏢', title: 'Your own private instance', body: 'Every account gets an isolated OpenPrintHQ — its own database, its own printers, its own storage. No shared tenancy, no noisy neighbors.' },
    { icon: '🖨️', title: 'Multi-brand, one HQ', body: 'Bambu Lab, Creality, Prusa, Snapmaker and Voron supported out of the box. One queue, one dashboard, every machine.' },
    { icon: '🧩', title: 'Orca slicing, built in', body: 'Slice STL and 3MF right in the browser with OrcaSlicer — no desktop install. Send straight to any printer in your fleet.' },
    { icon: '🎥', title: 'Live monitoring', body: 'Camera streams, temperatures, progress and health in real time over WebSockets. Catch failures before they waste a spool.' },
    { icon: '🧵', title: 'Filament & spool tracking', body: 'Track spools, usage and cost automatically. Know what you have and what a print really costs.' },
    { icon: '🔒', title: 'Yours to host', body: 'Self-hostable and fully open source under AGPL-3.0. Your data never has to leave your network.' }
  ];

  const steps = [
    { n: '01', t: 'Create your account', d: 'Sign in and we provision a dedicated instance just for you in seconds.' },
    { n: '02', t: 'Add your printers', d: 'Point OpenPrintHQ at your machines — Bambu, Creality, Prusa, Snapmaker or Voron.' },
    { n: '03', t: 'Run your farm', d: 'Queue, slice, monitor and manage everything from one command center.' }
  ];
</script>

<svelte:head><title>{$branding.siteName} — One HQ for every printer</title></svelte:head>

<Header />

<!-- HERO -->
<section class="hero">
  <div class="container">
    <span class="chip primary">Open source · AGPL-3.0 · Self-hostable</span>
    <h1>One command center for<br /><span class="grad">every 3D printer.</span></h1>
    <p class="lead">
      {$branding.siteName} brings Bambu Lab, Creality, Prusa, Snapmaker and Voron into a single private HQ —
      queue, slice, monitor and manage your whole farm from one place. Every user gets their own isolated instance.
    </p>
    <div class="cta">
      <a href="/app" class="btn btn-primary">Launch your HQ →</a>
      <a href="/#how" class="btn btn-ghost">See how it works</a>
    </div>
    <div class="stat-row">
      <div><b>5</b><span>printer brands out of the box</span></div>
      <div><b>1</b><span>isolated instance per user</span></div>
      <div><b>100%</b><span>open source</span></div>
    </div>
  </div>
</section>

<!-- PRINTERS -->
<section id="printers" class="section">
  <div class="container">
    <span class="eyebrow">Supported out of the box</span>
    <h2>Built for the printers you actually run</h2>
    <div class="printer-grid">
      {#each printers as p}
        <div class="card card-pad printer">
          <h3>{p.name}</h3>
          <div class="mono models">{p.models}</div>
          <span class="chip">{p.note}</span>
        </div>
      {/each}
    </div>
    <p class="muted small">More brands are on the roadmap — the engine is vendor-agnostic by design.</p>
  </div>
</section>

<!-- FEATURES -->
<section id="features" class="section">
  <div class="container">
    <span class="eyebrow">Everything in one place</span>
    <h2>A real HQ, not a dashboard bolt-on</h2>
    <div class="feature-grid">
      {#each features as f}
        <div class="card card-pad feature">
          <div class="ficon">{f.icon}</div>
          <h3>{f.title}</h3>
          <p>{f.body}</p>
        </div>
      {/each}
    </div>
  </div>
</section>

<!-- HOW -->
<section id="how" class="section">
  <div class="container">
    <span class="eyebrow">How it works</span>
    <h2>Your own instance in three steps</h2>
    <div class="steps">
      {#each steps as s}
        <div class="step">
          <span class="mono num">{s.n}</span>
          <h3>{s.t}</h3>
          <p>{s.d}</p>
        </div>
      {/each}
    </div>
  </div>
</section>

<!-- OPEN -->
<section id="open" class="section">
  <div class="container open-cta">
    <div class="card card-pad glow">
      <span class="eyebrow">Open by principle</span>
      <h2>Free software. Forever.</h2>
      <p>
        OpenPrintHQ is licensed under the GNU AGPL-3.0. You can read the source, self-host it, audit it and
        improve it. Built on the shoulders of the open 3D-printing community — with full attribution to the
        projects that made it possible.
      </p>
      <div class="cta">
        <a href="/legal" class="btn btn-primary">Read the source</a>
        <a href="/app" class="btn btn-ghost">Launch your HQ</a>
      </div>
    </div>
  </div>
</section>

<Footer />

<style>
  .hero { padding: 5.5rem 0 3rem; text-align: center; }
  .hero .chip { margin-bottom: 1.4rem; }
  .grad { background: linear-gradient(100deg, var(--ophq-primary-2), var(--ophq-accent)); -webkit-background-clip: text; background-clip: text; color: transparent; }
  .lead { font-size: 1.2rem; max-width: 60ch; margin: 1.2rem auto 2rem; color: var(--ophq-text-2); }
  .cta { display: flex; gap: 0.8rem; justify-content: center; flex-wrap: wrap; }
  .stat-row { display: flex; gap: 2.5rem; justify-content: center; margin-top: 3rem; flex-wrap: wrap; }
  .stat-row div { display: flex; flex-direction: column; }
  .stat-row b { font-size: 2rem; color: var(--ophq-text); font-family: var(--font-mono); }
  .stat-row span { color: var(--ophq-muted); font-size: 0.9rem; }

  .section { padding: 3.5rem 0; }
  .section h2 { margin-top: 0.4rem; margin-bottom: 2rem; }
  .small { font-size: 0.9rem; margin-top: 1.2rem; }

  .printer-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 1rem; }
  .printer h3 { margin-bottom: 0.3rem; }
  .printer .models { color: var(--ophq-primary-2); font-size: 0.85rem; margin-bottom: 0.8rem; }

  .feature-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.2rem; }
  .feature .ficon { font-size: 1.8rem; margin-bottom: 0.6rem; }
  .feature h3 { font-size: 1.1rem; }
  .feature p { font-size: 0.95rem; margin: 0; }

  .steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.2rem; }
  .step { padding: 1.4rem; border-left: 2px solid var(--ophq-primary); background: var(--ophq-surface); border-radius: 0 var(--radius) var(--radius) 0; }
  .step .num { color: var(--ophq-accent); font-size: 1.4rem; font-weight: 700; }
  .step h3 { margin: 0.4rem 0 0.3rem; font-size: 1.1rem; }
  .step p { font-size: 0.95rem; margin: 0; }

  .open-cta .glow { box-shadow: var(--shadow-glow); text-align: center; padding: 3rem; }
  .open-cta p { max-width: 62ch; margin: 1rem auto 1.6rem; }

  @media (max-width: 900px) {
    .printer-grid { grid-template-columns: repeat(2, 1fr); }
    .feature-grid, .steps { grid-template-columns: 1fr; }
  }
</style>
