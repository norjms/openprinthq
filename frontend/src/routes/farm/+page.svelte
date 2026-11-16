<script>
  import Page from '$lib/Page.svelte';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { Icon } from 'artisanhub-ui';
  import Glyph from '$lib/Glyph.svelte';
  import { api, fmtTime, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';
  import Timeline from '$lib/farm/Timeline.svelte';
  import Maintenance from '$lib/farm/Maintenance.svelte';
  import Plates from '$lib/farm/Plates.svelte';
  import Filament from '$lib/farm/Filament.svelte';
  import Alarms from '$lib/farm/Alarms.svelte';

  const VIEWS = { printers: ['Printers', 'Every printer in the farm. Click one to control it.'], cameras: ['Cameras', "A frame from each printer's camera, every 10 seconds."],
    queue: ['Print queue', 'Jobs waiting and printing, as the hub sees them.'], filament: ['Filament', 'What is loaded in each printer, and the spools the farm knows.'] };
  let view = $state(page.url.searchParams.get('view') || 'checks');
  // The sidebar links here with ?view=; follow it when the same page is opened with another view.
  $effect(() => { const v = page.url.searchParams.get('view') || 'checks'; if (v !== view) { view = v; if (v === 'settings') loadSettings(); if (v === 'history') loadStats(); } });
  function pick(k) {
    view = k; if (k === 'settings') loadSettings(); if (k === 'history') loadStats();
    goto(k === 'checks' ? '/farm' : `/farm?view=${k}`, { replaceState: true, noScroll: true, keepFocus: true });
  }
  let tick = $state(Date.now());
  const shot = (p) => `/api/mobile/v1/printers/${encodeURIComponent(p.name)}/snapshot?t=${tick}`;
  let noCam = $state({});
  let d = $state(null);
  let busy = $state(false);
  let failing = $state(null);   // check id with the fail form open
  let form = $state({ reason: '', note: '', reprint: false, set_ready: true });
  let q = $state('');
  let found = $state(null);
  let st = $state(null);      // farm settings
  async function loadSettings() { st = await api.get('/api/farm/settings'); }
  async function saveSettings(patch, msg = 'Saved') {
    st = await attempt(() => api.put('/api/farm/settings', patch), msg);
  }
  function toggleChannel(ev, ch) {
    const cur = st.alert_rules.events[ev] || [];
    const next = cur.includes(ch) ? cur.filter((c) => c !== ch) : [...cur, ch];
    saveSettings({ alert_rules: { events: { ...st.alert_rules.events, [ev]: next } } });
  }
  async function testAlerts() {
    const r = await attempt(() => api.post('/api/farm/alerts/test'));
    toast(r.message, r.ok ? 'ok' : 'bad', 8000);
  }
  async function setRouting(v) {
    const msg = v === 'hub'
      ? 'Send paid WooCommerce orders to the farm from the hub?'
      : 'Stop sending orders to the farm? Order jobs are then queued by hand.';
    if (!confirm(msg)) return;
    await saveSettings({ order_routing: v }, 'Order routing changed');
  }

  let loadErr = $state('');
  async function load() {
    try { d = await api.get('/api/farm'); loadErr = ''; } catch (e) { loadErr = e.message || String(e); return; }
    loadHidden(); loadAlarmSummary();
  }
  // Alarm feed counts for the tab label (farms with the alarms capability)
  let alarmSum = $state(null);
  async function loadAlarmSummary() {
    if (!d?.capabilities?.includes('alarms')) { alarmSum = null; return; }
    try { alarmSum = await api.get('/api/farm/alarms/summary'); } catch { alarmSum = null; }
  }
  // Commands a printer's connection supports (PrusaLink has no home or temperatures); none listed means all.
  const allows = (p, c) => !p.commands || p.commands.includes(c);
  // Printer codes hidden as nuisances: never shown as errors (also hidden from the phone app).
  let hiddenCodes = $state([]);
  async function loadHidden() { try { hiddenCodes = (await api.get('/api/farm/hidden-errors')).hidden; } catch { hiddenCodes = []; } }
  async function hideCode(p, everywhere) {
    await attempt(() => api.post('/api/farm/hidden-errors', { code: p.reported_code, printer: everywhere ? null : p.name }),
      everywhere ? 'Hidden on every printer' : `Hidden on ${p.name}`);
    load();
  }
  async function unhideCode(c) { await attempt(() => api.del(`/api/farm/hidden-errors/${c.code}`), 'Shown again'); load(); }
  let sel = $state(null);         // printer name with its control panel open
  let heat = $state('PLA');
  const farmName = $derived(d?.farm_label || 'the farm');
  const has = (cap) => (d?.capabilities || []).includes(cap);
  async function cmd(p, command, label, args = null, confirm = false) {
    busy = true;
    try {
      await attempt(() => api.post(`/api/farm/printers/${encodeURIComponent(p.name)}/command`, { command, args, confirm }), label);
      setTimeout(load, 2500);
    } finally { busy = false; }
  }
  async function cancelPrint(p) {
    if (!confirm(`Stop the print on ${p.name}? The job is cancelled.`)) return;
    await cmd(p, 'cancel', `Print on ${p.name} cancelled`, null, true);
  }
  async function service(p, down) {
    const reason = down ? prompt(`Why is ${p.name} out of service? (optional)`, '') : null;
    if (down && reason === null) return;
    busy = true;
    try {
      await attempt(() => api.post(`/api/farm/printers/${encodeURIComponent(p.name)}/service`, { down, reason }),
                    down ? `${p.name} is out of service: no new jobs` : `${p.name} is back in service`);
      await load();
    } finally { busy = false; }
  }
  async function setPriority(j, v) {
    await attempt(() => api.post(`/api/farm/jobs/${encodeURIComponent(j.external_id)}/priority`, { priority: Number(v) }), 'Priority set');
    load();
  }
  async function cancelJob(j) {
    if (!confirm(j.status === 'printing' ? `Stop "${j.name}"? It is printing on ${j.printer}.` : `Take "${j.name}" out of the queue?`)) return;
    await attempt(() => api.post(`/api/farm/jobs/${encodeURIComponent(j.external_id)}/cancel`), 'Cancelled');
    load();
  }
  // The farm's own answer to "why isn't this printing", printer by printer (farms with the preflight capability)
  let why = $state({});
  async function explain(j) {
    if (why[j.id]) { why = { ...why, [j.id]: null }; return; }
    const r = await attempt(() => api.get(`/api/farm/jobs/${j.id}/preflight`));
    if (r) why = { ...why, [j.id]: r };
  }
  // Queue statistics from the farm (farms with the queue_stats capability)
  let stats = $state(null);
  async function loadStats() {
    if (!d?.capabilities?.includes('queue_stats')) return;
    try { stats = await api.get('/api/farm/stats?days=30'); } catch { stats = null; }
  }
  const pct = (x) => (x == null ? '-' : `${Math.round(x * 100)}%`);   // success_rate is a fraction
  async function sync() {
    busy = true;
    try { const r = await attempt(() => api.post('/api/farm/sync')); toast(`Synced ${r.jobs} jobs`); await load(); }
    finally { busy = false; }
  }
  async function pass(c, setReady = true) {
    busy = true;
    try {
      const r = await attempt(() => api.post(`/api/plate-checks/${c.id}/pass`, { set_ready: setReady }));
      toast(r.ready_error ? `Passed, but Ready wasn't set: ${r.ready_error}` : setReady ? `${c.printer} is Ready` : 'Passed', r.ready_error ? 'warn' : 'ok', r.ready_error ? 8000 : 3500);
      await load();
    } finally { busy = false; }
  }
  function openFail(c) { failing = c.id; form = { reason: '', note: '', reprint: false, set_ready: true }; }
  async function fail(c) {
    if (!form.reason) { toast('Pick what went wrong', 'bad'); return; }
    busy = true;
    try {
      const r = await attempt(() => api.post(`/api/plate-checks/${c.id}/fail`, form));
      toast(r.ready_error ? `Recorded. ${r.ready_error}` : `Recorded${r.reprint_job_id ? ', reprint queued' : ''}${r.ready_set ? `, ${c.printer} is Ready` : ''}`, r.ready_error ? 'warn' : 'ok', r.ready_error ? 8000 : 3500);
      failing = null; await load();
    } finally { busy = false; }
  }
  async function skip(c) {
    if (!confirm(`Dismiss the plate check for ${c.printer} without recording anything? The printer stays as it is in ${farmName}.`)) return;
    await attempt(() => api.post(`/api/plate-checks/${c.id}/skip`)); await load();
  }
  async function retryReady(c) {
    const r = await attempt(() => api.post(`/api/plate-checks/${c.id}/retry-ready`));
    toast(r.ready_set ? `${c.printer} is Ready` : `Still not set: ${r.ready_error}`, r.ready_set ? 'ok' : 'bad', 7000); await load();
  }
  async function toggleGate() {
    await attempt(() => api.put('/api/farm/settings', { plate_checks: !d.plate_checks_on })); await load();
  }
  async function search() {
    found = await api.get(`/api/farm/jobs?q=${encodeURIComponent(q)}&limit=200`);
  }
  const statusCls = (s) => (s === 'completed' ? 'ok' : s === 'failed' ? 'bad' : ['printing', 'assigned', 'resuming'].includes(s) ? 'live' : ['waiting_filament', 'held', 'paused'].includes(s) ? 'warn' : '');
  const statusLabel = (s) => (s || '').replace('_', ' ');
  const outcomeCls = (s) => (s === 'completed' ? 'ok' : s === 'failed' ? 'bad' : 'warn');

  onMount(() => {
    load();
    const t = setInterval(load, 30000);
    const c = setInterval(() => { if (view === 'cameras') tick = Date.now(); }, 10000);
    return () => { clearInterval(t); clearInterval(c); };
  });
</script>

<Page title={VIEWS[view]?.[0] || 'Farm overview'} description={VIEWS[view]?.[1] || `${d?.farm_label || 'The farm'}'s queue as the hub sees it, and a plate check after every print. Passing a plate sets the printer Ready in ${farmName}. Click a printer to control it.`}>
{#snippet actions()}
  {#if d}<span class="ah-muted">{d.synced_at ? `Synced ${ago(d.synced_at)}` : 'Not synced yet'}</span>{/if}
  {#if can('farm')}<button class="btn" disabled={busy} onclick={sync}><Icon name="sync" size={16} />Sync now</button>{/if}
{/snippet}

{#if d}
  <div class="ah-tabs" role="tablist" aria-label="Farm">
    {#each [['checks', `Plate checks${d.checks.length ? ` (${d.checks.length})` : ''}`], ...(has('alarms') ? [['alarms', `Alarms${alarmSum?.active ? ` (${alarmSum.active})` : ''}`]] : []), ['printers', 'Printers'], ['cameras', 'Cameras'], ['queue', `Queue (${d.queue.length})`], ['timeline', 'Timeline'], ['maintenance', 'Maintenance'], ['plates', 'Build plates'], ['filament', 'Filament'], ['history', 'History'], ['search', 'Search'], ['settings', 'Settings']] as [k, l]}
      <button role="tab" aria-selected={view === k} onclick={() => pick(k)}>{l}</button>
    {/each}
  </div>
  {#if view !== 'printers' && view !== 'cameras'}
  <div class="flex flex-wrap gap-2">
    {#each d.printers as p}
      <button class="chip {p.out_of_service ? 'bad' : p.busy ? 'live' : !p.online ? '' : p.ready ? 'ok' : 'warn'} {sel === p.name ? 'ring-2' : ''}" title={p.state}
              onclick={() => (sel = sel === p.name ? null : p.name)}>
        {p.name}: {!p.online ? 'offline' : p.error ? 'error' : p.busy ? `printing${p.progress != null ? ` ${Math.round(p.progress)}%` : ''}` : p.ready ? 'Ready' : 'needs plate cleared'}{p.out_of_service ? ', out of service' : ''}{#if p.mx_due?.length}<span title="Maintenance due: {p.mx_due.map((m) => m.message).join('; ')}"><Glyph name="wrench" size={13} label="Maintenance due" /></span>{/if}
      </button>
    {/each}
    {#if d.printers_error}<span class="chip bad">Printers: {d.printers_error}</span>{/if}
  </div>
  {/if}
  {#if view === 'printers'}
    {#if d.printers_error}<div class="ah-banner ah-banner--bad">Printers: {d.printers_error}</div>{/if}
    <div class="grid gap-3" style="grid-template-columns:repeat(auto-fill,minmax(300px,1fr))">
      {#each d.printers as p (p.name)}
        {@const st = p.out_of_service ? ['bad', 'Out of service'] : !p.online ? ['', 'Offline'] : p.error ? ['bad', 'Error'] : p.busy ? ['live', (p.state || '').includes('pause') ? 'Paused' : 'Printing'] : p.ready ? ['ok', 'Ready'] : ['warn', 'Needs the plate cleared']}
        <button class="ah-glass ah-card" style="text-align:left;cursor:pointer;color:inherit;font:inherit;{sel === p.name ? 'border-color:var(--ah-accent)' : ''}" onclick={() => (sel = sel === p.name ? null : p.name)}>
          <div class="flex items-center gap-[10px]"><div class="ah-icon-tile"><Icon name="printer" /></div>
            <div style="flex:1;min-width:0"><div class="ah-card-title">{p.name}</div><div class="ah-muted">{p.model || ''}</div></div>
            <span class="chip {st[0]}">{st[1]}</span></div>
          <div class="flex items-center gap-[10px] ah-muted"><span class="truncate">{p.busy ? p.job || 'Printing' : p.ready ? 'Ready for the next job' : !p.online ? 'Not reachable' : 'Waiting for the plate to be cleared'}</span>
            <span class="ah-num" style="margin-left:auto">{p.busy && p.progress != null ? `${Math.round(p.progress)}%` : ''}</span></div>
          <div class="ah-progress{st[0] === 'warn' ? ' ah-progress--attention' : ''}"><i style="width:{p.busy ? Math.round(p.progress || 0) : 0}%"></i></div>
          <div class="flex gap-4 ah-muted ah-num">
            {#if p.nozzle_temp != null}<span>Nozzle {Math.round(p.nozzle_temp)}°C</span>{/if}
            {#if p.bed_temp != null}<span>Bed {Math.round(p.bed_temp)}°C</span>{/if}
            {#if p.busy && p.eta_minutes != null}<span style="margin-left:auto">{fmtTime(p.eta_minutes * 60)} left</span>{/if}
          </div>
        </button>
      {:else}<div class="panel ah-muted" style="padding:24px;grid-column:1/-1;text-align:center">The farm reports no printers yet.</div>{/each}
    </div>
  {:else if view === 'cameras'}
    <div class="grid gap-3" style="grid-template-columns:repeat(auto-fill,minmax(380px,1fr))">
      {#each d.printers as p (p.name)}
        <section class="ah-glass ah-card">
          <div class="flex items-center gap-[10px]"><span class="ah-card-title" style="flex:1">{p.name}</span>
            <span class="chip {p.busy ? 'live' : p.online ? 'ok' : ''}">{p.busy ? `Printing${p.progress != null ? ` ${Math.round(p.progress)}%` : ''}` : p.online ? 'Idle' : 'Offline'}</span></div>
          <div class="panel" style="padding:0;overflow:hidden;aspect-ratio:16/9;display:grid;place-items:center">
            <!-- The picture keeps reloading every 10 s even after a miss (a camera that is slow to wake fails its
                 first frame), and shows again as soon as one arrives. -->
            {#if noCam[p.name]}<span class="ah-muted" style="grid-area:1/1">No camera frame from {p.name}</span>{/if}
            <img src={shot(p)} alt="Camera on {p.name}" style="grid-area:1/1;width:100%;height:100%;object-fit:cover"
              style:visibility={noCam[p.name] ? 'hidden' : 'visible'}
              onload={() => { if (noCam[p.name]) { const { [p.name]: _, ...rest } = noCam; noCam = rest; } }}
              onerror={() => (noCam = { ...noCam, [p.name]: true })} />
          </div>
        </section>
      {:else}<div class="panel ah-muted" style="padding:24px;grid-column:1/-1;text-align:center">The farm reports no printers yet.</div>{/each}
    </div>
  {/if}
  {#if hiddenCodes.length}
    <details class="mb-4 text-sm"><summary class="muted cursor-pointer">{hiddenCodes.length} printer code{hiddenCodes.length === 1 ? '' : 's'} hidden as nuisances</summary>
      <ul class="mt-2 space-y-1">{#each hiddenCodes as c (c.code)}<li class="flex items-center gap-2"><span class="font-mono text-xs">{c.code}</span><span class="flex-1">{c.message || 'no description'} <span class="muted">({c.printer || 'every printer'}, by {c.by})</span></span>{#if can('farm')}<button class="btn sm" onclick={() => unhideCode(c)}>Show again</button>{/if}</li>{/each}</ul>
    </details>
  {/if}
  {#each d.printers.filter((x) => x.name === sel) as p (p.name)}
    {@const paused = (p.state || '').includes('pause')}
    <div class="panel p-4 mb-4 flex flex-col gap-3">
      <div class="flex flex-wrap items-baseline gap-3">
        <b class="text-base">{p.name}</b>
        <span class="muted text-sm">{p.model || ''}</span>
        <span class="text-sm">{!p.online ? 'Offline' : p.busy ? `${paused ? 'Paused' : 'Printing'}${p.job ? `: ${p.job}` : ''}` : p.ready ? 'Ready for the next job' : 'Waiting for the plate to be cleared'}</span>
        <button class="btn sm ml-auto" onclick={() => (sel = null)}>Close</button>
      </div>
      {#if p.online}
        <div class="flex flex-wrap gap-4 text-sm">
          {#if p.busy && p.progress != null}<span>{Math.round(p.progress)}%{p.layer != null && p.total_layers ? ` · layer ${p.layer} of ${p.total_layers}` : ''}{p.eta_minutes != null ? ` · ${fmtTime(p.eta_minutes * 60)} left` : ''}</span>{/if}
          {#if p.nozzle_temp != null}<span class="muted">nozzle {Math.round(p.nozzle_temp)}°C</span>{/if}
          {#if p.bed_temp != null}<span class="muted">bed {Math.round(p.bed_temp)}°C</span>{/if}
        </div>
      {/if}
      {#if p.error}
        <div class="text-sm text-red-600">Printer error: {p.error_message || p.error} <span class="muted font-mono text-xs">({p.error})</span></div>
        {#if can('farm')}<div class="flex gap-2"><button class="btn sm" onclick={() => hideCode(p, false)}>Hide this code on {p.name}</button><button class="btn sm" onclick={() => hideCode(p, true)}>Hide on every printer</button></div>{/if}
      {/if}
      {#each p.mx_due || [] as m}<div class="text-sm flex items-center gap-2" style="color:var(--ah-chip-warn-text)"><Glyph name="wrench" size={14} />Maintenance due: {m.message} It is on the Maintenance tab.</div>{/each}
      {#if !p.error && p.last_result}<div class="text-xs muted">Last job {p.last_result}; the alarm has been cleared.</div>{/if}
      {#if p.out_of_service}<div class="text-sm text-amber-600">Out of service{p.out_of_service_reason ? `: ${p.out_of_service_reason}` : ''}. It gets no new jobs.</div>{/if}
      {#if can('farm') && p.online}
        <div class="flex flex-wrap gap-2">
          {#if !p.busy && !p.ready}<button class="btn primary sm" disabled={busy} onclick={() => cmd(p, 'set_ready', `${p.name}: plate cleared`)}>Clear plate</button>{/if}
          {#if p.busy}
            {#if allows(p, 'pause')}
              {#if paused}<button class="btn primary sm" disabled={busy} onclick={() => cmd(p, 'resume', `${p.name} resumed`)}>Resume</button>
              {:else}<button class="btn sm" disabled={busy} onclick={() => cmd(p, 'pause', `${p.name} paused`)}>Pause</button>{/if}
            {/if}
            {#if allows(p, 'cancel')}<button class="btn sm danger" disabled={busy} onclick={() => cancelPrint(p)}>Cancel print</button>{/if}
          {/if}
          {#if allows(p, 'light_on')}
            <button class="btn sm" disabled={busy} onclick={() => cmd(p, 'light_on', 'Light on')}>Light on</button>
            <button class="btn sm" disabled={busy} onclick={() => cmd(p, 'light_off', 'Light off')}>Light off</button>
          {/if}
          {#if !p.busy}
            {#if allows(p, 'home')}<button class="btn sm" disabled={busy} onclick={() => cmd(p, 'home', `Homing ${p.name}`)}>Home</button>{/if}
            {#if allows(p, 'preheat')}
              <span class="flex items-center gap-1">
                <select class="input" style="width: 6rem" bind:value={heat}>{#each ['PLA', 'PETG', 'ABS', 'ASA', 'TPU', 'PC'] as m}<option>{m}</option>{/each}</select>
                <button class="btn sm" disabled={busy} onclick={() => cmd(p, 'preheat', `Preheating ${p.name} for ${heat}`, { material: heat })}>Preheat</button>
              </span>
              <button class="btn sm" disabled={busy} onclick={() => cmd(p, 'cooldown', `Cooling ${p.name}`)}>Cool down</button>
            {/if}
            {#if !allows(p, 'home') && !allows(p, 'preheat')}<span class="text-xs muted self-center">Homing and temperatures are set at the printer ({p.family}).</span>{/if}
          {/if}
          {#if has('out_of_service')}
            <button class="btn sm" disabled={busy} onclick={() => service(p, !p.out_of_service)}>{p.out_of_service ? 'Back in service' : 'Out of service'}</button>
          {/if}
        </div>
        <div class="flex flex-wrap gap-3 text-xs">
          <button class="underline" onclick={() => pick('plates')}>Build plate</button>
          <button class="underline" onclick={() => pick('filament')}>Filament</button>
          <button class="underline" onclick={() => pick('maintenance')}>Maintenance</button>
          <button class="underline" onclick={() => pick('queue')}>Queue</button>
        </div>
      {/if}
    </div>
  {/each}


  {#if view === 'checks'}
    {#if !d.plate_checks_on}
      <div class="panel p-4 mb-4 text-sm">Plate checks are off: prints end without a check here. {#if can('settings')}<button class="btn sm ml-2" onclick={toggleGate}>Turn on</button>{/if}</div>
    {/if}
    <div class="grid gap-4" style="grid-template-columns: repeat(auto-fill, minmax(300px, 1fr))">
      {#each d.checks as c (c.id)}
        <div class="panel p-4 flex flex-col gap-3">
          {#if c.photo}<a href={c.photo} target="_blank" rel="noopener"><img src={c.photo} alt="Finish photo" class="w-full rounded-lg object-cover" style="max-height: 220px" /></a>{/if}
          <div class="flex gap-3">
            {#if c.model?.thumb}<img src={c.model.thumb} alt="" class="w-20 h-20 object-cover rounded-lg" />{/if}
            <div class="min-w-0">
              <div class="font-semibold">{c.printer}</div>
              {#if c.model}<a class="text-sm hover:underline" href={`/models/${c.model.id}`}>{c.model.code} {c.model.name}</a>{/if}
              <div class="text-xs muted break-all">{c.job_name}</div>
              <div class="mt-1"><span class="chip {outcomeCls(c.job_outcome)}">{c.job_outcome}</span> <span class="text-xs muted">{ago(c.created_at)}</span></div>
            </div>
          </div>
          {#if can('farm')}
            {#if failing === c.id}
              <div class="flex flex-col gap-2">
                <select class="input" bind:value={form.reason}>
                  <option value="">What went wrong?</option>
                  {#each Object.entries(d.reasons) as [k, l]}<option value={k}>{l}</option>{/each}
                </select>
                <input class="input" placeholder="Note (optional)" bind:value={form.note} />
                <label class="text-sm flex items-center gap-2"><input type="checkbox" bind:checked={form.reprint} /> Reprint it</label>
                <label class="text-sm flex items-center gap-2"><input type="checkbox" bind:checked={form.set_ready} /> Plate is cleared: set {c.printer} Ready</label>
                <div class="flex gap-2"><button class="btn primary" disabled={busy} onclick={() => fail(c)}>Record failure</button><button class="btn" onclick={() => (failing = null)}>Cancel</button></div>
              </div>
            {:else}
              <div class="flex flex-wrap gap-2">
                <button class="btn primary" disabled={busy} onclick={() => pass(c)}>Passed, set Ready</button>
                <button class="btn danger" disabled={busy} onclick={() => openFail(c)}>Failed...</button>
                <button class="btn sm" disabled={busy} title="Passed, but leave the printer not Ready" onclick={() => pass(c, false)}>Passed, keep off</button>
                <button class="btn sm" onclick={() => skip(c)}>Dismiss</button>
              </div>
            {/if}
          {/if}
        </div>
      {:else}
        <div class="panel p-6 muted text-center" style="grid-column: 1 / -1">No plates waiting for a check.</div>
      {/each}
    </div>

    {#if d.recent_checks.length}
      <h2 class="font-semibold mt-6 mb-2">Recent checks</h2>
      <div class="panel p-4 scroll-x">
        <table class="grid">
          <thead><tr><th>When</th><th>Printer</th><th>Model / job</th><th>Result</th><th>Ready</th><th>By</th></tr></thead>
          <tbody>
            {#each d.recent_checks as c}
              <tr>
                <td class="text-xs muted">{ago(c.checked_at)}</td>
                <td>{c.printer}</td>
                <td class="text-sm">{#if c.model}<a class="hover:underline" href={`/models/${c.model.id}`}>{c.model.code} {c.model.name}</a>{:else}<span class="break-all">{c.job_name}</span>{/if}</td>
                <td><span class="chip {c.state === 'passed' ? 'ok' : c.state === 'failed' ? 'bad' : ''}">{c.state}</span>
                  {#if c.reason_label}<span class="text-xs">{c.reason_label}</span>{/if}{#if c.note}<div class="text-xs muted">{c.note}</div>{/if}
                  {#if c.reprint_job_id}<div class="text-xs">reprint job {c.reprint_job_id}</div>{/if}</td>
                <td>{#if c.ready_set}<span class="chip ok">set</span>{:else if c.ready_due}<span class="chip warn" title="Staggered start">at {new Date(c.ready_due).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</span>{:else if c.ready_error}<span class="chip bad" title={c.ready_error}>not set</span> {#if can('farm')}<button class="btn sm" onclick={() => retryReady(c)}>Retry</button>{/if}{:else}<span class="muted text-xs">no</span>{/if}</td>
                <td class="text-xs muted">{c.checked_by}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
    {#if d.plate_checks_on && can('settings')}<p class="text-xs muted mt-4">Plate checks are on. <button class="underline" onclick={toggleGate}>Turn off</button></p>{/if}

  {:else if view === 'alarms'}
    <Alarms onchange={loadAlarmSummary} />
  {:else if view === 'timeline'}
    <Timeline />
  {:else if view === 'maintenance'}
    <Maintenance />
  {:else if view === 'plates'}
    <Plates />
  {:else if view === 'filament'}
    <Filament printers={d.printers} />
  {:else if view === 'settings'}
    {#if !st}<p class="ah-muted">Loading...</p>{:else}
      {@const edit = can('settings')}
      <div class="grid gap-4" style="grid-template-columns: repeat(auto-fit, minmax(320px, 1fr))">
        <div class="panel p-4 flex flex-col gap-3">
          <h2 class="font-semibold">Plate checks and starts</h2>
          <label class="text-sm flex items-center gap-2"><input type="checkbox" disabled={!edit} checked={st.plate_checks} onchange={(e) => saveSettings({ plate_checks: e.target.checked })} /> Open a plate check when a print ends</label>
          <label class="text-sm">Staggered start: at least
            <input class="input inline-block" style="width: 5rem" type="number" min="0" max="60" step="0.5" disabled={!edit} value={st.stagger_minutes}
              onchange={(e) => saveSettings({ stagger_minutes: Number(e.target.value) })} /> minutes between printers starting</label>
          <label class="text-sm">Clearing a plate takes about
            <input class="input inline-block" style="width: 5rem" type="number" min="0" max="240" disabled={!edit} value={st.turnaround_minutes}
              onchange={(e) => saveSettings({ turnaround_minutes: Number(e.target.value) })} /> minutes (used by the timeline)</label>
          <p class="text-xs muted">When several plates are passed together the hub sets the printers Ready one at a time, so they don't all heat up at once. Prints that start on their own count too. 0 turns it off.</p>
        </div>
        <div class="panel p-4 flex flex-col gap-3">
          <h2 class="font-semibold">WooCommerce orders</h2>
          <div class="flex gap-2">
            <button class="btn {st.order_routing === 'hub' ? 'primary' : ''}" disabled={!edit || st.order_routing === 'hub'} onclick={() => setRouting('hub')}>Hub sends them</button>
            <button class="btn {st.order_routing === 'off' ? 'primary' : ''}" disabled={!edit || st.order_routing === 'off'} onclick={() => setRouting('off')}>Queue by hand</button>
          </div>
          <p class="text-xs muted">{st.order_routing === 'hub'
            ? 'Paid orders go to the farm from the hub (the approved print files). Cancelled orders pull jobs that have not started. When every job has printed the Woo order gets a private note.'
            : 'Orders are not sent to the farm: queue their prints by hand.'}</p>
          <label class="text-sm">Orders should ship within
            <input class="input inline-block" style="width: 5rem" type="number" min="1" max="60" disabled={!edit} value={st.ship_within_days}
              onchange={(e) => saveSettings({ ship_within_days: Number(e.target.value) })} /> days of being placed (the default due date)</label>
          <label class="text-sm">If an order print fails, retry it
            <input class="input inline-block" style="width: 4.5rem" type="number" min="0" max="10" disabled={!edit} value={st.max_retries}
              onchange={(e) => saveSettings({ max_retries: Number(e.target.value) })} /> time(s) automatically (farms that support it)</label>
          <label class="text-sm flex items-center gap-2"><input type="checkbox" disabled={!edit} checked={st.route_test_orders} onchange={(e) => saveSettings({ route_test_orders: e.target.checked })} /> Send test orders too</label>
        </div>
      </div>
      <div class="panel p-4 mt-4">
        <div class="flex flex-wrap items-center gap-3 mb-3">
          <h2 class="font-semibold mr-auto">Alerts</h2>
          <span class="text-xs muted">Set up: {st.channels_configured.length ? st.channels_configured.join(', ') : 'nothing yet'}</span>
          {#if can('connections')}<a class="btn sm" href="/connections">Phone alert channels</a>{/if}
          {#if can('farm')}<button class="btn sm" onclick={testAlerts}>Send a test</button>{/if}
        </div>
        <div class="scroll-x">
          <table class="grid">
            <thead><tr><th>Event</th>{#each st.channels as ch}<th class="text-center">{ch}</th>{/each}</tr></thead>
            <tbody>
              {#each Object.entries(st.alert_events) as [ev, label]}
                <tr>
                  <td>{label}{#if st.urgent_events.includes(ev)} <span class="chip bad">urgent</span>{/if}</td>
                  {#each st.channels as ch}
                    <td class="text-center"><input type="checkbox" disabled={!edit} checked={(st.alert_rules.events[ev] || []).includes(ch)}
                      title={st.channels_configured.includes(ch) ? '' : `${ch} isn't set up yet`} onchange={() => toggleChannel(ev, ch)} /></td>
                  {/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <div class="flex flex-wrap items-end gap-3 mt-4 text-sm">
          <label>Quiet hours from <input class="input inline-block" style="width: 6rem" type="time" disabled={!edit} value={st.alert_rules.quiet_start} onchange={(e) => saveSettings({ alert_rules: { quiet_start: e.target.value } })} /></label>
          <label>to <input class="input inline-block" style="width: 6rem" type="time" disabled={!edit} value={st.alert_rules.quiet_end} onchange={(e) => saveSettings({ alert_rules: { quiet_end: e.target.value } })} /></label>
          <label>Time zone <input class="input inline-block" style="width: 12rem" disabled={!edit} value={st.alert_rules.timezone} onchange={(e) => saveSettings({ alert_rules: { timezone: e.target.value } })} /></label>
          <label>During quiet hours
            <select class="input inline-block" style="width: auto" disabled={!edit} value={st.alert_rules.quiet_mode} onchange={(e) => saveSettings({ alert_rules: { quiet_mode: e.target.value } })}>
              <option value="urgent">only urgent alerts</option><option value="none">nothing</option><option value="off">everything (no quiet hours)</option>
            </select></label>
        </div>
        <p class="text-xs muted mt-2">Print-finished and print-failed alerts carry a photo from the printer's camera when it has one (ntfy and Telegram; Slack and email get a link).</p>
      </div>
    {/if}

  {:else if view === 'queue' || view === 'history' || view === 'search'}
    {#if view === 'search'}
      <form class="flex gap-2 mb-4" onsubmit={(e) => { e.preventDefault(); search(); }}>
        <input class="input" placeholder="Job name contains..." bind:value={q} />
        <button class="btn">Search</button>
      </form>
    {/if}
    {@const rows = view === 'queue' ? d.queue : view === 'history' ? d.history : found?.jobs || []}
    {#if view === 'history' && stats}
      <div class="panel p-4 mb-4 flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <span class="font-semibold">Last {stats.days} days</span>
        <span>{stats.total} jobs</span>
        <span>success {pct(stats.success_rate)}</span>
        {#if stats.avg_wait_seconds != null}<span>avg wait {fmtTime(stats.avg_wait_seconds)}</span>{/if}
        {#if stats.avg_print_seconds != null}<span>avg print {fmtTime(stats.avg_print_seconds)}</span>{/if}
        {#if stats.retried_items}<span>{stats.retried_items} automatic retries</span>{/if}
      </div>
    {/if}
    <div class="panel p-4 scroll-x">
      <table class="grid">
        <thead><tr><th>Job</th><th>Status</th><th>Printer</th><th>Priority</th><th>Time</th><th>Filament</th><th>{view === 'queue' ? 'Waiting because' : 'Ended'}</th></tr></thead>
        <tbody>
          {#each rows as j}
            <tr>
              <td class="text-sm"><div class="break-all">#{j.external_id} {j.name}</div>{#if j.model}<a class="text-xs hover:underline" href={`/models/${j.model.id}`}>{j.model.code} {j.model.name}</a>{/if}{#if j.orders.length}<div class="text-xs muted">orders {j.orders.join(', ')}</div>{/if}</td>
              <td><span class="chip {statusCls(j.status)}">{statusLabel(j.status)}{j.status === 'printing' && j.progress != null ? ` ${Math.round(j.progress)}%` : ''}</span></td>
              <td>{j.printer || ''}</td>
              <td>{j.priority ?? ''}</td>
              <td class="text-sm">{fmtTime(j.est_time_s)}</td>
              <td class="text-sm" title={j.used_source ? `Used ${Math.round(j.used_g)} g (${j.used_source}); slicer estimate ${Math.round(j.filament_g || 0)} g` : ''}>{#if j.used_g != null && (j.used_source === 'printer' || j.used_source === 'weighed')}{Math.round(j.used_g)} g <span class="chip ok" style="font-size: .62rem">{j.used_source === 'printer' ? 'measured' : 'weighed'}</span>{:else}{j.filament_g ? `${Math.round(j.used_g ?? j.filament_g)} g` : ''}{/if} {j.material || ''}</td>
              <td class="text-sm">{#if view === 'queue'}{#if j.blocked}<span class="text-amber-600">{j.blocked}</span>{:else}<span class="muted text-xs">{ago(j.created_at)}</span>{/if}{#if j.plate_reslice?.msg}<div class="text-xs {j.plate_reslice.state === 'swapped' ? 'muted' : 'text-amber-600'}">Plate: {j.plate_reslice.msg}</div>{/if}{#if can('farm') && !['completed', 'failed', 'cancelled'].includes(j.status)}
                  <span class="inline-flex gap-1 ml-1">
                    {#if has('priority') && j.status !== 'printing'}<select class="input" style="width: 6.5rem; padding: 2px 4px" value={String(j.priority ?? 50)} onchange={(e) => setPriority(j, e.currentTarget.value)} title="Priority">
                      {#each [['25', 'Low'], ['50', 'Normal'], ['75', 'High'], ['100', 'Urgent']] as [v, l]}<option value={v}>{l}</option>{/each}
                      {#if ![25, 50, 75, 100].includes(j.priority ?? 50)}<option value={String(j.priority)}>{j.priority}</option>{/if}
                    </select>{/if}
                    <button class="btn sm danger" onclick={() => cancelJob(j)}>{j.status === 'printing' ? 'Stop' : 'Cancel'}</button>
                  </span>
                {/if}{#if d.capabilities?.includes('preflight')} <button class="btn sm" onclick={() => explain(j)}>{why[j.id] ? 'Hide' : 'Why?'}</button>{/if}
                {#if why[j.id]}
                  <div class="text-xs mt-1">
                    {#if why[j.id].waiting_reason}<div>{why[j.id].waiting_reason}</div>{/if}
                    {#each why[j.id].printers || [] as pr}<div><span class="chip {pr.eligible ? 'ok' : 'warn'}">{pr.name}</span> {pr.eligible ? 'can take it' : (pr.reasons || []).join(', ')}</div>{:else}<div class="muted">No printer matches this job.</div>{/each}
                  </div>
                {/if}{:else}<span class="text-xs muted">{ago(j.completed_at)}</span>{#if j.error}<div class="text-xs text-red-600">{j.error}</div>{/if}{/if}</td>
            </tr>
          {:else}<tr><td colspan="7" class="muted text-center py-6">{view === 'search' && !found ? 'Search the mirrored jobs by name.' : 'Nothing here.'}</td></tr>{/each}
        </tbody>
      </table>
    </div>
  {/if}
{:else if loadErr}
  <div class="ah-banner ah-banner--bad">The farm did not answer: {loadErr}</div>
{:else}
  <p class="ah-muted">Loading...</p>
{/if}
</Page>
