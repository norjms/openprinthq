<script>
  // Where THIS instance's logs go. Scoped to the signed-in tenant: the platform
  // operator never sets this and never receives these logs, which describe the
  // tenant's own printers and activity.
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let logUrl = $state('');
  let busy = $state(false);
  let msg = $state('');
  let err = $state('');

  onMount(async () => {
    try { logUrl = (await api.logSettings()).log_url || ''; }
    catch (e) { err = e.message || 'could not load the logging setting'; }
  });

  async function save() {
    if (busy) return;
    busy = true; msg = ''; err = '';
    try {
      const r = await api.saveLogSettings(logUrl.trim());
      logUrl = r.log_url || '';
      msg = logUrl ? 'Saved. This instance is sending its logs to that destination.' : 'Cleared. Logs stay on the server.';
    } catch (e) { err = e.message || 'could not save the logging setting'; }
    finally { busy = false; }
  }
</script>

<div class="card card-pad">
  <span class="eyebrow">Logging</span>
  <p class="muted small">
    Send this instance's logs to your own Grafana Loki or syslog server. Nothing is sent
    anywhere unless you set a destination here, and only your instance's logs are sent.
  </p>
  <label class="small lbl">
    Destination
    <input class="input" bind:value={logUrl} autocomplete="off"
           placeholder="http://loki.mynetwork.lan:3100 or syslog://siem.mynetwork.lan:514" />
  </label>
  <div class="flex gap">
    <button class="btn" disabled={busy} onclick={save}>Save</button>
    <button class="btn btn-ghost" disabled={busy || !logUrl} onclick={() => { logUrl = ''; save(); }}>Clear</button>
  </div>
  {#if msg}<p class="small ok">{msg}</p>{/if}
  {#if err}<p class="small bad">{err}</p>{/if}
  <p class="muted tiny">
    Supported: <code>http(s)://host:3100</code> for Loki, <code>syslog://host:514</code> for RFC5424 syslog.
  </p>
</div>

<style>
  .lbl { display: grid; gap: 0.3rem; margin: 0.6rem 0; }
  .bad { color: var(--danger, #d66); }
</style>
