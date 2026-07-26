<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import PageTitle from '$lib/components/PageTitle.svelte';

  let loading = $state(true);
  let error = $state(null);
  let archives = $state([]);
  let onlyTL = $state(true);
  let recordDefault = $state(false);
  let savingDefault = $state(false);
  let play = $state(null);   // archive being played

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.items || d?.archives || []);
    return arr.map((a) => ({
      id: a.id,
      name: a.print_name || a.filename || 'Print',
      printer: a.printer_name || (a.printer_id ? 'Printer ' + a.printer_id : ''),
      status: (a.status || '').toString(),
      hasTL: !!a.timelapse_path,
      thumb: a.thumbnail_path ? api.archiveThumbUrl(a.id) : null,
      when: a.completed_at || a.created_at
    }));
  }
  async function load() {
    loading = true; error = null;
    try {
      const eng = await api.engineSettings().catch(() => null);
      if (eng) recordDefault = !!eng.default_timelapse;
      archives = norm(await api.archives(80));
    } catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(load);

  async function toggleDefault() {
    savingDefault = true;
    try { await api.updateEngineSettings({ default_timelapse: !recordDefault }); recordDefault = !recordDefault; }
    catch { /* ignore */ } finally { savingDefault = false; }
  }
  const shown = $derived(onlyTL ? archives.filter((a) => a.hasTL) : archives);
  function fmtWhen(v) { if (!v) return ''; const d = new Date(v); return isNaN(d) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }
</script>

<PageTitle page="Timelapses" />
<svelte:window onkeydown={(e) => { if (e.key === 'Escape') play = null; }} />

<div class="head">
  <div><h1>Timelapses</h1><p class="muted">Print timelapses across your fleet.</p></div>
  <div class="flex gap center">
    <label class="tog"><input type="checkbox" checked={recordDefault} onchange={toggleDefault} disabled={savingDefault} /><span>Record on new prints</span></label>
    <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
  </div>
</div>

{#if loading}
  <div class="card card-pad muted">Loading…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else}
  <label class="filt"><input type="checkbox" bind:checked={onlyTL} /><span>Only prints with a timelapse</span></label>
  {#if shown.length === 0}
    <div class="card card-pad empty">
      <div class="ic">🎞️</div><h3>No timelapses yet</h3>
      <p class="muted">Turn on <b>Record on new prints</b> and your completed prints will appear here as timelapses.</p>
    </div>
  {:else}
    <div class="grid tls">
      {#each shown as a (a.id)}
        <div class="card tl">
          <button class="poster" onclick={() => a.hasTL && (play = a)} class:playable={a.hasTL}>
            {#if a.thumb}<img src={a.thumb} alt={a.name} loading="lazy" />{:else}<div class="noimg">no preview</div>{/if}
            {#if a.hasTL}<span class="playic">▶</span>{/if}
          </button>
          <div class="meta">
            <span class="tn">{a.name}</span>
            <span class="muted mono sub">{a.printer}{#if fmtWhen(a.when)} · {fmtWhen(a.when)}{/if}</span>
          </div>
          {#if a.hasTL}<a class="dl mono" href={api.archiveTimelapseUrl(a.id)} download>download</a>{:else}<span class="muted mono notl">no timelapse</span>{/if}
        </div>
      {/each}
    </div>
  {/if}
{/if}

{#if play}
  <div class="lightbox" role="presentation" onclick={() => (play = null)}>
    <button class="lb-close" onclick={() => (play = null)} aria-label="Close">✕</button>
    <!-- svelte-ignore a11y_media_has_caption -->
    <video src={api.archiveTimelapseUrl(play.id)} controls autoplay onclick={(e) => e.stopPropagation()}></video>
    <div class="lb-cap mono">{play.name}</div>
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.2rem; gap: 1rem; }
  .head h1 { margin: 0; }
  .tog, .filt { display: flex; align-items: center; gap: 0.4rem; font-size: 0.85rem; color: var(--ophq-text-2); }
  .tog input, .filt input { width: auto; accent-color: var(--ophq-primary); }
  .filt { margin-bottom: 1rem; }
  .empty { text-align: center; padding: 2.6rem; } .empty .ic { font-size: 1.8rem; }
  .empty p { max-width: 46ch; margin: 0.6rem auto 0; }
  .tls { grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
  .tl { overflow: hidden; padding: 0; }
  .poster { position: relative; width: 100%; aspect-ratio: 16/9; background: var(--ophq-bg-2); border: none; padding: 0; cursor: default; display: block; }
  .poster.playable { cursor: pointer; }
  .poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .noimg { width: 100%; height: 100%; display: grid; place-items: center; color: var(--ophq-muted); font-size: 0.82rem; }
  .playic { position: absolute; inset: 0; margin: auto; width: 46px; height: 46px; display: grid; place-items: center; background: rgba(0,0,0,0.55); color: #fff; border-radius: 50%; font-size: 1.1rem; }
  .meta { padding: 0.6rem 0.8rem 0.2rem; display: flex; flex-direction: column; gap: 0.15rem; }
  .tn { font-weight: 600; font-size: 0.9rem; }
  .sub { font-size: 0.76rem; }
  .dl, .notl { display: block; padding: 0.2rem 0.8rem 0.7rem; font-size: 0.76rem; }
  .dl { color: var(--ophq-primary-2); }
  .lightbox { position: fixed; inset: 0; z-index: 200; background: rgba(3,5,8,0.92); display: grid; place-items: center; padding: 2rem; }
  .lightbox video { max-width: 94vw; max-height: 90vh; border-radius: var(--radius-sm); }
  .lb-close { position: fixed; top: 1.1rem; right: 1.3rem; width: 40px; height: 40px; border-radius: 50%; background: var(--ophq-surface); border: 1px solid var(--ophq-border); color: var(--ophq-text); font-size: 1.1rem; cursor: pointer; }
  .lb-cap { position: fixed; bottom: 1.3rem; left: 50%; transform: translateX(-50%); color: var(--ophq-text-2); font-size: 0.85rem; }
</style>
