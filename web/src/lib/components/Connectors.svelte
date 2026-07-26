<script>
  // OpenPrintHQ — local connectors (#28/#29).
  // Manage outbound-tunnel agents that let this cloud instance reach printers
  // on a private LAN behind NAT/CGNAT/firewalls. Create a connector (token
  // shown once), install the agent on that network, revoke when done.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let list = $state([]);
  let loading = $state(true);
  let error = $state(null);
  let name = $state('');
  let creating = $state(false);
  let created = $state(null);   // { name, token } shown once
  let copied = $state(false);
  let confirmDel = $state(null);
  let os = $state('docker');
  let printers = $state([]);
  let routing = $state({});   // printerId -> connector_id|null

  const base = $derived(typeof window !== 'undefined' ? window.location.origin : 'https://your-instance');

  async function load() {
    loading = true; error = null;
    try {
      const [r, pl, auto] = await Promise.all([
        api.connectors(),
        api.printers().catch(() => []),
        api.printerAutomation().catch(() => ({}))
      ]);
      list = Array.isArray(r) ? r : (r?.items || []);
      printers = (Array.isArray(pl) ? pl : (pl?.printers || pl?.items || [])).map((p) => ({ id: p.id ?? p.printer_id, name: p.name || ('Printer ' + p.id) }));
      const map = {};
      for (const p of printers) { const c = auto?.[p.id] || auto?.[String(p.id)]; map[p.id] = c?.connector_id ?? ''; }
      routing = map;
    }
    catch (e) { error = e.message || 'could not load connectors'; }
    finally { loading = false; }
  }
  onMount(load);

  async function setRoute(printerId, connectorId) {
    routing = { ...routing, [printerId]: connectorId };
    try { await api.savePrinterAutomation({ [printerId]: { connector_id: connectorId === '' ? null : Number(connectorId) } }); }
    catch { /* ignore */ }
  }

  async function create() {
    if (!name.trim() || creating) return;
    creating = true; created = null;
    try { created = await api.createConnector(name.trim()); name = ''; await load(); }
    catch (e) { error = e.message || 'could not create connector'; }
    finally { creating = false; }
  }
  async function copyTok() { try { await navigator.clipboard.writeText(created.token); copied = true; setTimeout(() => (copied = false), 2000); } catch { /* */ } }
  async function del(c) { try { await api.deleteConnector(c.id); confirmDel = null; await load(); } catch { /* */ } }
  function fmt(v) { if (!v) return 'never'; const d = new Date(v); return isNaN(d) ? 'never' : d.toLocaleString(); }

  const tok = $derived(created?.token || '<connector-token>');
  const instructions = $derived({
    docker: `# On a machine on the same LAN as your printers:\ncp .env.example .env\n# set OPHQ_CONTROL_URL=${base}\n# set OPHQ_CONNECTOR_TOKEN=${tok}\ndocker compose up -d`,
    linux: `sudo cp -r src package.json /opt/openprinthq-connector/\nsudo cp packaging/systemd/openprinthq-connector.service /etc/systemd/system/\n# put OPHQ_CONTROL_URL=${base} and OPHQ_CONNECTOR_TOKEN=${tok}\n#   into /etc/openprinthq-connector.env\nsudo systemctl enable --now openprinthq-connector`,
    macos: `cp -r src package.json "$HOME/Library/Application Support/openprinthq-connector/"\ncp packaging/launchd/org.openprinthq.connector.plist ~/Library/LaunchAgents/\n# edit the plist: OPHQ_CONTROL_URL=${base}, OPHQ_CONNECTOR_TOKEN=${tok}\nlaunchctl load ~/Library/LaunchAgents/org.openprinthq.connector.plist`,
    windows: `# PowerShell (Administrator):\n.\\packaging\\windows\\install-service.ps1 \`\n  -ControlUrl "${base}" \`\n  -Token "${tok}" -Name "windows-pc"`
  });
  let snipCopied = $state(false);
  async function copySnip() { try { await navigator.clipboard.writeText(instructions[os]); snipCopied = true; setTimeout(() => (snipCopied = false), 2000); } catch { /* */ } }
</script>

<div class="card card-pad conn">
  <div class="ch">
    <div><span class="eyebrow">Local connectors</span><p class="muted">Reach printers on a private network — behind NAT, CGNAT, or a firewall — with no port-forwarding. Install a small agent on that LAN; it dials out to this instance.</p></div>
  </div>

  <div class="create">
    <input class="input" placeholder="Connector name (e.g. home-lab)" bind:value={name} onkeydown={(e) => e.key === 'Enter' && create()} />
    <button class="btn btn-primary btn-sm" onclick={create} disabled={creating || !name.trim()}>{creating ? 'Creating…' : '+ New connector'}</button>
  </div>

  {#if created}
    <div class="newtok">
      <div class="nt-h">Connector token — copy it now, it won’t be shown again.</div>
      <div class="nt-row"><code class="mono">{created.token}</code><button class="btn btn-ghost btn-sm" onclick={copyTok}>{copied ? 'Copied ✓' : 'Copy'}</button></div>
      <div class="os-tabs">
        {#each [['docker', 'Docker'], ['linux', 'Linux'], ['macos', 'macOS'], ['windows', 'Windows 11']] as [k, lbl]}
          <button class="ostab" class:on={os === k} onclick={() => (os = k)}>{lbl}</button>
        {/each}
      </div>
      <div class="snip"><button class="cbtn" onclick={copySnip}>{snipCopied ? 'Copied' : 'Copy'}</button><pre>{instructions[os]}</pre></div>
      <p class="muted tiny">Get the agent from the <code>connector/</code> folder of the OpenPrintHQ repo. Full steps in its README.</p>
      <button class="btn btn-ghost btn-xs" onclick={() => (created = null)}>Dismiss</button>
    </div>
  {/if}

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if list.length === 0}
    <p class="muted none">No connectors yet. Create one for each network that has printers you want to reach remotely.</p>
  {:else}
    <div class="clist">
      {#each list as c (c.id)}
        <div class="crow">
          <div class="cmain">
            <div class="cname">{c.name} <span class="dot {c.online ? 'on' : ''}" title={c.online ? 'online' : 'offline'}></span><span class="st muted">{c.online ? 'online' : 'offline'}</span></div>
            <div class="cmeta muted mono">last seen {fmt(c.last_seen)}</div>
          </div>
          {#if confirmDel === c.id}
            <span class="flex gap"><button class="btn btn-danger btn-xs" onclick={() => del(c)}>Revoke</button><button class="btn btn-ghost btn-xs" onclick={() => (confirmDel = null)}>✕</button></span>
          {:else}
            <button class="btn btn-ghost btn-xs" onclick={() => (confirmDel = c.id)}>Revoke</button>
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  {#if !loading && !error && list.length > 0 && printers.length > 0}
    <div class="routing">
      <span class="gl">Printer routing</span>
      <p class="muted tiny">Choose how each printer is reached. <b>Direct</b> = this instance talks to it on its own network; <b>via a connector</b> = tunnelled through that agent (for printers on a remote LAN). Assignment is saved immediately; it takes effect when the printer connects through the connector.</p>
      <div class="rlist">
        {#each printers as p (p.id)}
          <div class="rrow">
            <span class="rn">{p.name}</span>
            <select class="input rsel" value={routing[p.id] ?? ''} onchange={(e) => setRoute(p.id, e.currentTarget.value)}>
              <option value="">Direct (same network)</option>
              {#each list as c (c.id)}<option value={c.id}>via {c.name}</option>{/each}
            </select>
          </div>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .conn { margin-top: 1.2rem; }
  .ch p { margin: 0.3rem 0 0; font-size: 0.88rem; max-width: 68ch; }
  code { font-family: var(--font-mono); font-size: 0.9em; background: var(--ophq-bg-2); padding: 0.05rem 0.3rem; border-radius: 4px; }
  .create { display: flex; gap: 0.5rem; margin: 1rem 0; }
  .create .input { max-width: 320px; }
  .newtok { margin: 0.5rem 0 1rem; padding: 0.9rem; border: 1px solid var(--ophq-success); border-radius: var(--radius-sm); background: rgba(53,196,107,0.07); }
  .nt-h { font-size: 0.86rem; color: var(--ophq-success); margin-bottom: 0.5rem; }
  .nt-row { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 0.8rem; }
  .nt-row code { flex: 1; overflow-x: auto; white-space: nowrap; padding: 0.4rem 0.6rem; }
  .os-tabs { display: flex; gap: 0.3rem; margin-bottom: 0.5rem; flex-wrap: wrap; }
  .ostab { padding: 0.3rem 0.7rem; font-size: 0.8rem; border: 1px solid var(--ophq-border); background: var(--ophq-surface); color: var(--ophq-text-2); border-radius: 999px; cursor: pointer; }
  .ostab.on { background: var(--ophq-primary-dim); color: var(--ophq-primary-2); border-color: transparent; }
  .snip { position: relative; }
  .snip pre { background: #070b11; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 0.7rem 0.8rem; font-size: 0.78rem; overflow-x: auto; white-space: pre; margin: 0; }
  .cbtn { position: absolute; top: 0.4rem; right: 0.4rem; font-size: 0.7rem; padding: 0.15rem 0.5rem; border: 1px solid var(--ophq-border); background: var(--ophq-surface); color: var(--ophq-text-2); border-radius: 4px; cursor: pointer; }
  .tiny { font-size: 0.76rem; margin: 0.5rem 0; }
  .none { padding: 0.4rem 0; }
  .clist { display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.8rem; }
  .crow { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 0.6rem 0.8rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); }
  .cname { font-weight: 600; display: flex; align-items: center; gap: 0.5rem; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--ophq-muted); display: inline-block; }
  .dot.on { background: var(--ophq-success); box-shadow: 0 0 6px rgba(53,196,107,0.6); }
  .st { font-size: 0.78rem; font-weight: 400; }
  .cmeta { font-size: 0.72rem; margin-top: 0.15rem; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; }
  .routing { margin-top: 1.2rem; border-top: 1px solid var(--ophq-border); padding-top: 0.9rem; }
  .gl { display: block; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); margin-bottom: 0.4rem; }
  .rlist { display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.6rem; }
  .rrow { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
  .rn { font-size: 0.9rem; }
  .rsel { max-width: 240px; }
</style>
