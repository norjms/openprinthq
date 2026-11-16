<script>
  // Phone app notifications: how they work (the relay's own /status decides part of the text), each person's
  // choice, their phones, and for admins the installation switch and relay address.
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';

  let s = $state(null);
  let relayEdit = $state('');
  async function load() { s = await api.get('/api/push/state'); relayEdit = s.relay_url || ''; }
  onMount(load);

  async function choose(choice) {
    s = await attempt(() => api.put('/api/push/choice', { choice }),
      choice === 'off' ? 'Your alerts will come by email' : choice === null ? 'Phone notifications off' : 'Saved');
  }
  async function remove(d) { s = await attempt(() => api.del(`/api/push/devices/${d.id}`), 'Phone removed'); }
  async function test() { await attempt(() => api.post('/api/push/devices/test'), 'Test sent to your phones'); }
  async function setMode(mode) { s = await attempt(() => api.put('/api/push/settings', { mode }), 'Saved'); }
  async function setRelay() { s = await attempt(() => api.put('/api/push/settings', { relay_url: relayEdit }), 'Relay saved'); relayEdit = s.relay_url || ''; }
  const st = $derived(s?.disclaimer?.relay_status || {});
</script>

{#if s}
  <div class="panel p-4 mt-4" data-testid="phone-push">
    <div class="flex flex-wrap items-center gap-3 mb-3">
      <h2 class="font-semibold mr-auto">Phone app notifications</h2>
      <span class="chip {s.available ? 'ok' : ''}">{s.available ? 'On for this installation' : 'Off for this installation'}</span>
      {#if s.devices.length}<button class="btn sm" onclick={test}>Send me a test</button>{/if}
    </div>
    <div class="grid gap-4" style="grid-template-columns: minmax(0, 3fr) minmax(0, 2fr)">
      <div>
        <h3 class="text-sm font-semibold mb-1">{s.disclaimer.title}</h3>
        <ul class="text-sm list-disc pl-5 space-y-1">
          {#each s.disclaimer.points as p}<li>{p}</li>{/each}
        </ul>
        <p class="text-xs muted mt-2">
          Relay: {s.relay_url || 'none set'}{#if s.relay_url} · {st.reachable ? `answering, version ${st.version || '?'}` : 'not answering'}{#if st.reachable} · delivery log {st.delivery_log ? 'on (testing)' : 'off'}{/if}{/if}
          · <a href={s.disclaimer.source} target="_blank" rel="noopener">relay source code</a>
        </p>
      </div>
      <div class="text-sm">
        <h3 class="font-semibold mb-1">For you</h3>
        <div class="flex flex-col gap-1 mb-3">
          <label class="flex items-center gap-2"><input type="radio" name="pushchoice" checked={s.choice === 'on'} disabled={!s.available} onchange={() => choose('on')} /> Phone notifications (turn them on in the app)</label>
          <label class="flex items-center gap-2"><input type="radio" name="pushchoice" checked={s.choice === 'off'} onchange={() => choose('off')} /> Email instead, and map filament on the web</label>
          <label class="flex items-center gap-2"><input type="radio" name="pushchoice" checked={!s.choice} onchange={() => choose(null)} /> Neither</label>
        </div>
        {#if s.devices.length}
          <h3 class="font-semibold mb-1">Your phones</h3>
          <ul class="flex flex-col gap-1">
            {#each s.devices as d (d.id)}
              <li class="flex items-center gap-2">
                <span class="mr-auto">{d.label || (d.platform === 'apns' ? 'iPhone or iPad' : 'Android')}{#if d.app_version} <span class="muted">· app {d.app_version}</span>{/if}
                  <span class="muted text-xs">· {d.last_sent_at ? `last alert ${ago(d.last_sent_at)}` : 'no alerts yet'}</span>
                  {#if d.last_error}<span class="chip bad">{d.last_error}</span>{/if}</span>
                <button class="btn sm" onclick={() => remove(d)}>Remove</button>
              </li>
            {/each}
          </ul>
        {:else if s.choice === 'on'}
          <p class="muted">No phone yet: open the ArtisanHub app and turn notifications on.</p>
        {/if}
        {#if can('settings')}
          <h3 class="font-semibold mt-4 mb-1">This installation</h3>
          <div class="flex gap-2 mb-2">
            <button class="btn sm {s.mode === 'on' ? 'primary' : ''}" disabled={s.mode === 'on'} onclick={() => setMode('on')}>Use the relay</button>
            <button class="btn sm {s.mode === 'off' ? 'primary' : ''}" disabled={s.mode === 'off'} onclick={() => setMode('off')}>Off: email everyone instead</button>
          </div>
          <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); setRelay(); }}>
            <input class="input" placeholder="https://push.example.com" bind:value={relayEdit} aria-label="Push relay address" />
            <button class="btn sm" type="submit">Save</button>
          </form>
          <p class="text-xs muted mt-1">Any relay running artisanhub-push-relay with keys for this app. Empty: the installation's default.</p>
        {/if}
      </div>
    </div>
  </div>
{/if}
