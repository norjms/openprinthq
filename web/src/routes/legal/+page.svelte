<script>
  // OpenPrintHQ — public legal / open-source page (AGPL-3.0 §13 source offer).
  // Deliberately reachable WITHOUT login: license, attribution, and the
  // complete corresponding source must be available to every user of the
  // running instance.
  import Header from '$lib/components/Header.svelte';
  import Footer from '$lib/components/Footer.svelte';
  import { branding } from '$lib/stores/appearance';

  // AGPL §13 source offer — the complete corresponding source is published in the
  // public repositories (the app + the print engine). Linking them satisfies the
  // network-use source requirement and stays current with the deployed version.
  const APP_REPO = 'https://git.nnlink.org/OpenPrintHQ/openprinthq';
  const ENGINE_REPO = 'https://git.nnlink.org/OpenPrintHQ/openprinthq-engine';
  // Deployed commit SHAs (injected at build time via vite `define`). When present,
  // link to the exact running version; otherwise fall back to the repo default branch.
  const appCommit = (typeof __OPHQ_APP_COMMIT__ === 'string') ? __OPHQ_APP_COMMIT__ : '';
  const engineCommit = (typeof __OPHQ_ENGINE_COMMIT__ === 'string') ? __OPHQ_ENGINE_COMMIT__ : '';
  const APP_SRC = appCommit ? `${APP_REPO}/src/commit/${appCommit}` : APP_REPO;
  const ENGINE_SRC = engineCommit ? `${ENGINE_REPO}/src/commit/${engineCommit}` : ENGINE_REPO;
  const LICENSE_TXT = APP_REPO + '/src/branch/main/LICENSE';
  const NOTICE_TXT = ENGINE_REPO + '/src/branch/main/NOTICE';
</script>

<svelte:head>
  <title>License, attribution &amp; source · {$branding.siteName}</title>
  <meta name="description" content="OpenPrintHQ is free software under the GNU AGPL v3. License, upstream attribution, and complete corresponding source." />
</svelte:head>

<Header />

<main class="wrap">
  <header class="phead">
    <span class="eyebrow">Open source</span>
    <h1>License, attribution &amp; source</h1>
    <p class="lead">OpenPrintHQ is free software under the GNU Affero General Public License v3.0.
      Everything on this page is public — no account required — because the AGPL guarantees every
      user of this instance access to its license and complete source.</p>
  </header>

  <section id="license" class="card card-pad">
    <h2>License</h2>
    <p>OpenPrintHQ — both the control-plane/web application and its print engine — is licensed under the
      <strong>GNU Affero General Public License, version 3 (or later)</strong>. You may use, study, share,
      and modify it under those terms; if you run a modified version to serve users over a network, you
      must offer them its source.</p>
    <p class="actions">
      <a class="btn btn-ghost" href={LICENSE_TXT}>Read the full AGPL-3.0 text →</a>
    </p>
  </section>

  <section id="attribution" class="card card-pad">
    <h2>Attribution &amp; credits</h2>
    <p>OpenPrintHQ stands on the work of other open-source projects. The main ones:</p>
    <ul class="credits">
      <li><a href="https://github.com/maziggy/bambuddy" rel="noopener">Bambuddy</a>
        (© maziggy and contributors, AGPL-3.0) — our print engine is a derivative fork of it.</li>
      <li><a href="https://github.com/SoftFever/OrcaSlicer" rel="noopener">OrcaSlicer</a>
        (© SoftFever and contributors, AGPL-3.0) — our built-in slicer, itself derived from
        BambuStudio, PrusaSlicer and Slic3r.</li>
      <li><a href="https://github.com/rsms/inter" rel="noopener">Inter</a> (© Rasmus Andersson) and
        <a href="https://github.com/JetBrains/JetBrainsMono" rel="noopener">JetBrains Mono</a>
        (© JetBrains) — the typefaces, under the SIL Open Font License.</li>
      <li>Core frameworks: Svelte/SvelteKit, Fastify and node-postgres (the app), and the FastAPI +
        React stack (the engine), each under its own license.</li>
    </ul>
    <p>Every project's copyright and license is retained in our source, and the complete list of
      dependencies — with versions and licenses — is in the source archive.</p>
    <p class="actions">
      <a class="btn btn-ghost" href={NOTICE_TXT}>Read the full third-party notices →</a>
    </p>
  </section>

  <section id="source" class="card card-pad">
    <h2>Source code</h2>
    <p>In keeping with AGPL-3.0 §13, the <strong>complete corresponding source</strong> for the version of
      OpenPrintHQ running on this instance is public and available to every user, at no charge — both the
      application and the print engine, in their entirety:</p>
    <p class="actions">
      <a class="btn btn-primary" href={APP_SRC} rel="noopener">Application source (web + control-plane) →</a>
      <a class="btn btn-primary" href={ENGINE_SRC} rel="noopener">Print-engine source →</a>
    </p>
    {#if appCommit || engineCommit}
      <p class="fine">Pinned to the exact version running here —
        {#if appCommit}app <span class="mono">{appCommit.slice(0, 10)}</span>{/if}{#if appCommit && engineCommit}, {/if}{#if engineCommit}engine <span class="mono">{engineCommit.slice(0, 10)}</span>{/if}.</p>
    {/if}
    <p class="fine">Public repositories, browsable and cloneable by anyone. Upstream copyright and
      licences are preserved in the history; deployment secrets are never committed.</p>
  </section>
</main>

<Footer />

<style>
  .wrap { max-width: 860px; margin: 0 auto; padding: 3rem 24px 1rem; }
  .phead { margin-bottom: 2rem; }
  .phead h1 { margin: 0.4rem 0 0.6rem; }
  .lead { max-width: 62ch; color: var(--ophq-text-2); }
  section { margin-bottom: 1.2rem; }
  section h2 { font-size: 1.25rem; margin: 0 0 0.6rem; }
  section p { color: var(--ophq-text-2); }
  .credits { color: var(--ophq-text-2); margin: 0.6rem 0 0.9rem; padding-left: 1.1rem; }
  .credits li { margin: 0.35rem 0; }
  .actions { margin: 1rem 0 0; }
  .fine { font-size: 0.85rem; color: var(--ophq-muted); margin-top: 0.6rem; }
  .mono { font-family: var(--font-mono); font-size: 0.82em; color: var(--ophq-text-2); }
</style>
