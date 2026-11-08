<script>
  // SPDX-License-Identifier: AGPL-3.0-or-later
  // Shared "can't reach the engine" card. Retry re-authenticates when the
  // sign-in has expired (see $lib/session.js) instead of repeating a request
  // that the gateway will keep redirecting.
  import { sessionExpired, retryOrReauth, reauth } from '$lib/session.js';
  let { error, onretry, class: cls = '' } = $props();
</script>

<div class="card card-pad {cls}">
  {#if $sessionExpired}
    <h3>Signed out</h3>
    <p class="muted">Your sign-in has expired. Sign in again to reconnect to your engine.</p>
    <button class="btn btn-primary btn-sm" onclick={reauth}>Sign in again</button>
  {:else}
    <h3>Engine unreachable</h3>
    <p class="muted">{error}</p>
    <button class="btn btn-ghost btn-sm" onclick={() => retryOrReauth(onretry)}>Retry</button>
  {/if}
</div>
