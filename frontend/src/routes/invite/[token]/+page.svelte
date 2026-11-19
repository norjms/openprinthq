<script>
  import { Icon } from 'openprinthq-ui';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.js';

  let info = $state(null);
  let err = $state('');
  let busy = $state(false);
  const token = page.params.token;
  const here = `/invite/${encodeURIComponent(token)}`;

  onMount(async () => {
    try { info = await api.get(`/api/invites/${encodeURIComponent(token)}`); }
    catch (e) { err = e.message; }
  });
  async function accept() {
    busy = true;
    try { await api.post(`/api/invites/${encodeURIComponent(token)}/accept`); location.href = '/'; }
    catch (e) { err = e.message; busy = false; }
  }
</script>

<svelte:head><title>Join OpenPrintHQ</title></svelte:head>

<div style="width:min(520px,100%)">
  <div class="flex items-center justify-center gap-[10px]" style="margin-bottom:18px"><span class="ah-icon-tile" style="background:var(--ah-accent);color:var(--ah-on-accent)"><Icon name="cube" /></span><span class="ah-sidebar__brand" style="padding:0">OpenPrintHQ</span></div>
  <section class="ah-glass ah-card" style="padding:28px;gap:16px">
        {#if err}
      <h1 class="ah-title" style="font-size:30px">Can't use this invite</h1>
      <p class="muted">{err}</p>
      <a class="btn mt-4 inline-block" href="/">Go to OpenPrintHQ</a>
    {:else if info}
      <h1 class="ah-title" style="font-size:30px">{info.kind === 'join' ? `Join ${info.team}` : info.kind === 'new_team' ? `Your team: ${info.team}` : 'Your own workspace'}</h1>
      <p class="muted mb-4">{info.kind === 'join' ? `You were invited as ${info.role}.` : "You'll be its owner and can invite your own people."} This invite is for <b>{info.email}</b>.</p>
      {#if info.signed_in && info.matches}
        <button class="btn primary w-full" disabled={busy} onclick={accept}>{busy ? 'Joining...' : 'Accept invite'}</button>
      {:else if info.signed_in}
        <p class="mb-4" style="color:var(--bad)">You are signed in as {info.signed_in_as || 'another account'}.</p>
        <a class="btn w-full inline-block text-center" href="/auth/logout">Sign out and use {info.email}</a>
      {:else}
        <ol class="space-y-4">
          {#if info.signup_url}
            <li><div class="font-medium mb-1">1. Create your login</div>
              <p class="text-sm muted mb-2">Choose a password for {info.email}. It opens in a new tab; come back here when you're done.</p>
              <a class="btn primary w-full inline-block text-center" href={info.signup_url} target="_blank" rel="noopener">Create login</a></li>
          {/if}
          <li><div class="font-medium mb-1">{info.signup_url ? '2. ' : ''}Sign in and accept</div>
            <a class="btn {info.signup_url ? '' : 'primary'} w-full inline-block text-center" href={`/auth/login?next=${encodeURIComponent(here)}`}>Sign in</a></li>
        </ol>
      {/if}
    {:else}
      <p class="muted">Checking the invite...</p>
    {/if}
  </section>
</div>
