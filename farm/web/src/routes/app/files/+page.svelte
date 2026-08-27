<script>
  // The Files tab is the tenant's model library.
  //
  // It is served by our build of GyroidVault, one container per tenant, reading
  // that tenant's object-storage bucket. Embedded from our own origin rather
  // than a subdomain: the library keeps its own cookie session and has no SSO,
  // and a cookie can only be handed to the browser for the origin it will be
  // sent back to. /api/vault/session opens that session server-side, so the
  // tenant never sees or types a second credential.
  //
  // Falls back to the engine-backed file list when no library is configured,
  // which is any deployment without the image. A missing library should degrade
  // to the previous behaviour, not to a broken tab.
  import { onMount } from 'svelte';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import EngineFiles from '$lib/components/EngineFiles.svelte';

  let state_ = $state('checking'); // checking | library | fallback
  let error = $state(null);
  // The library is served on its own host, because it references assets and
  // its own API absolutely and those paths only resolve at an origin root.
  // The entry point opens the session and bounces to the app, so the iframe
  // never lands on a login screen.
  let vaultUrl = $state('');

  onMount(async () => {
    try {
      const res = await fetch('/api/vault/status', { method: 'GET', credentials: 'include' });
      if (res.ok) {
        const d = await res.json();
        vaultUrl = d.url;
        state_ = 'library';
        return;
      }
      // 503 means no library on this deployment. Anything else is a real
      // failure, but the engine list still works, so show it and say why.
      if (res.status !== 503) {
        const d = await res.json().catch(() => ({}));
        error = d.error || `library unavailable (${res.status})`;
      }
      state_ = 'fallback';
    } catch (e) {
      error = e.message;
      state_ = 'fallback';
    }
  });
</script>

{#if state_ === 'checking'}
  <PageTitle page="Files" />
  <p class="muted">Opening your library…</p>
{:else if state_ === 'library'}
  <PageTitle page="Files" />
  {#if error}<p class="warn">{error}</p>{/if}
  <iframe class="vault" src={vaultUrl} title="Model library"></iframe>
{:else}
  {#if error}<p class="warn">Library unavailable, showing the engine file list. {error}</p>{/if}
  <EngineFiles />
{/if}

<style>
  .vault {
    width: 100%;
    /* Fills the tab rather than scrolling twice: the embedded app has its own
       scroll container, and a short frame would produce a scrollbar inside a
       scrollbar. */
    height: calc(100vh - 8rem);
    border: 1px solid var(--ophq-border);
    border-radius: 12px;
    background: var(--ophq-bg);
  }
  .muted { color: var(--ophq-muted); }
  .warn { color: var(--ophq-warn); }
</style>
