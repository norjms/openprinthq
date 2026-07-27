<script>
  import { onMount } from 'svelte';
  import Logo from '$lib/components/Logo.svelte';
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
      // Full-page navigation (NOT client-side) so npmplus forward-auth can hand
      // off to the Authentik login cleanly — otherwise the SPA can't follow the
      // external redirect and the app looks broken until a manual refresh.
      setTimeout(() => { window.location.href = '/app'; }, 1100);
    } catch (err) {
      error = err?.message || 'Signup failed. Please try again.';
      busy = false;
    }
  }
</script>

<PageTitle page="Create your account" />

<div class="auth">
  <div class="card card-pad box">
    <div class="lg"><Logo size={34} /></div>
    {#if done}
      <h2>You're in 🎉</h2>
      <p class="muted">Your account and private instance are ready — taking you to secure sign-in…</p>
      <a class="btn btn-primary full" href="/app" data-sveltekit-reload>Continue with SSO →</a>
    {:else}
      <h2>Create your account</h2>
      <p class="muted">{inviteRequired ? 'Redeem your invite code to get your own private OpenPrintHQ instance.' : 'Set up the first account — you’ll be the owner.'}</p>
      <form onsubmit={submit} class="form">
        {#if inviteRequired}
          <label>Invite code
            <input bind:value={code} required autocomplete="off" placeholder="ophq-…" />
          </label>
        {/if}
        <label>Email
          <input type="email" bind:value={email} required autocomplete="email" placeholder="you@example.com" />
        </label>
        <label>Name
          <input bind:value={name} autocomplete="name" placeholder="Your name" />
        </label>
        <label>Password
          <input type="password" bind:value={password} required minlength="10" autocomplete="new-password" placeholder="at least 10 characters" />
        </label>
        {#if error}<p class="err" role="alert">{error}</p>{/if}
        <button class="btn btn-primary full" disabled={busy}>{busy ? 'Creating…' : 'Create account'}</button>
      </form>
      <p class="muted small">Already have an account? <a href="/app" data-sveltekit-reload>Sign in</a></p>
    {/if}
  </div>
</div>

<style>
  .auth { min-height: 100vh; display: grid; place-items: center; padding: 2rem; }
  .box { width: 100%; max-width: 420px; text-align: center; }
  .lg { margin-bottom: 1rem; display: flex; justify-content: center; }
  .full { width: 100%; justify-content: center; margin-top: 0.4rem; }
  .form { display: grid; gap: 0.8rem; text-align: left; margin-top: 1.2rem; }
  .form label { display: grid; gap: 0.3rem; font-size: 0.85rem; color: var(--ophq-text-2); }
  .form input { padding: 0.6rem 0.7rem; border-radius: var(--radius-sm); border: 1px solid var(--ophq-border); background: var(--ophq-bg-2); color: var(--ophq-text); font-size: 0.95rem; }
  .hint { font-size: 0.72rem; color: var(--ophq-muted); }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0; }
  .small { margin-top: 1rem; font-size: 0.85rem; }
</style>
