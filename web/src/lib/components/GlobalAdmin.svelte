<script>
  // Settings → Global Admin (owner-only). Two sections: site Branding and the
  // Instances admin console (invites, users, instances, per-instance features +
  // storage quota). The Settings page hides this tab for non-owners, and every
  // /api/admin/* route is requireOwner-gated, so this is defence-in-depth.
  import BrandingEditor from './BrandingEditor.svelte';
  import InstancesAdmin from './InstancesAdmin.svelte';
</script>

<div class="ga">
  <div class="ga-head">
    <h2>Global Admin</h2>
    <p class="muted">Site-wide settings that only you (the owner) can change: branding and instance management.</p>
  </div>

  <section class="ga-sec">
    <BrandingEditor />
  </section>

  <section class="ga-sec">
    <h3 class="ga-sech">Instances</h3>
    <p class="muted tiny">Invites, users, instances, per-instance features and storage quota.</p>
    <InstancesAdmin />
  </section>
</div>

<style>
  .ga-head h2 { margin: 0 0 0.2rem; font-size: 1.5rem; }
  .ga-head p { margin: 0 0 1.2rem; max-width: 62ch; font-size: 0.9rem; }
  .ga-sec { margin-top: 1.6rem; }
  .ga-sech { margin: 0 0 0.2rem; font-size: 1.25rem; }
  .ga-sec .tiny { font-size: 0.82rem; margin: 0 0 0.4rem; }
</style>
