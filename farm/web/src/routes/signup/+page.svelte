<script>
  import Logo from '$lib/components/Logo.svelte';
  import { goto } from '$app/navigation';

  let email = $state('');
  let busy = $state(false);
  let err = $state(null);

  async function start(e) {
    e.preventDefault();
    busy = true; err = null;
    try {
      const res = await fetch('/api/auth/dev-login', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        credentials: 'include', body: JSON.stringify({ email, signup: true })
      });
      if (!res.ok) throw new Error('sign-up failed');
      goto('/app'); // overview will offer to provision the instance
    } catch (e2) { err = e2.message; } finally { busy = false; }
  }
</script>

<svelte:head><title>Launch your HQ · OpenPrintHQ</title></svelte:head>

<div class="auth">
  <a href="/" class="back muted">← back</a>
  <div class="card card-pad box">
    <div class="lg"><Logo size={34} /></div>
    <h2>Launch your HQ</h2>
    <p class="muted">Create an account and we'll provision a private, isolated instance for you.</p>

    <a class="btn btn-primary sso" href="/api/auth/login">Sign up with SSO (Authentik)</a>
    <div class="or"><span>or dev sign-up</span></div>

    <form onsubmit={start}>
      <div class="field">
        <label for="e">Email</label>
        <input id="e" class="input" type="email" bind:value={email} placeholder="you@example.com" required />
      </div>
      {#if err}<p class="err">{err}</p>{/if}
      <button class="btn btn-primary full" disabled={busy}>{busy ? 'Creating…' : 'Create my HQ →'}</button>
    </form>
    <ul class="perks">
      <li>✓ Your own isolated instance &amp; database</li>
      <li>✓ Bambu · Creality · Prusa · Snapmaker · Voron</li>
      <li>✓ Open source, AGPL-3.0</li>
    </ul>
  </div>
</div>

<style>
  .auth { min-height: 100vh; display: grid; place-items: center; padding: 2rem; }
  .box { width: 100%; max-width: 420px; text-align: center; }
  .lg { margin-bottom: 1rem; display: flex; justify-content: center; }
  .sso { width: 100%; justify-content: center; margin: 1.2rem 0 0.4rem; }
  .or { display: flex; align-items: center; gap: 0.8rem; color: var(--ophq-faint); font-size: 0.8rem; margin: 1rem 0; }
  .or::before, .or::after { content: ''; flex: 1; height: 1px; background: var(--ophq-border); }
  .full { width: 100%; justify-content: center; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; }
  .perks { list-style: none; padding: 0; margin: 1.4rem 0 0; text-align: left; display: grid; gap: 0.4rem; color: var(--ophq-text-2); font-size: 0.9rem; }
  .back { position: fixed; top: 1.4rem; left: 1.6rem; font-size: 0.9rem; }
</style>
