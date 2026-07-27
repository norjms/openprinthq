<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';
  import CameraImg from '$lib/components/CameraImg.svelte';
  import { prettyModel, printerLabel } from '$lib/models.js';
  import { markSeen, recentlyOnline } from '$lib/online.js';

  let loading = $state(true);
  let error = $state(null);
  let printers = $state([]);
  let timer = null;
  // Camera thumbnails: a slow 60s snapshot poll (bump camTick) keeps resource
  // use low, independent of the 5s status poll. CameraImg shows the cached last
  // frame instantly on load, then swaps in the live one.
  let camTick = $state(0);
  let camTimer = null;

  function base(data) {
    const arr = Array.isArray(data) ? data : (data?.printers || data?.items || data?.results || []);
    return arr.map((p) => ({
      id: p.id ?? p.printer_id ?? p.serial ?? p.name,
      name: p.name ?? p.friendly_name ?? p.model ?? 'Printer',
      model: p.model ?? p.printer_type ?? p.type ?? '',
      vendor: p.connection_type ?? p.vendor ?? p.brand ?? ''
    }));
  }

  async function load(initial = true) {
    if (initial) { loading = true; error = null; }
    try {
      const list = base(await api.printers());
      // Live state comes from the per-printer status endpoint, not the list.
      const live = await Promise.all(list.map((p) => api.printerStatus(p.id).catch(() => null)));
      printers = list.map((p, i) => ({ ...p, live: live[i] }));
      // Remember the last time each printer reported connected (drives hysteresis).
      printers.forEach((p) => markSeen(p.id, p.live?.connected));
      error = null;
    } catch (e) {
      error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable');
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    load(true);
    timer = setInterval(() => load(false), 5000);
    // Camera snapshots refresh on a slow 60s cadence (independent of the 5s
    // status poll) to keep resource use low. CameraImg keeps the last frame.
    camTimer = setInterval(() => camTick++, 60000);
    return () => { clearInterval(timer); clearInterval(camTimer); };
  });

  function statusOf(p) {
    if (!p.live) return 'unknown';
    if (!p.live.connected) return 'offline';
    return (p.live.state || 'idle').toString().toLowerCase();
  }
  // Card status chip: an idle printer with a clear plate reads "ready" (not the
  // last job's failed/finished). After a print it prompts to clear the plate.
  function dispOf(p) {
    const l = p.live;
    // Online with hysteresis first: a momentary connected=false (MQTT flap) still
    // reads online if the printer was connected within the grace window; a cached
    // last-online makes a fresh reload show the true state instead of "offline".
    const online = l ? (l.connected || recentlyOnline(p.id)) : recentlyOnline(p.id);
    if (!l && !online) return { label: 'checking', tone: '' };
    if (!online) return { label: 'offline', tone: 'danger' };
    const s = (l?.state || '').toString().toLowerCase();
    if (/run|print/.test(s)) return { label: 'printing', tone: 'primary' };
    if (/pause/.test(s)) return { label: 'paused', tone: 'accent' };
    if (l?.awaiting_plate_clear) return { label: 'clear plate', tone: 'accent', clear: true };
    return { label: 'ready', tone: 'ok' };
  }
  let clearing = $state({});
  async function clearPlate(p, e) {
    e.preventDefault(); e.stopPropagation();
    clearing = { ...clearing, [p.id]: true };
    try { await api.clearPlate(p.id); await load(false); }
    catch (err) { /* next poll reflects state */ }
    finally { clearing = { ...clearing, [p.id]: false }; }
  }
  function tone(s) {
    if (/run|print/.test(s)) return 'primary';
    if (/pause/.test(s)) return 'accent';
    if (/idle|ready|online/.test(s)) return 'ok';
    if (/finish|done|complete/.test(s)) return 'ok';
    if (/error|offline|fault|fail/.test(s)) return 'danger';
    return '';
  }
  const t1 = (v) => (v == null ? null : Math.round(Number(v)));

  // ---- fleet firmware ----
  let fw = $state(null);          // { updates:[...], updates_available }
  let fwLoading = $state(false);
  let fwErr = $state(null);
  async function checkFirmware() {
    fwLoading = true; fwErr = null;
    try { fw = await api.firmwareUpdates(); }
    catch (e) { fwErr = e.message || 'firmware check failed'; }
    finally { fwLoading = false; }
  }
</script>

<PageTitle page="Printers" />

<div class="head">
  <div>
    <h1>Printers</h1>
    <p class="muted">Bambu Lab, Creality, Prusa, Snapmaker and Voron — one fleet.</p>
  </div>
  <div class="flex gap">
    <button class="btn btn-ghost btn-sm" onclick={() => load(false)}>Refresh</button>
    <a class="btn btn-primary btn-sm" href="/app/printers/add">+ Add printer</a>
  </div>
</div>

{#if loading}
  <div class="card card-pad muted">Connecting to your engine…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad">
    <h3>No instance yet</h3>
    <p class="muted">Provision your instance from the <a href="/app">overview</a> to start adding printers.</p>
  </div>
{:else if error}
  <div class="card card-pad">
    <h3>Engine unreachable</h3>
    <p class="muted">{error}</p>
    <button class="btn btn-ghost btn-sm" onclick={() => load()}>Retry</button>
  </div>
{:else if printers.length === 0}
  <div class="card card-pad empty">
    <div class="ic">🖨️</div>
    <h3>No printers yet</h3>
    <p class="muted">Your engine is live and ready. Add your first printer — Bambu, Klipper (Mainsail/Fluidd), Prusa, Snapmaker and more are supported out of the box.</p>
    <a class="btn btn-primary" href="/app/printers/add">+ Add your first printer</a>
  </div>
{:else}
  <div class="grid printers">
    {#each printers as p (p.id)}
      {@const st = statusOf(p)}
      {@const d = dispOf(p)}
      <a class="card card-pad printer" href="/app/printers/{p.id}">
        <div class="flex between center">
          <h3>{p.name}</h3>
          {#if d.clear}
            <button class="chip accent clearchip" onclick={(e) => clearPlate(p, e)} disabled={clearing[p.id]}>
              {clearing[p.id] ? 'Clearing…' : '✓ Clear plate'}
            </button>
          {:else}
            <span class="chip {d.tone}">{d.label}</span>
          {/if}
        </div>
        <div class="meta mono">
          {#if printerLabel(p.vendor, p.model)}<span>{printerLabel(p.vendor, p.model)}</span>{/if}
        </div>
        {#if p.live?.connected || recentlyOnline(p.id)}
          <div class="cam">
            <CameraImg printerId={p.id} tick={camTick} alt={`${p.name || 'Printer'} camera`} mode="fill" />
            {#if /run|print/.test(st) && p.live.progress != null}
              <span class="cam-prog mono">{Math.round(p.live.progress)}%</span>
            {/if}
          </div>
        {/if}
        {#if p.live?.connected}
          <div class="temps mono">
            {#if t1(p.live.temperatures?.nozzle) != null}<span>◦ {t1(p.live.temperatures.nozzle)}°</span>{/if}
            {#if t1(p.live.temperatures?.bed) != null}<span>▱ {t1(p.live.temperatures.bed)}°</span>{/if}
            {#if /run|print/.test(st) && p.live.progress != null}<span class="prog">{Math.round(p.live.progress)}%</span>{/if}
          </div>
          {#if /run|print/.test(st) && p.live.progress != null}
            <div class="bar"><div class="fill" style="width:{Math.min(100, Math.max(0, p.live.progress))}%"></div></div>
          {/if}
        {/if}
      </a>
    {/each}
  </div>

  <div class="card card-pad fw">
    <div class="flex between center">
      <div>
        <span class="eyebrow">Firmware</span>
        {#if fw}<span class="muted fwsum">{fw.updates_available > 0 ? `${fw.updates_available} update${fw.updates_available > 1 ? 's' : ''} available` : 'All up to date'}</span>{/if}
      </div>
      <button class="btn btn-ghost btn-sm" onclick={checkFirmware} disabled={fwLoading}>{fwLoading ? 'Checking…' : (fw ? 'Re-check' : 'Check firmware')}</button>
    </div>
    {#if fwErr}<p class="err">{fwErr}</p>{/if}
    {#if fw?.updates?.length}
      <div class="fwlist">
        {#each fw.updates as u (u.printer_id)}
          <div class="fwrow">
            <span class="fwn">{u.printer_name}{#if u.model}<span class="muted mono"> · {prettyModel(u.model)}</span>{/if}</span>
            <span class="mono muted fwcur">{u.current_version || 'unknown'}</span>
            {#if u.update_available}
              <span class="chip accent" title={u.latest_version || ''}>update{#if u.latest_version} → {u.latest_version}{/if}</span>
            {:else if u.current_version}
              <span class="chip ok">up to date</span>
            {:else}
              <span class="chip">—</span>
            {/if}
          </div>
        {/each}
      </div>
      <p class="muted tiny">Bambu checks Bambu's firmware feed; Klipper checks Moonraker's update manager. Apply updates from the printer's own screen / Mainsail.</p>
    {:else if fw}
      <p class="muted">No printers to check.</p>
    {:else}
      <p class="muted">Check current firmware and available updates across every printer (Bambu + Klipper).</p>
    {/if}
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; gap: 1rem; }
  .head h1 { margin: 0; }
  .printers { grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
  .printer { display: block; color: var(--ophq-text); text-decoration: none; transition: border 0.15s, transform 0.15s; }
  .printer:hover { border-color: var(--ophq-primary); transform: translateY(-2px); color: var(--ophq-text); }
  .printer h3 { margin: 0; font-size: 1.05rem; color: var(--ophq-text); }
  .printer .meta { display: flex; gap: 0.6rem; margin-top: 0.5rem; color: var(--ophq-muted); font-size: 0.85rem; }
  .cam { position: relative; margin-top: 0.7rem; aspect-ratio: 16 / 9; border-radius: var(--radius-sm); overflow: hidden; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); }
  .cam img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .cam-prog { position: absolute; bottom: 6px; right: 7px; font-size: 0.75rem; padding: 0.1rem 0.4rem; border-radius: 999px; background: rgba(0,0,0,0.55); color: #fff; backdrop-filter: blur(2px); }
  .temps { display: flex; gap: 0.9rem; margin-top: 0.6rem; color: var(--ophq-text-2); font-size: 0.85rem; }
  .temps .prog { color: var(--ophq-primary-2); margin-left: auto; }
  .bar { height: 6px; background: var(--ophq-bg-2); border: 1px solid var(--ophq-border); border-radius: 999px; overflow: hidden; margin-top: 0.5rem; }
  .fill { height: 100%; background: linear-gradient(90deg, var(--ophq-primary), var(--ophq-primary-2)); transition: width 0.4s ease; }
  .chip.danger { color: var(--ophq-danger); border-color: rgba(255,92,108,0.3); background: rgba(255,92,108,0.08); }
  .chip.accent { color: var(--ophq-accent); border-color: rgba(255,176,32,0.3); background: rgba(255,176,32,0.08); }
  .chip.primary { color: var(--ophq-primary-2); border-color: rgba(124,108,255,0.35); background: var(--ophq-primary-dim); }
  .clearchip { cursor: pointer; font: inherit; line-height: inherit; }
  .clearchip:hover:not(:disabled) { filter: brightness(1.08); }
  .clearchip:disabled { opacity: 0.6; cursor: default; }
  .fw { margin-top: 1.4rem; }
  .fwsum { margin-left: 0.6rem; font-size: 0.85rem; }
  .fwlist { display: flex; flex-direction: column; gap: 0.4rem; margin: 0.9rem 0 0.6rem; }
  .fwrow { display: grid; grid-template-columns: 1fr auto auto; gap: 1rem; align-items: center; padding: 0.5rem 0.7rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-surface); }
  .fwn { font-size: 0.9rem; }
  .fwcur { font-size: 0.8rem; }
  .fw .chip.accent { color: var(--ophq-accent); border-color: rgba(255,176,32,0.35); background: rgba(255,176,32,0.08); }
  .fw .chip.ok { color: var(--ophq-success); border-color: rgba(53,196,107,0.3); background: rgba(53,196,107,0.08); }
  .fw .tiny { font-size: 0.78rem; }
  .fw .err { color: var(--ophq-danger); font-size: 0.88rem; }
  .empty { text-align: center; padding: 2.6rem; }
  .empty .ic { font-size: 2rem; margin-bottom: 0.4rem; }
  .empty p { max-width: 48ch; margin: 0.6rem auto 1.4rem; }
</style>
