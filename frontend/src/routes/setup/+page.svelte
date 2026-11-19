<script>
  import { Icon } from 'openprinthq-ui';
  // First-run setup: the hub's own front end over /api/setup/v1, so the hub is complete without the
  // installer (invariants 3 and 4). The installer drives the same API from outside.
  import { onMount } from 'svelte';

  let status = $state(null);
  let err = $state('');
  let busy = $state(false);
  const farm = 'openprinthq';
  let teamName = $state('');
  let token = $state('');
  let done = $state(null);
  let copied = $state(false);

  onMount(async () => {
    try {
      const r = await fetch('/api/setup/v1/status');
      status = await r.json();
    } catch (e) {
      err = 'The hub did not answer. Reload the page in a moment.';
    }
  });

  async function submit(e) {
    e.preventDefault();
    err = '';
    busy = true;
    try {
      const r = await fetch('/api/setup/v1/initialize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token.trim()}` },
        body: JSON.stringify({ farm, team_name: teamName.trim(), key_name: 'First-run setup' })
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        err = typeof body.detail === 'string' ? body.detail : `Setup failed (${r.status})`;
        return;
      }
      done = body;
    } catch (e) {
      err = 'The hub did not answer. Try again.';
    } finally {
      busy = false;
    }
  }

  async function copyKey() {
    await navigator.clipboard.writeText(done.api_key);
    copied = true;
  }
</script>

<svelte:head><title>Set up OpenPrintHQ</title></svelte:head>

<div style="width:min(520px,100%)">
  <div class="flex items-center justify-center gap-[10px]" style="margin-bottom:18px"><span class="ah-icon-tile" style="background:var(--ah-accent);color:var(--ah-on-accent)"><Icon name="cube" /></span><span class="ah-sidebar__brand" style="padding:0">OpenPrintHQ</span></div>
  <section class="ah-glass ah-card" style="padding:28px;gap:16px">
    
    {#if done}
      <h1 class="ah-title" style="font-size:30px">Setup is done</h1>
      <p class="muted mb-4">
        Your workspace <b>{done.team.name}</b> runs <b>the farm</b>.
        This API key lets a script or the installer connect your other services. It is shown once.
      </p>
      <div class="flex gap-2 mb-4">
        <input class="input w-full font-mono text-sm" readonly value={done.api_key} aria-label="API key" />
        <button class="btn" onclick={copyKey}>{copied ? 'Copied' : 'Copy'}</button>
      </div>
      <a class="btn primary w-full inline-block text-center" href="/auth/login">Sign in</a>
    {:else if status && status.setup_complete}
      <h1 class="ah-title" style="font-size:30px">This hub is already set up</h1>
      <a class="btn primary w-full inline-block text-center" href="/">Go to OpenPrintHQ</a>
    {:else if status}
      <h1 class="ah-title" style="font-size:30px">Set up this hub</h1>
      <p class="muted mb-4">
        The setup token is in the hub's log from its first start, or is the HUB_SETUP_TOKEN the hub was started with.
      </p>
      <form class="space-y-4" onsubmit={submit}>
        <label class="block">
          <span class="font-medium">Workspace name</span>
          <input class="input w-full" bind:value={teamName} required maxlength="120" placeholder="My workshop" />
        </label>
        <label class="block">
          <span class="font-medium">Setup token</span>
          <input class="input w-full font-mono" bind:value={token} required autocomplete="off" />
        </label>
        {#if err}<p role="alert" style="color:var(--bad)">{err}</p>{/if}
        <button class="btn primary w-full" disabled={busy}>{busy ? 'Setting up...' : 'Finish setup'}</button>
      </form>
    {:else if err}
      <p role="alert" style="color:var(--bad)">{err}</p>
    {:else}
      <p class="muted">Checking the hub...</p>
    {/if}
  </section>
</div>
