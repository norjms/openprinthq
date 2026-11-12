<script>
  import { base as basePath } from '$app/paths';
  import Icon from '$lib/components/AppIcon.svelte';
  // Timelapse gallery: recorded prints from every camera, newest first, with a
  // filter per printer. The Timelapses page renders it under its own head.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import { Toggle } from 'artisanhub-ui';
  import { api } from '$lib/api';
  import EngineError from '$lib/components/EngineError.svelte';

  let { reload = $bindable() } = $props();

  let loading = $state(true);
  let error = $state(null);
  let archives = $state([]);
  let printerNames = $state({});
  let recordDefault = $state(false);
  let savingDefault = $state(false);
  let only = $state('all');  // 'all' or a printer id
  let play = $state(null);   // archive being played

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.items || d?.archives || []);
    return arr.map((a) => ({
      id: a.id,
      name: a.print_name || a.filename || 'Print',
      printerId: a.printer_id != null ? String(a.printer_id) : '',
      printer: a.printer_name || '',
      hasTL: !!a.timelapse_path,
      thumb: a.thumbnail_path ? api.archiveThumbUrl(a.id) : null,
      when: a.completed_at || a.created_at
    }));
  }
  async function load() {
    loading = true; error = null;
    try {
      const [eng, list, printers] = await Promise.all([
        api.engineSettings().catch(() => null),
        api.archives(80),
        api.printers().catch(() => [])
      ]);
      if (eng) recordDefault = !!eng.default_timelapse;
      const arr = Array.isArray(printers) ? printers : (printers?.printers || printers?.items || []);
      printerNames = Object.fromEntries(arr.map((p) => [String(p.id ?? p.printer_id), p.name || p.model || `Printer ${p.id}`]));
      archives = norm(list)
        .filter((a) => a.hasTL)
        .sort((x, y) => new Date(y.when || 0) - new Date(x.when || 0));
    } catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'The engine is not answering.'); }
    finally { loading = false; }
  }
  reload = load;
  onMount(load);

  async function toggleDefault(v) {
    savingDefault = true;
    try { await api.updateEngineSettings({ default_timelapse: v }); recordDefault = v; }
    catch { /* the switch shows the saved value */ } finally { savingDefault = false; }
  }
  const printerOf = (a) => a.printer || printerNames[a.printerId] || (a.printerId ? `Printer ${a.printerId}` : '');
  const filters = $derived([...new Set(archives.map((a) => a.printerId).filter(Boolean))]
    .map((id) => ({ id, name: printerNames[id] || `Printer ${id}` })));
  const shown = $derived(only === 'all' ? archives : archives.filter((a) => a.printerId === only));
  function fmtWhen(v) { if (!v) return ''; const d = new Date(v); return isNaN(d) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') play = null; }} />

{#if loading}
  <div class="card card-pad muted">Loading timelapses</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="{basePath}/app">overview</a> first.</p></div>
{:else if error}
  <EngineError {error} onretry={load} />
{:else}
  <section class="card gallery fill">
    <div class="ghead">
      <h2 class="ah-widget-title">Recorded prints</h2>
      <label class="rec">
        <Toggle checked={recordDefault} disabled={savingDefault} label="Record on new prints" onchange={toggleDefault} />
        <span>Record on new prints</span>
      </label>
      <span class="sp"></span>
      {#if filters.length > 1}
        <div class="ah-seg" role="tablist" aria-label="Printer">
          <button type="button" role="tab" aria-selected={only === 'all'} onclick={() => (only = 'all')}>All printers</button>
          {#each filters as f (f.id)}
            <button type="button" role="tab" aria-selected={only === f.id} onclick={() => (only = f.id)}>{f.name}</button>
          {/each}
        </div>
      {/if}
    </div>
    {#if shown.length === 0}
      <div class="empty">
        <span class="empty-ic"><Icon name="film" size={26} /></span>
        <h3>No timelapses yet</h3>
        <p class="muted">Turn on <b>Record on new prints</b> and finished prints appear here as timelapses.</p>
      </div>
    {:else}
      <div class="tls">
        {#each shown as a (a.id)}
          <article class="tl">
            <button class="poster" onclick={() => (play = a)} aria-label="Play the timelapse of {a.name}">
              {#if a.thumb}<img src={a.thumb} alt="" loading="lazy" />{:else}<span class="noimg">No preview</span>{/if}
              <span class="playic"><Icon name="play" size={18} /></span>
            </button>
            <div class="meta">
              <span class="tn">{a.name}</span>
              <span class="sub"><span>{printerOf(a)}</span><span>{fmtWhen(a.when)}</span></span>
            </div>
          </article>
        {/each}
      </div>
    {/if}
  </section>
{/if}

{#if play}
  <div class="lightbox" role="presentation" onclick={() => (play = null)}>
    <button class="lb-close" onclick={() => (play = null)} aria-label="Close"><Icon name="x" size={18} /></button>
    <!-- svelte-ignore a11y_media_has_caption -->
    <video src={api.archiveTimelapseUrl(play.id)} controls autoplay onclick={(e) => e.stopPropagation()}></video>
    <div class="lb-cap">
      {play.name}, {printerOf(play)}
      <a class="btn btn-sm" href={api.archiveTimelapseUrl(play.id)} download onclick={(e) => e.stopPropagation()}><Icon name="download" size={15} />Download</a>
    </div>
  </div>
{/if}

<style>
  .gallery { display: flex; flex-direction: column; min-height: 0; padding: 0; overflow: hidden; }
  .ghead { display: flex; align-items: center; gap: 16px; padding: 16px 18px 12px; flex-wrap: wrap; }
  .ghead h2 { margin: 0; }
  .rec { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; color: var(--ah-text-2); }
  .sp { flex: 1; }
  .tls { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr)); grid-auto-rows: max-content; gap: 14px; padding: 0 18px 18px; overflow: auto; min-height: 0; align-content: start; }
  .tl { border: 1px solid var(--ah-panel-line); border-radius: var(--ah-radius-choice); background: var(--ah-panel); overflow: hidden; }
  .poster { position: relative; width: 100%; aspect-ratio: 16 / 7; background: var(--ah-solid-panel); border: 0; padding: 0; cursor: pointer; display: block; }
  .poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .noimg { width: 100%; height: 100%; display: grid; place-items: center; color: var(--ah-text-2); font-size: 13px; }
  .playic { position: absolute; inset: 0; margin: auto; width: 44px; height: 44px; display: grid; place-items: center; background: rgba(12, 20, 26, 0.6); color: var(--ah-text); border-radius: 50%; }
  .meta { padding: 10px 14px 12px; display: flex; flex-direction: column; gap: 3px; }
  .tn { font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sub { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--ah-text-2); font-variant-numeric: tabular-nums; }
  .empty { text-align: center; padding: 40px 18px; display: flex; flex-direction: column; align-items: center; }
  .empty-ic { width: 56px; height: 56px; border-radius: 16px; background: var(--ah-chip-live-bg); color: var(--ah-accent); display: grid; place-items: center; margin-bottom: 10px; }
  .empty h3 { margin: 0; }
  .empty p { max-width: 46ch; margin: 6px auto 0; }
  .lightbox { position: fixed; inset: 0; z-index: 200; background: rgba(12, 20, 26, 0.88); backdrop-filter: blur(6px); display: grid; place-items: center; padding: 2rem; }
  .lightbox video { max-width: 94vw; max-height: 84vh; border-radius: var(--ah-radius-choice); border: 1px solid var(--ah-glass-line); }
  .lb-close { position: fixed; top: 18px; right: 20px; width: 40px; height: 40px; border-radius: 50%; background: var(--ah-glass); border: 1px solid var(--ah-field-line); color: var(--ah-text); cursor: pointer; display: grid; place-items: center; }
  .lb-cap { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); color: var(--ah-text-2); font-size: 13px; display: flex; align-items: center; gap: 12px; }
</style>
