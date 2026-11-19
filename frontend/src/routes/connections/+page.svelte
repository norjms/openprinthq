<script>
  import Glyph from '$lib/Glyph.svelte';
  import Page from '$lib/Page.svelte';
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { session, loadMe } from '$lib/session.svelte.js';

  let list = $state([]);
  let edit = $state(null);      // { kind, label, help, config, secrets: {field: ''}, has: {field: bool}, models, listing }
  let busy = $state('');
  let aiOrder = $state([]);     // [{kind, label, connected, enabled, model, last_test_ok}]
  let lang = $state({ translate: true, connection: 'order' });   // imports in English

  const LABELS = {
    url: 'Address', public_url: 'Public link (for buttons)', host: 'Host name', user: 'API user', username: 'Login name',
    verify_tls: 'Check TLS certificate', target_group: 'Printer group for jobs (optional)', library_path: 'Library path (as Manyfold sees it)', key: 'API key', secret: 'API secret',
    webhook_secret: 'Webhook secret', api_key: 'API key', password: 'Password', token: 'API token', client_id: 'Client ID',
    client_secret: 'Client secret', ntfy_url: 'ntfy topic URL', ntfy_token: 'ntfy access token (optional)',
    telegram_chat_id: 'Telegram chat id', telegram_bot_token: 'Telegram bot token', slack_webhook_url: 'Slack webhook URL',
    printers: 'Label printers (JSON list)', model: 'Model', base_url: 'API address (only for another OpenAI-compatible service)',
    shop: 'Store name (my-store.myshopify.com)', location_id: 'Location id for stock (optional)',
    access_token: 'Admin API access token (older custom apps only)', keystring: 'Keystring', shared_secret: 'Shared secret',
    taxonomy_id: 'Etsy category (taxonomy id)', shipping_profile_id: 'Shipping profile', return_policy_id: 'Return policy (optional)',
    readiness_state_id: 'Processing time', who_made: 'Who made it', when_made: 'When it was made'
  };
  const WHO = [['i_did', 'I did'], ['someone_else', 'Another company or person'], ['collective', 'A member of my shop']];
  const WHEN = ['made_to_order', '2020_2026', '2010_2019', '2007_2009', 'before_2007'];
  const ETSY_PICK = { shipping_profile_id: 'shipping_profiles', return_policy_id: 'return_policies', readiness_state_id: 'readiness_states' };
  let etsy = $state(null);      // GET /api/stores/etsy: configured, connected, shop, redirect_uri, webhook_url, options
  async function loadEtsy() { try { etsy = await api.get('/api/stores/etsy'); } catch { etsy = null; } }
  async function connectEtsy() {
    const r = await attempt(() => api.post('/api/stores/etsy/connect'));
    if (r?.url) window.location.href = r.url;
  }
  async function signOutEtsy() {
    if (!confirm('Sign the hub out of the Etsy shop? Listings and orders stop syncing until you connect again.')) return;
    await attempt(() => api.del('/api/stores/etsy'), 'Signed out of Etsy'); loadEtsy(); load();
  }
  const PRINTERS_EXAMPLE = '[{"name": "Brother QL", "kind": "brother_ql", "host": "192.0.2.20", "port": 9100, "model": "QL-820NWB", "tape": "62x29"}]';
  const isAi = (kind) => kind?.startsWith('ai_');

  async function load() {
    list = await api.get('/api/connections');
    aiOrder = (await api.get('/api/ai/order')).order;
    try { lang = await api.get('/api/import-language'); } catch { /* older hub */ }
  }
  onMount(() => {
    load(); loadEtsy();
    const q = new URLSearchParams(window.location.search);
    if (q.get('etsy') === 'connected') toast(`Etsy shop ${q.get('shop') || ''} connected`, 'ok', 7000);
    if (q.get('etsy') === 'error') toast(`Etsy: ${q.get('msg') || 'sign-in failed'}`, 'bad', 10000);
    if (q.get('etsy')) history.replaceState(null, '', '/connections');
  });

  function open(c) {
    edit = { kind: c.kind, label: c.label, help: c.help, config: { ...c.config }, has: { ...c.secrets },
             secrets: Object.fromEntries(Object.keys(c.secrets).map((k) => [k, ''])) , enabled: c.missing ? true : c.enabled,
             models: [], listing: false, modelsError: '' };
    if ('verify_tls' in edit.config && edit.config.verify_tls == null) edit.config.verify_tls = true;
    if ('printers' in edit.config) edit.config.printers = JSON.stringify(edit.config.printers || [], null, 2);
    if (isAi(c.kind) && !c.missing) listModels();
    if (c.kind === 'etsy') {
      edit.config.who_made ||= 'i_did'; edit.config.when_made ||= 'made_to_order';
      loadEtsy();
    }
  }
  async function listModels() {
    if (!edit) return;
    edit.listing = true; edit.modelsError = '';
    try {
      const secrets = Object.fromEntries(Object.entries(edit.secrets).filter(([, v]) => v !== ''));
      const r = await api.post(`/api/connections/${edit.kind}/models`, { config: edit.config, secrets });
      edit.models = r.models || [];
      if (!edit.models.length) edit.modelsError = 'It answered, but offers no models.';
    } catch (e) {
      edit.modelsError = e?.message || String(e);
    } finally { if (edit) edit.listing = false; }
  }
  async function save() {
    const secrets = Object.fromEntries(Object.entries(edit.secrets).filter(([, v]) => v !== ''));
    busy = edit.kind;
    try {
      await attempt(() => api.put(`/api/connections/${edit.kind}`, { config: edit.config, secrets, enabled: edit.enabled }));
      const r = await api.post(`/api/connections/${edit.kind}/test`);
      toast(r.last_test_ok ? `${edit.label} connected` : `Saved, but the test failed: ${r.last_test_msg}`, r.last_test_ok ? 'ok' : 'bad', 7000);
      edit = null; await load(); loadMe(); loadEtsy();
    } finally { busy = ''; }
  }
  async function clearSecret(f) {
    await attempt(() => api.put(`/api/connections/${edit.kind}`, { secrets: { [f]: '' } }), 'Cleared');
    edit.has[f] = false; load();
  }
  async function test(c) {
    busy = c.kind;
    try { const r = await attempt(() => api.post(`/api/connections/${c.kind}/test`)); toast(r.last_test_ok ? 'Working' : r.last_test_msg, r.last_test_ok ? 'ok' : 'bad', 7000); load(); }
    finally { busy = ''; }
  }
  async function remove(c) {
    if (!confirm(`Disconnect ${c.label} from ${session.me?.team?.name}? ${isAi(c.kind) ? 'The hub stops using it to read labels.' : 'Syncing with it stops.'}`)) return;
    await attempt(() => api.del(`/api/connections/${c.kind}`), 'Disconnected'); load(); loadMe();
  }
  async function move(i, by) {
    const j = i + by;
    if (j < 0 || j >= aiOrder.length) return;
    const kinds = aiOrder.map((x) => x.kind);
    [kinds[i], kinds[j]] = [kinds[j], kinds[i]];
    aiOrder = (await attempt(() => api.put('/api/ai/order', { order: kinds }), 'Order saved')).order;
  }
  const services = $derived(list.filter((c) => !isAi(c.kind)));
  const aiCards = $derived(aiOrder.map((o) => list.find((c) => c.kind === o.kind)).filter(Boolean));
</script>

<svelte:head><title>Connections · OpenPrintHQ</title></svelte:head>

{#snippet card(c)}
  <div class="panel p-4 flex flex-col gap-2">
    <div class="flex items-center gap-2">
      <h3 class="font-semibold flex-1">{c.label}</h3>
      {#if c.missing}<span class="chip">not connected</span>
      {:else if !c.enabled}<span class="chip warn">paused</span>
      {:else if c.last_test_ok === true}<span class="chip ok">working</span>
      {:else if c.last_test_ok === false}<span class="chip bad">failing</span>
      {:else}<span class="chip">saved</span>{/if}
    </div>
    {#if !c.missing && c.config.url}<div class="text-sm muted truncate">{c.config.url}</div>{/if}
    {#if !c.missing && c.config.model}<div class="text-sm muted truncate">Model: <span class="font-mono">{c.config.model}</span></div>{/if}
    {#if c.last_test_ok === false}<div class="text-xs" style="color:var(--bad)">{c.last_test_msg}</div>{/if}
    {#if c.last_test_at}<div class="text-xs muted">checked {ago(c.last_test_at)}</div>{/if}
    {#if c.kind === 'etsy' && !c.missing && etsy}
      {#if etsy.connected}<div class="text-sm muted">Signed in to <b>{etsy.shop?.shop_name}</b></div>
      {:else}<div class="text-sm" style="color:var(--warn)">Saved. Now sign in to the shop with Connect Etsy.</div>{/if}
    {/if}
    <div class="flex gap-2 mt-auto pt-1">
      <button class="btn sm primary" onclick={() => open(c)}>{c.missing ? 'Connect' : 'Edit'}</button>
      {#if c.kind === 'etsy' && !c.missing && etsy}
        {#if etsy.connected}<button class="btn sm" onclick={signOutEtsy}>Sign out of Etsy</button>
        {:else}<button class="btn sm primary" onclick={connectEtsy}>Connect Etsy</button>{/if}
      {/if}
      {#if !c.missing}
        <button class="btn sm" disabled={busy === c.kind} onclick={() => test(c)}>{busy === c.kind ? 'Testing…' : 'Test'}</button>
        <button class="btn sm danger" onclick={() => remove(c)}>Disconnect</button>
      {/if}
    </div>
  </div>
{/snippet}

<Page title="Connections" description={`The outside systems ${session.me?.team?.name || 'this team'} syncs with. Each team keeps its own settings; two teams can point at the same instance or at their own. Passwords and keys are stored encrypted and never shown again.`}>

  <div class="grid gap-3 md:grid-cols-2">
    {#each services as c}{@render card(c)}{/each}
  </div>

  <h2 class="text-xl font-bold mt-8 mb-1">Label-reading AI</h2>
  <p class="muted text-sm mb-3">The phone app reads a spool label with the phone's own AI first (Gemini Nano, Apple Intelligence or the downloaded Gemma model). When the phone cannot, or you tap <b>Ask the hub</b>, the hub sends the photo to these connections in this order and uses the first one that reads it. Label photos go to the provider you connect, under its API terms.</p>

  <div class="panel p-3 mb-3">
    <div class="text-sm font-medium mb-2">Order the hub tries them in</div>
    <ol class="space-y-1">
      {#each aiOrder as o, i (o.kind)}
        <li class="flex items-center gap-2 text-sm">
          <span class="w-5 text-right muted">{i + 1}.</span>
          <span class="flex-1">{o.label}{#if o.model}<span class="muted font-mono ml-2">{o.model}</span>{/if}</span>
          {#if !o.connected}<span class="chip">not connected, skipped</span>
          {:else if !o.enabled}<span class="chip warn">paused, skipped</span>
          {:else if o.last_test_ok === false}<span class="chip bad">failing</span>
          {:else if !o.model}<span class="chip warn">no model, skipped</span>{/if}
          <button class="btn sm" aria-label="Move {o.label} up" disabled={i === 0} onclick={() => move(i, -1)}><Glyph name="arrow-up" /></button>
          <button class="btn sm" aria-label="Move {o.label} down" disabled={i === aiOrder.length - 1} onclick={() => move(i, 1)}><Glyph name="arrow-down" /></button>
        </li>
      {/each}
    </ol>
  </div>

  <div class="grid gap-3 md:grid-cols-2">
    {#each aiCards as c}{@render card(c)}{/each}
  </div>

  <h2 class="text-xl font-bold mt-8 mb-1">Imports in English</h2>
  <p class="muted text-sm mb-3">Models imported from Printables, MakerWorld and the others sometimes come in another language. The phone app puts the name and description into English with the phone's own AI before importing; the hub does the rest (file names, long descriptions, imports from this website) with the AI connection you choose here. The original wording is kept in the model's notes.</p>
  <div class="panel p-3 mb-3 flex flex-wrap items-center gap-3 text-sm">
    <label class="flex items-center gap-2"><input type="checkbox" bind:checked={lang.translate} /> Put imports into English</label>
    <label class="flex items-center gap-2">with
      <select class="input" bind:value={lang.connection} disabled={!lang.translate}>
        <option value="order">the order above</option>
        {#each aiOrder.filter((o) => o.connected) as o}<option value={o.kind}>{o.label}</option>{/each}
      </select></label>
    <button class="btn sm" onclick={async () => { lang = await attempt(() => api.put('/api/import-language', { translate: lang.translate, connection: lang.connection }), 'Saved'); }}>Save</button>
    <span class="text-xs muted">A local connection (Ollama) costs nothing per import.</span>
  </div>
</Page>

{#if edit}
  <div class="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-4" role="presentation" onclick={(e) => e.target === e.currentTarget && (edit = null)}>
    <div class="panel p-5 w-full max-w-lg max-h-[90vh] overflow-y-auto" role="dialog" aria-modal="true" aria-label="{edit.label} connection">
      <h3 class="font-semibold text-lg mb-1">{edit.label}</h3>
      <p class="text-sm muted mb-4">{edit.help}</p>
      {#if edit.kind === 'etsy' && etsy}
        <div class="text-xs muted mb-4 space-y-1">
          <div>Redirect URI for your Etsy app: <span class="font-mono select-all">{etsy.redirect_uri}</span></div>
          <div>Webhook address for Etsy's Webhook portal (order.paid, order.canceled, order.shipped): <span class="font-mono select-all">{etsy.webhook_url}</span></div>
          {#if etsy.connected && !etsy.options}<div style="color:var(--warn)">Could not read the shop's shipping profiles: {etsy.options_error}</div>
          {:else if !etsy.connected}<div>Sign in with Connect Etsy after saving; the shipping profile and processing time lists fill in then.</div>{/if}
        </div>
      {/if}
      <form class="space-y-3" onsubmit={(e) => { e.preventDefault(); save(); }}>
        {#each Object.keys(edit.config) as f}
          {#if f === 'printers'}
            <label class="block text-sm"><span class="font-medium">{LABELS[f]}</span>
              <textarea class="input w-full mt-1 font-mono text-xs" rows="8" bind:value={edit.config[f]} placeholder={PRINTERS_EXAMPLE}></textarea>
              <span class="text-xs muted">kind brother_ql (tape 62x29, 62, 62x100 or 29x90) or zpl (tape WxH in mm, dpi 203 or 300). The phone app can add them too.</span></label>
          {:else if edit.kind === 'etsy' && ETSY_PICK[f] && etsy?.options?.[ETSY_PICK[f]]?.length}
            <label class="block text-sm"><span class="font-medium">{LABELS[f]}</span>
              <select class="input w-full mt-1" bind:value={edit.config[f]}>
                <option value="">{f === 'return_policy_id' ? 'None' : 'Pick one'}</option>
                {#each etsy.options[ETSY_PICK[f]] as o}<option value={String(o.id)}>{o.name}</option>{/each}
              </select></label>
          {:else if edit.kind === 'etsy' && f === 'who_made'}
            <label class="block text-sm"><span class="font-medium">{LABELS[f]}</span>
              <select class="input w-full mt-1" bind:value={edit.config[f]}>{#each WHO as [v, l]}<option value={v}>{l}</option>{/each}</select></label>
          {:else if edit.kind === 'etsy' && f === 'when_made'}
            <label class="block text-sm"><span class="font-medium">{LABELS[f]}</span>
              <select class="input w-full mt-1" bind:value={edit.config[f]}>{#each WHEN as v}<option value={v}>{v === 'made_to_order' ? 'Made to order' : v.replace('_', ' to ').replace('before to ', 'before ')}</option>{/each}</select></label>
          {:else if f === 'verify_tls'}
            <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={edit.config[f]} /> {LABELS[f]}</label>
          {:else if f === 'model' && isAi(edit.kind)}
            <div class="text-sm">
              <label for="ai-model" class="font-medium">{LABELS[f]}</label>
              <div class="flex gap-2 mt-1">
                {#if edit.models.length}
                  <select id="ai-model" class="input flex-1 font-mono" bind:value={edit.config[f]}>
                    {#if edit.config[f] && !edit.models.includes(edit.config[f])}<option value={edit.config[f]}>{edit.config[f]} (not offered)</option>{/if}
                    {#if !edit.config[f]}<option value="">Pick a model</option>{/if}
                    {#each edit.models as m}<option value={m}>{m}</option>{/each}
                  </select>
                {:else}
                  <input id="ai-model" class="input flex-1 font-mono" bind:value={edit.config[f]} autocomplete="off" placeholder="List the models, or type a name" />
                {/if}
                <button type="button" class="btn" disabled={edit.listing} onclick={listModels}>{edit.listing ? 'Listing…' : 'List models'}</button>
              </div>
              {#if edit.modelsError}<div class="text-xs mt-1" style="color:var(--bad)">{edit.modelsError}</div>
              {:else}<div class="text-xs muted mt-1">{edit.kind === 'ai_ollama' ? 'Fill in the address, then list the models the server has.' : 'Enter the API key, then list the models it can use. Pick one that reads images.'}</div>{/if}
            </div>
          {:else}
            <label class="block text-sm"><span class="font-medium">{LABELS[f] || f}</span>
              <input class="input w-full mt-1" bind:value={edit.config[f]} placeholder={f.endsWith('url') ? 'https://' : ''} autocomplete="off" /></label>
          {/if}
        {/each}
        {#each Object.keys(edit.secrets) as f}
          <label class="block text-sm"><span class="font-medium">{LABELS[f] || f}{#if edit.kind === 'ai_ollama'} (optional){/if}</span>
            {#if edit.has[f]}<span class="chip ok ml-2">saved</span> <button type="button" class="text-xs muted hover:underline ml-1" onclick={() => clearSecret(f)}>clear</button>{/if}
            <input class="input w-full mt-1" type="password" bind:value={edit.secrets[f]} autocomplete="new-password"
              placeholder={edit.has[f] ? 'leave empty to keep the saved value' : ''} /></label>
        {/each}
        <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={edit.enabled} /> {isAi(edit.kind) ? 'Use this to read labels' : 'Sync with this service'}</label>
        <div class="flex gap-2 justify-end pt-2">
          <button type="button" class="btn" onclick={() => (edit = null)}>Cancel</button>
          <button type="submit" class="btn primary" disabled={busy === edit.kind}>{busy === edit.kind ? 'Saving…' : 'Save and test'}</button>
        </div>
      </form>
    </div>
  </div>
{/if}
