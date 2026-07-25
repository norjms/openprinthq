<script>
  import Logo from '$lib/components/Logo.svelte';
  import { goto } from '$app/navigation';

  let email = $state('');
  let busy = $state(false);
  let err = $state(null);

  // Production auth is Authentik OIDC via the control-plane (/api/auth/login).
  // Until SSO is wired, a dev sign-in creates a local session so the app is walkable.
  async function devSignin(e) {
    e.preventDefault();
    busy = true; err = null;
    try {
      const res = await fetch('/api/auth/dev-login', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        credentials: 'include', body: JSON.stringify({ email })
      });
      if (!res.ok) throw new Error('sign-in failed');
      goto('/app');
    } catch (e2) { err = e2.message; } finally { busy = false; }
  }
</script>

<svelte:head><title>Sign in · OpenPrintHQ</title></svelte:head>

<div class="auth">
  <a href="/" class="back muted">← back</a>
  <div class="card card-pad box">
    <div class="lg"><Logo size={34} /></div>
    <h2>Sign in to your HQ</h2>
    <p class="muted">Access your private OpenPrintHQ instance.</p>

    <a class="btn btn-primary sso" href="/api/auth/login">Continue with SSO (Authentik)</a>
    <div class="or"><span>or dev sign-in</span></div>

    <form onsubmit={devSignin}>
      <div class="field">
        <label for="e">Email</label>
        <input id="e" class="input" type="email" bind:value={email} placeholder="you@example.com" required />
      </div>
      {#if err}<p class="err">{err}</p>{/if}
      <button class="btn btn-ghost full" disabled={busy}>{busy ? 'Signing in…' : 'Dev sign-in'}</button>
    </form>
    <p class="muted small">New here? <a href="/signup">Launch your HQ →</a></p>
  </div>
</div>

<style>
  .auth { min-height: 100vh; display: grid; place-items: center; padding: 2rem; }
  .box { width: 100%; max-width: 400px; text-align: center; }
  .lg { margin-bottom: 1rem; display: flex; justify-content: center; }
  .sso { width: 100%; justify-content: center; margin: 1.2rem 0 0.4rem; }
  .or { display: flex; align-items: center; gap: 0.8rem; color: var(--ophq-faint); font-size: 0.8rem; margin: 1rem 0; }
  .or::before, .or::after { content: ''; flex: 1; height: 1px; background: var(--ophq-border); }
  .full { width: 100%; justify-content: center; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; }
  .small { margin-top: 1rem; }
  .back { position: fixed; top: 1.4rem; left: 1.6rem; font-size: 0.9rem; }
</style>
