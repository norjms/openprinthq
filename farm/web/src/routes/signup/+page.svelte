<script>
  import { onMount } from 'svelte';
  import AuthCard from '$lib/components/AuthCard.svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import { api } from '$lib/api';

  let code = $state('');
  let email = $state('');
  let name = $state('');
  let password = $state('');
  let busy = $state(false);
  let error = $state('');
  let done = $state(false);
  // Whether an invite is required yet. Hidden for the very first (bootstrap)
  // account so it isn't confused by a code field it doesn't need.
  let inviteRequired = $state(true);
  onMount(() => { api.signupInfo().then((i) => { inviteRequired = i?.inviteRequired !== false; }).catch(() => {}); });

  async function submit(e) {
    e.preventDefault();
    error = '';
    if (password.length < 10) { error = 'Password must be at least 10 characters.'; return; }
    busy = true;
    try {
      await api.signup({ code: code.trim(), email: email.trim(), name: name.trim(), password });
      done = true;
      // Full-page navigation (NOT client-side) so the reverse proxy's forward-auth can hand
      // off to the Authentik login cleanly, otherwise the SPA can't follow the
      // external redirect and the app looks broken until a manual refresh.
      setTimeout(() => { window.location.href = '/app'; }, 1100);
    } catch (err) {
      error = err?.message || 'Signup failed. Please try again.';
      busy = false;
    }
  }
</script>

<PageTitle page="Create your account" />

<AuthCard title={done ? "You're in" : 'Create your account'}>
  {#if done}
    <p class="ah-secondary">Your account and private instance are ready. Taking you to secure sign-in.</p>
    <a class="btn btn-primary full" href="/app" data-sveltekit-reload>Continue with single sign-on</a>
  {:else}
    <p class="ah-secondary">{inviteRequired ? 'Redeem your invite code to get your own private OpenPrintHQ instance.' : 'Set up the first account. You will be the owner.'}</p>
    <form onsubmit={submit} class="form">
      {#if inviteRequired}
        <div class="ah-field"><label for="su-code">Invite code</label>
          <input id="su-code" class="ah-input" bind:value={code} required autocomplete="off" placeholder="ophq-" /></div>
      {/if}
      <div class="ah-field"><label for="su-email">Email</label>
        <input id="su-email" class="ah-input" type="email" bind:value={email} required autocomplete="email" placeholder="you@example.com" /></div>
      <div class="ah-field"><label for="su-name">Name</label>
        <input id="su-name" class="ah-input" bind:value={name} autocomplete="name" placeholder="Your name" /></div>
      <div class="ah-field"><label for="su-pass">Password</label>
        <input id="su-pass" class="ah-input" type="password" bind:value={password} required minlength="10" autocomplete="new-password" placeholder="At least 10 characters" /></div>
      {#if error}<p class="err" role="alert">{error}</p>{/if}
      <button class="btn btn-primary full" disabled={busy}>{busy ? 'Creating your account' : 'Create account'}</button>
    </form>
    <p class="ah-muted small">Already have an account? <a href="/app" data-sveltekit-reload>Sign in</a></p>
  {/if}
</AuthCard>

<style>
  .full { width: 100%; }
  .form { display: grid; gap: 12px; }
  .err { color: var(--ah-chip-bad-text); font-size: 13px; }
  .small { font-size: 13px; }
</style>
