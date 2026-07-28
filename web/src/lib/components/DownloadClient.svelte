<script>
  // Download + install instructions for the OpenPrintHQ Cloud Client — the
  // outbound-only connector that bridges this cloud instance to printers on a
  // private LAN. Buttons point at the public release page; Docker runs inline.
  const CLIENT_REPO = 'https://git.nnlink.org/OpenPrintHQ/openprinthq-cloud-client';
  const RELEASES = `${CLIENT_REPO}/releases`;

  const origin = (typeof window !== 'undefined' && window.location?.origin)
    ? window.location.origin
    : 'https://openprinthq.com';

  const platforms = [
    { os: 'Windows', icon: '⊞', file: '.msi installer', note: 'Installs a background service + system-tray app. Starts at boot, no login needed.' },
    { os: 'macOS', icon: '', file: '.pkg installer', note: 'Installs a LaunchDaemon + menu-bar app. Starts at boot, no login needed.' },
    { os: 'Linux', icon: '🐧', file: '.deb / .rpm', note: 'Ships its own runtime; enable the systemd service for boot-without-login.' }
  ];

  const dockerRun =
`# Build & run the connector on a machine on your printers' LAN
git clone ${CLIENT_REPO}.git
cd openprinthq-cloud-client/agent
docker build -t openprinthq/connector .
docker run -d --name openprinthq-connector --restart unless-stopped \\
  --network host \\
  -e OPHQ_CONTROL_URL=${origin} \\
  -e OPHQ_CONNECTOR_TOKEN=YOUR_CONNECTOR_TOKEN \\
  openprinthq/connector`;

  let copied = $state(false);
  async function copyDocker() {
    try {
      await navigator.clipboard.writeText(dockerRun);
      copied = true;
      setTimeout(() => (copied = false), 1500);
    } catch { /* clipboard unavailable */ }
  }
</script>

<div class="dl">
  <div class="dl-head">
    <div>
      <h3>Connect a printer on your network</h3>
      <p class="muted">
        This instance runs in the cloud. To reach printers on your local network, run the
        <strong>Cloud Client</strong> connector on any always-on machine there (a PC, Mac, mini-PC or
        Raspberry Pi). It dials <em>out</em> to your instance over HTTPS — no port-forwarding and no
        inbound firewall rules. First create a token in
        <a href="/app/settings#connectors">Settings → Connectors</a>, then install and paste it in.
      </p>
    </div>
  </div>

  <div class="dl-grid">
    {#each platforms as p}
      <div class="dl-card">
        <div class="dl-os"><span class="dl-ic">{p.icon}</span>{p.os}</div>
        <p class="dl-note muted">{p.note}</p>
        <a class="btn btn-primary btn-sm" href={RELEASES} target="_blank" rel="noopener">Download {p.file} →</a>
      </div>
    {/each}
  </div>

  <details class="dl-docker">
    <summary>Or run it with Docker</summary>
    <div class="dl-code">
      <button class="copy" onclick={copyDocker}>{copied ? 'Copied' : 'Copy'}</button>
      <pre>{dockerRun}</pre>
    </div>
    <p class="muted fine">
      A prebuilt image is also attached to each <a href={RELEASES} target="_blank" rel="noopener">release</a>
      (<span class="mono">docker load</span> it), and a <span class="mono">compose.yml</span> lives in the repo.
    </p>
  </details>

  <p class="fine muted">
    Open source (AGPL-3.0): <a href={CLIENT_REPO} target="_blank" rel="noopener">openprinthq-cloud-client</a>.
    All platforms are outbound-only and self-test connectivity on install.
  </p>
</div>

<style>
  .dl { display: flex; flex-direction: column; gap: 1rem; }
  .dl-head h3 { margin: 0 0 0.3rem; }
  .dl-head p { margin: 0; max-width: 70ch; }
  .dl-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.75rem; }
  .dl-card { border: 1px solid var(--ophq-border, #2a2f3a); border-radius: 12px; padding: 0.9rem; display: flex; flex-direction: column; gap: 0.5rem; }
  .dl-os { font-weight: 600; display: flex; align-items: center; gap: 0.4rem; }
  .dl-ic { font-size: 1.1rem; }
  .dl-note { font-size: 0.85rem; flex: 1; margin: 0; }
  .dl-card .btn { align-self: flex-start; }
  .dl-docker summary { cursor: pointer; color: var(--ophq-text-2, #9aa2b1); font-size: 0.9rem; }
  .dl-code { position: relative; margin-top: 0.6rem; }
  .dl-code pre { background: #10131a; color: #e7e9ee; border: 1px solid var(--ophq-border, #2a2f3a); border-radius: 10px; padding: 0.9rem; overflow-x: auto; font-size: 0.8rem; line-height: 1.45; }
  .copy { position: absolute; top: 0.5rem; right: 0.5rem; font-size: 0.75rem; padding: 0.2rem 0.55rem; border-radius: 6px; border: 1px solid var(--ophq-border, #2a2f3a); background: var(--ophq-surface, #1e222b); color: inherit; cursor: pointer; }
  .fine { font-size: 0.8rem; }
  .mono { font-family: ui-monospace, Menlo, Consolas, monospace; }
</style>
