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
  let nameError = $state(null);
  let creating = $state(false);
  let created = $state(null);   // { name, token } shown once
  let copied = $state(false);
  let confirmDel = $state(null);
  let os = $state('docker');
  let keyFormId = $state(null);   // connector id whose client-key form is open
  let keyPem = $state('');
  let keyBusy = $state(false);
  let printers = $state([]);
  let routing = $state({});   // printerId -> connector_id|null

  // client key (server-held private half; connectors verify commands with the public half)
  let signPub = $state(null);
  let signCreated = $state(null);
  let signBusy = $state(false);
  let confirmRegen = $state(false);
  let confirmRemove = $state(false);
  let pubCopied = $state(false);

  const base = $derived(typeof window !== 'undefined' ? window.location.origin : 'https://your-instance');

  async function load() {
    loading = true; error = null;
    try {
      const [r, pl, auto, sk] = await Promise.all([
        api.connectors(),
        api.printers().catch(() => []),
        api.printerAutomation().catch(() => ({})),
        api.signingKey().catch(() => ({}))
      ]);
      signPub = sk?.public_pem || null;
      signCreated = sk?.created_at || null;
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

  async function genKey() {
    signBusy = true; confirmRegen = false;
    try { const r = await api.generateSigningKey(); signPub = r.public_pem; signCreated = new Date().toISOString(); }
    catch { /* ignore */ } finally { signBusy = false; }
  }
  // Printers routed through a connector are useless once the key it verifies
  // commands with is gone — they stay listed but silently stop responding. Offer
  // to clear them in the same action rather than leaving the user to discover
  // stranded entries later.
  let alsoRemovePrinters = $state(false);
  let boundPrinters = $state(0);
  async function countBoundPrinters() {
    try {
      const auto = await api.printerAutomation();
      const ids = new Set(list.map((c) => c.id));
      boundPrinters = Object.values(auto || {}).filter((a) => a && ids.has(a.connector_id)).length;
    } catch { boundPrinters = 0; }
  }

  async function removeKey() {
    signBusy = true; confirmRemove = false;
    try {
      if (alsoRemovePrinters) {
        const auto = await api.printerAutomation().catch(() => ({}));
        const ids = new Set(list.map((c) => c.id));
        for (const [pid, a] of Object.entries(auto || {})) {
          if (a && ids.has(a.connector_id)) await api.deletePrinter(pid).catch(() => {});
        }
      }
      await api.deleteSigningKey();
      signPub = null; signCreated = null;
    } catch { /* ignore */ }
    finally { signBusy = false; alsoRemovePrinters = false; boundPrinters = 0; }
  }
  async function copyPub() { try { await navigator.clipboard.writeText(signPub); pubCopied = true; setTimeout(() => (pubCopied = false), 2000); } catch { /* */ } }

  function openKeyForm(c) { keyFormId = keyFormId === c.id ? null : c.id; keyPem = ''; }
  async function saveClientKey(c) {
    keyBusy = true;
    try { await api.setConnectorClientKey(c.id, keyPem); keyFormId = null; keyPem = ''; await load(); }
    catch (e) { error = e.message || 'could not save key'; }
    finally { keyBusy = false; }
  }
  async function clearClientKey(c) {
    keyBusy = true;
    try { await api.setConnectorClientKey(c.id, ''); keyFormId = null; await load(); }
    catch { /* */ } finally { keyBusy = false; }
  }

  // Trust-on-first-use: clear the locked client key so the next client to
  // connect with this token pairs (locks) instead.
  let confirmReset = $state(null);
  let resetBusy = $state(false);
  async function resetKey(c) {
    resetBusy = true;
    try { await api.resetConnectorKey(c.id); confirmReset = null; await load(); }
    catch (e) { error = e.message || 'could not reset key'; }
    finally { resetBusy = false; }
  }

  // LAN discovery through a specific site (connector).
  let scanBusy = $state(null);       // connector id being scanned
  let scanResults = $state({});      // connector id -> devices[]
  let scanMsg = $state({});          // connector id -> message
  let subnets = $state({});          // connector id -> editable subnet CIDR

  // Default the subnet field to the connector's reported host /24 (falls back to
  // a friendly example). Only computed once per connector; the user can edit it.
  function subnetFor(c) {
    if (subnets[c.id] != null) return subnets[c.id];
    const def = normalizeCidr(c.host_cidr) || '';
    return def;
  }
  function normalizeCidr(v) {
    if (!v) return '';
    const m = String(v).match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?:\/(\d{1,2}))?$/);
    if (!m) return '';
    // force a /24 base regardless of what was reported (scan is capped at /24)
    return `${m[1]}.${m[2]}.${m[3]}.0/24`;
  }
  async function scanSite(c) {
    const subnet = normalizeCidr(subnetFor(c)) || '';
    if (subnetFor(c) && !subnet) { scanMsg = { ...scanMsg, [c.id]: 'Enter a subnet like 192.168.1.0/24 (/24 max).' }; return; }
    scanBusy = c.id; scanMsg = { ...scanMsg, [c.id]: '' };
    try {
      const r = await api.discoverConnector(c.id, 8000, subnet);
      if (!r.connector_online) { scanMsg = { ...scanMsg, [c.id]: 'This connector is offline — start the Cloud Client on that site, then scan again.' }; scanResults = { ...scanResults, [c.id]: [] }; }
      else {
        scanResults = { ...scanResults, [c.id]: r.devices || [] };
        scanMsg = { ...scanMsg, [c.id]: (r.devices?.length ? '' : 'No printers announced on that LAN during the scan window. Confirm the printers are on and in LAN mode, then retry.') };
      }
    } catch (e) { scanMsg = { ...scanMsg, [c.id]: e.message || 'scan failed' }; }
    finally { scanBusy = null; }
  }

  async function create() {
    if (creating) return;
    if (!name.trim()) { nameError = 'Name is required'; return; }
    nameError = null;
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
    <div><span class="eyebrow">Local connectors</span><p class="muted">Reach printers on a private network — behind NAT, CGNAT, or a firewall — with no port-forwarding. Install the Cloud Client on that LAN; it dials out to this instance. Create <b>one connector per site</b> (home, shop, office) — you'll pick which site a printer lives on when you add it. The first client to connect with a connector's token <b>pairs</b> to it automatically (no key to copy); after that the connector only accepts that client until you Reset it.</p></div>
  </div>

  <div class="create">
    <input
      class="input"
      class:invalid={nameError}
      placeholder="Connector name (e.g. home-lab)"
      bind:value={name}
      aria-invalid={nameError ? 'true' : undefined}
      aria-describedby={nameError ? 'connector-name-error' : undefined}
      oninput={() => { if (nameError) nameError = null; }}
      onkeydown={(e) => e.key === 'Enter' && create()}
    />
    <button class="btn btn-primary btn-sm" onclick={create} disabled={creating}>{creating ? 'Creating…' : '+ New connector'}</button>
  </div>
  {#if nameError}<p class="err" id="connector-name-error" role="alert">{nameError}</p>{/if}

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

  <div class="signing">
    <span class="gl">Client key <span class="muted">(command authentication)</span></span>
    <p class="muted tiny">An RSA-2048 key pair that lets a connector verify every command really came from this instance. This server keeps the private key; copy the <b>public</b> key into your connector as <code>OPHQ_SIGNING_PUBKEY</code>. The connector then rejects any command not signed by this instance.</p>
    {#if signPub}
      <div class="snip"><button class="cbtn" onclick={copyPub}>{pubCopied ? 'Copied' : 'Copy'}</button><pre>{signPub}</pre></div>
      <div class="skacts">
        <span class="muted mono tiny">created {fmt(signCreated)}</span>
        {#if confirmRegen}
          <span class="flex gap"><button class="btn btn-primary btn-xs" onclick={genKey} disabled={signBusy}>Regenerate</button><button class="btn btn-ghost btn-xs" onclick={() => (confirmRegen = false)} aria-label="Cancel">✕</button></span>
          <span class="muted tiny">— you’ll need to update every connector with the new key.</span>
        {:else if confirmRemove}
          <div class="removewarn">
            <p class="tiny">
              Removing the client key stops every connector from verifying commands from this
              instance. {#if list.length}<b>{list.length} connector{list.length === 1 ? '' : 's'}</b>
              {#if boundPrinters}and <b>{boundPrinters} printer{boundPrinters === 1 ? '' : 's'}</b> routed through
              {list.length === 1 ? 'it' : 'them'}{/if} will stop responding until you generate a new key and
              update {list.length === 1 ? 'it' : 'each one'}.{/if}
            </p>
            {#if boundPrinters}
              <label class="tiny chk">
                <input type="checkbox" bind:checked={alsoRemovePrinters} />
                Also remove the {boundPrinters} printer{boundPrinters === 1 ? '' : 's'} routed through
                {list.length === 1 ? 'this connector' : 'these connectors'}
              </label>
            {/if}
            <span class="flex gap"><button class="btn btn-danger btn-xs" onclick={removeKey} disabled={signBusy}>{alsoRemovePrinters ? 'Remove key and printers' : 'Remove key'}</button><button class="btn btn-ghost btn-xs" onclick={() => { confirmRemove = false; alsoRemovePrinters = false; }} aria-label="Cancel">✕</button></span>
          </div>
        {:else}
          <button class="btn btn-ghost btn-xs" onclick={() => (confirmRegen = true)}>Regenerate</button>
          <button class="btn btn-ghost btn-xs" onclick={() => { confirmRemove = true; countBoundPrinters(); }}>Remove</button>
        {/if}
      </div>
    {:else}
      <button class="btn btn-ghost btn-sm" onclick={genKey} disabled={signBusy}>{signBusy ? 'Generating…' : 'Generate client key'}</button>
    {/if}
  </div>

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
            <div class="cname">{c.name} <span class="dot {c.online ? 'on' : ''}" title={c.online ? 'online' : 'offline'}></span><span class="st muted">{c.online ? 'online' : 'offline'}</span>
              {#if c.has_client_key}<span class="lock" title="A client is paired. The connector is locked to that client's key; reset to pair a different one.">🔒 paired</span>
              {:else}<span class="lock open" title="No client paired yet. The first Cloud Client that connects with this token locks onto its key (trust-on-first-use).">🔓 awaiting first client</span>{/if}
            </div>
            <div class="cmeta muted mono">last seen {fmt(c.last_seen)}</div>
          </div>
          <div class="flex gap">
            <input class="input subnet mono" placeholder="192.168.1.0/24" value={subnetFor(c)} oninput={(e) => (subnets = { ...subnets, [c.id]: e.target.value })} title="Subnet to scan (defaults to the client's host network; /24 max)" disabled={!c.online} />
            <button class="btn btn-ghost btn-xs" onclick={() => scanSite(c)} disabled={scanBusy === c.id || !c.online} title={c.online ? 'Scan the selected subnet for printers' : 'Connector offline'}>{scanBusy === c.id ? 'Scanning…' : 'Scan LAN'}</button>
            {#if c.has_client_key}
              {#if confirmReset === c.id}
                <button class="btn btn-danger btn-xs" onclick={() => resetKey(c)} disabled={resetBusy}>{resetBusy ? '…' : 'Reset key'}</button><button class="btn btn-ghost btn-xs" onclick={() => (confirmReset = null)} aria-label="Cancel">✕</button>
              {:else}
                <button class="btn btn-ghost btn-xs" onclick={() => (confirmReset = c.id)} title="Unpair the current client so a new one can connect">Reset key</button>
              {/if}
            {/if}
            <button class="btn btn-ghost btn-xs" onclick={() => openKeyForm(c)} title="Advanced: paste a key manually">Key</button>
            {#if confirmDel === c.id}
              <button class="btn btn-danger btn-xs" onclick={() => del(c)}>Revoke</button><button class="btn btn-ghost btn-xs" onclick={() => (confirmDel = null)} aria-label="Cancel">✕</button>
            {:else}
              <button class="btn btn-ghost btn-xs" onclick={() => (confirmDel = c.id)}>Revoke</button>
            {/if}
          </div>
        </div>
        {#if confirmReset === c.id}
          <div class="keyform"><p class="muted tiny">Reset unpairs the current client. The <b>next</b> Cloud Client that connects with this connector's token will lock onto its key. Use this when you replace or reinstall the client.</p></div>
        {/if}
        {#if scanResults[c.id] || scanMsg[c.id]}
          <div class="scanres">
            {#if scanResults[c.id]?.length}
              <div class="srhead muted tiny">Found on this site's LAN:</div>
              {#each scanResults[c.id] as d}
                <div class="srrow">
                  <span class="srn">{d.name || 'Printer'}</span>
                  <span class="muted mono tiny">{d.ip}{#if d.serial} · {d.serial}{/if} · {d.vendor}</span>
                  <a class="btn btn-ghost btn-xs" href={`/app/printers/add?ip=${encodeURIComponent(d.ip)}&vendor=${encodeURIComponent(d.vendor)}&connector=${c.id}&serial=${encodeURIComponent(d.serial||'')}&model=${encodeURIComponent(d.friendly_model||d.model||'')}&code=${encodeURIComponent(d.model||'')}`}>Add</a>
                </div>
              {/each}
            {/if}
            {#if scanMsg[c.id]}<p class="muted tiny">{scanMsg[c.id]}</p>{/if}
          </div>
        {/if}
        {#if keyFormId === c.id}
          <div class="keyform">
            <p class="muted tiny">Paste this connector's <b>public</b> key (run <code>node src/agent.js --pubkey</code> on the connector, or copy it from the connector's startup log). Once set, the connector must prove it holds the matching private key on every connect.</p>
            <textarea class="input kf" rows="4" placeholder="-----BEGIN PUBLIC KEY-----&#10;...&#10;-----END PUBLIC KEY-----" bind:value={keyPem} spellcheck="false"></textarea>
            <div class="flex gap">
              <button class="btn btn-primary btn-xs" onclick={() => saveClientKey(c)} disabled={keyBusy || !keyPem.trim()}>{keyBusy ? 'Saving…' : 'Save key'}</button>
              {#if c.has_client_key}<button class="btn btn-ghost btn-xs" onclick={() => clearClientKey(c)} disabled={keyBusy}>Remove (disable mutual auth)</button>{/if}
              <button class="btn btn-ghost btn-xs" onclick={() => (keyFormId = null)}>Cancel</button>
            </div>
          </div>
        {/if}
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
  .subnet { width: 9.5rem; font-size: 0.78rem; padding: 0.2rem 0.45rem; }
  .input.invalid { border-color: var(--ophq-danger); }
  .lock { font-size: 0.68rem; color: var(--ophq-success); border: 1px solid rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); padding: 0.05rem 0.4rem; border-radius: 999px; }
  .lock.open { color: var(--ophq-muted); border-color: var(--ophq-border); background: transparent; }
  .scanres { margin: -0.2rem 0 0.3rem; padding: 0.6rem 0.8rem; border: 1px solid var(--ophq-border); border-top: none; border-radius: 0 0 var(--radius-sm) var(--radius-sm); background: var(--ophq-bg-2); }
  .srhead { margin-bottom: 0.3rem; }
  .srrow { display: flex; align-items: center; gap: 0.6rem; padding: 0.35rem 0; border-top: 1px solid var(--ophq-border); }
  .srrow:first-of-type { border-top: none; }
  .srn { font-size: 0.86rem; flex: 1; }
  .keyform { margin: -0.2rem 0 0.3rem; padding: 0.7rem 0.8rem; border: 1px solid var(--ophq-border); border-top: none; border-radius: 0 0 var(--radius-sm) var(--radius-sm); background: var(--ophq-bg-2); }
  .kf { font-family: var(--font-mono); font-size: 0.76rem; margin: 0.3rem 0 0.5rem; resize: vertical; }
  .signing { margin-top: 1.2rem; border-top: 1px solid var(--ophq-border); padding-top: 0.9rem; }
  .signing .snip pre { max-height: 150px; }
  .skacts { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.6rem; flex-wrap: wrap; }
  .routing { margin-top: 1.2rem; border-top: 1px solid var(--ophq-border); padding-top: 0.9rem; }
  .gl { display: block; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); margin-bottom: 0.4rem; }
  .rlist { display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.6rem; }
  .rrow { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
  .rn { font-size: 0.9rem; }
  .rsel { max-width: 240px; }
  .removewarn { display: grid; gap: 0.45rem; padding: 0.55rem 0.7rem; border-radius: var(--radius, 8px);
                border: 1px solid var(--danger-border, #6b2b2b); background: var(--danger-bg, #2a1616); }
  .removewarn .chk { display: flex; align-items: center; gap: 0.4rem; cursor: pointer; }
</style>
