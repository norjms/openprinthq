<script>
  import PanelStub from '$lib/components/PanelStub.svelte';
</script>
<PanelStub title="Settings"
  blurb="Manage your instance, account, notifications and integrations."
  roadmap={[
    'Account + SSO (Authentik OIDC) session management',
    'Instance controls: restart, backup/restore, version',
    'Notification channels',
    'API keys and webhooks'
  ]} />
