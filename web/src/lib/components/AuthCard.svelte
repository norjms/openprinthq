<script>
  // A centred glass card on the page gradient, with the product mark above it:
  // sign in, create an account, and the other pages people see signed out.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import Logo from './Logo.svelte';
  /** @type {{ title: string, wide?: boolean, children?: import('svelte').Snippet }} */
  let { title, wide = false, children } = $props();
</script>

<div class="ah-root ah-page auth">
  <div class="auth-col" class:wide>
    <div class="auth-mark"><Logo /></div>
    <section class="ah-glass auth-card">
      <h1 class="ah-title">{title}</h1>
      {@render children?.()}
    </section>
  </div>
</div>

<style>
  .auth { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
  .auth-col { width: min(460px, 100%); }
  .auth-col.wide { width: min(560px, 100%); }
  .auth-mark { display: flex; justify-content: center; margin-bottom: 18px; }
  .auth-card { display: flex; flex-direction: column; gap: 16px; padding: 28px; }
  .auth-card :global(h1) { font-size: 30px; margin: 0; }
  .auth-card :global(p) { margin: 0; }
</style>
