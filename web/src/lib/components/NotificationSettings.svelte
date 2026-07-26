<script>
  // OpenPrintHQ — notification channels (Discord/Telegram/email/Pushover/ntfy/webhook)
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  const TYPES = {
    discord: { label: 'Discord', fields: [['webhook_url', 'Webhook URL', 'text']] },
    telegram: { label: 'Telegram', fields: [['bot_token', 'Bot token', 'text'], ['chat_id', 'Chat ID', 'text']] },
    ntfy: { label: 'ntfy', fields: [['topic', 'Topic', 'text'], ['server', 'Server (optional, default ntfy.sh)', 'text']] },
    pushover: { label: 'Pushover', fields: [['app_token', 'App token', 'text'], ['user_key', 'User key', 'text']] },
    webhook: { label: 'Generic webhook', fields: [['webhook_url', 'URL', 'text']] },
    email: { label: 'Email (SMTP)', fields: [
      ['smtp_server', 'SMTP server', 'text'], ['smtp_port', 'Port', 'number'],
      ['username', 'Username', 'text'], ['password', 'Password', 'password'],
      ['from_email', 'From', 'text'], ['to_email', 'To', 'text']] }
  };
  const EVENTS = [
    ['on_print_complete', 'Print complete'], ['on_print_failed', 'Print failed'],
    ['on_print_stopped', 'Print stopped'], ['on_print_start', 'Print start'],
    ['on_print_progress', 'Progress (25/50/75%)'], ['on_printer_offline', 'Printer offline'],
    ['on_printer_error', 'Printer error / HMS'], ['on_ai_failure_detection', 'AI failure detected'],
    ['on_filament_low', 'Filament low'], ['on_maintenance_due', 'Maintenance due'],
    ['on_ams_humidity_high', 'AMS humidity high']
  ];

  let providers = $state([]);
  let loading = $state(true);
  let adding = $state(false);
  let busy = $state(null);
  let msg = $state(null);
  let f = $state(newForm());

  function newForm() {
    return { name: '', provider_type: 'discord', config: {},
      on_print_complete: true, on_print_failed: true, on_print_stopped: true };
  }
  async function load() {
    loading = true;
    try { providers = await api.notifProviders(); } catch { providers = []; }
    finally { loading = false; }
  }
  onMount(load);

  async function create() {
    if (!f.name.trim()) { msg = { kind: 'err', text: 'Name required.' }; return; }
    busy = 'create'; msg = null;
    try {
      const body = { name: f.name.trim(), provider_type: f.provider_type, enabled: true, config: f.config };
      for (const [k] of EVENTS) if (f[k]) body[k] = true;
      await api.notifCreate(body);
      adding = false; f = newForm();
      await load();
      msg = { kind: 'ok', text: 'Channel added.' };
    } catch (e) { msg = { kind: 'err', text: e.message || 'could not add channel' }; }
    finally { busy = null; }
  }
  async function toggle(p) {
    busy = p.id;
    try { await api.notifUpdate(p.id, { enabled: !p.enabled }); await load(); }
    catch (e) { msg = { kind: 'err', text: e.message || 'update failed' }; }
    finally { busy = null; }
  }
  async function test(p) {
    busy = p.id; msg = null;
    try { await api.notifTest(p.id); msg = { kind: 'ok', text: `Test sent to ${p.name}.` }; }
    catch (e) { msg = { kind: 'err', text: e.message || 'test failed' }; }
    finally { busy = null; }
  }
  async function del(p) {
    busy = p.id;
    try { await api.notifDelete(p.id); await load(); }
    catch (e) { msg = { kind: 'err', text: e.message || 'delete failed' }; }
    finally { busy = null; }
  }
  const enabledEvents = (p) => EVENTS.filter(([k]) => p[k]).map(([, l]) => l);
</script>

<div class="card card-pad notif">
  <div class="nh">
    <span class="eyebrow">Notifications</span>
    <button class="btn btn-ghost btn-sm" onclick={() => { adding = !adding; f = newForm(); }}>{adding ? 'Close' : '+ Add channel'}</button>
  </div>
  <p class="muted">Get alerted on print events via Discord, Telegram, email, Pushover, ntfy or a generic webhook.</p>

  {#if adding}
    <div class="form">
      <div class="frow">
        <div class="fld grow"><label for="nn">Name</label><input id="nn" class="input" bind:value={f.name} placeholder="My Discord" /></div>
        <div class="fld"><label for="nt">Type</label>
          <select id="nt" class="input" bind:value={f.provider_type} onchange={() => (f.config = {})}>
            {#each Object.entries(TYPES) as [k, v]}<option value={k}>{v.label}</option>{/each}
          </select>
        </div>
      </div>
      <div class="cfg">
        {#each TYPES[f.provider_type].fields as [key, label, type]}
          <div class="fld"><label for={'c-' + key}>{label}</label>
            <input id={'c-' + key} class="input" {type} value={f.config[key] ?? ''} oninput={(e) => (f.config = { ...f.config, [key]: e.target.value })} />
          </div>
        {/each}
      </div>
      <span class="evh">Notify on</span>
      <div class="events">
        {#each EVENTS as [key, label]}
          <label class="ev"><input type="checkbox" checked={!!f[key]} onchange={(e) => (f[key] = e.target.checked)} /><span>{label}</span></label>
        {/each}
      </div>
      <div class="flex gap"><button class="btn btn-primary btn-sm" onclick={create} disabled={busy === 'create'}>{busy === 'create' ? 'Adding…' : 'Add channel'}</button></div>
    </div>
  {/if}

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if providers.length === 0}
    <p class="muted small">No channels yet.</p>
  {:else}
    <div class="plist">
      {#each providers as p (p.id)}
        <div class="prov" class:off={!p.enabled}>
          <div class="pmain">
            <span class="pn">{p.name} <span class="chip mono">{TYPES[p.provider_type]?.label || p.provider_type}</span></span>
            <span class="muted tiny ev-sum">{enabledEvents(p).join(' · ') || 'no events'}</span>
          </div>
          <div class="pacts">
            <label class="sw"><input type="checkbox" checked={p.enabled} onchange={() => toggle(p)} disabled={busy === p.id} /><span>on</span></label>
            <button class="btn btn-ghost btn-xs" onclick={() => test(p)} disabled={busy === p.id}>Test</button>
            <button class="btn btn-ghost btn-xs danger-text" onclick={() => del(p)} disabled={busy === p.id}>Delete</button>
          </div>
        </div>
      {/each}
    </div>
  {/if}
  {#if msg}<p class={msg.kind === 'ok' ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}
</div>

<style>
  .notif { margin-top: 1.2rem; }
  .nh { display: flex; align-items: center; justify-content: space-between; }
  .notif > p { margin: 0.3rem 0 0.9rem; font-size: 0.9rem; }
  .form { border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); padding: 1rem; margin-bottom: 1rem; background: var(--ophq-bg-2); display: flex; flex-direction: column; gap: 0.7rem; }
  .frow { display: flex; gap: 0.8rem; flex-wrap: wrap; }
  .fld { display: flex; flex-direction: column; gap: 0.3rem; } .fld.grow { flex: 1; min-width: 180px; }
  .fld label { font-size: 0.8rem; color: var(--ophq-text-2); }
  .cfg { display: flex; flex-direction: column; gap: 0.6rem; }
  .evh { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ophq-muted); }
  .events { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.4rem; }
  .ev { display: flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; color: var(--ophq-text-2); }
  .ev input, .sw input { width: auto; accent-color: var(--ophq-primary); }
  .plist { display: flex; flex-direction: column; gap: 0.5rem; }
  .prov { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 0.6rem 0.8rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); }
  .prov.off { opacity: 0.55; }
  .pmain { display: flex; flex-direction: column; gap: 0.2rem; }
  .pn { font-weight: 600; font-size: 0.92rem; display: flex; align-items: center; gap: 0.5rem; }
  .chip { font-size: 0.7rem; padding: 0.05rem 0.4rem; border: 1px solid var(--ophq-border); border-radius: 999px; color: var(--ophq-text-2); }
  .ev-sum { font-size: 0.76rem; }
  .pacts { display: flex; align-items: center; gap: 0.5rem; }
  .sw { display: flex; align-items: center; gap: 0.3rem; font-size: 0.76rem; color: var(--ophq-text-2); }
  .small { font-size: 0.9rem; }
  .btn-xs { padding: 0.12rem 0.5rem; font-size: 0.72rem; }
  .danger-text { color: var(--ophq-danger); }
  .ok-msg { color: var(--ophq-success); font-size: 0.88rem; margin: 0.7rem 0 0; }
  .err { color: var(--ophq-danger); font-size: 0.88rem; margin: 0.7rem 0 0; }
  @media (max-width: 700px) { .events { grid-template-columns: 1fr 1fr; } }
</style>
