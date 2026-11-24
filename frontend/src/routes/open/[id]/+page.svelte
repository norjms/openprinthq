<script>
  // A desktop app inside the hub (docs/apps.md): the hub's sidebar and header stay, and the app's desktop, served
  // at its own address (the sidebar item's frame, /apps/<id>/), is shown below a bar with Pop out, Open in a new
  // tab and Full screen. The page finds the app in the sidebar the hub serves, so it shows only apps this hub runs
  // and the person may see.
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { nav } from '$lib/nav.svelte.js';
  import Page from '$lib/Page.svelte';
  import Glyph from '$lib/Glyph.svelte';

  const id = $derived(page.params.id);
  const item = $derived(nav.groups.flatMap((g) => g.items).find((i) => i.id === `app-${id}` && i.frame));
  // The sidebar starts as the hub's built-in copy and becomes the server's answer, which names the apps.
  let loaded = $state(false);
  const sent = $derived(page.url.searchParams.get('sent') || '');

  let full = $state(false);
  function onKey(e) { if (e.key === 'Escape') full = false; }

  // The desktop in a window of its own: moved there, not copied, as a desktop shows on one screen at a time. The
  // page shows it again when that window closes.
  let popup = null;
  let popped = $state(false);
  let watch = null;
  function popOut() {
    const w = Math.min(1600, window.screen?.availWidth || 1600);
    const h = Math.min(1000, window.screen?.availHeight || 1000);
    popup = window.open(item.frame, `openprinthq-${id}`, `popup=yes,width=${w},height=${h}`);
    if (!popup) return; // a pop-up blocker: the page keeps it
    popped = true; full = false;
    clearInterval(watch);
    watch = setInterval(() => { if (!popup || popup.closed) { popped = false; clearInterval(watch); } }, 1000);
  }
  function bringBack() { try { popup?.close(); } catch { /* */ } popped = false; clearInterval(watch); }

  // Its height on the page, dragged at its bottom edge, kept for each app in this browser.
  let height = $state(null);
  function rememberHeight(el) {
    const key = `ahi.desktop.height.${id}`;
    try { const h = Number(localStorage.getItem(key)); if (h >= 320) height = h; } catch { /* */ }
    const seen = new ResizeObserver(() => {
      if (full || !el.offsetHeight) return;
      try { localStorage.setItem(key, String(el.offsetHeight)); } catch { /* */ }
    });
    seen.observe(el);
    return { destroy: () => seen.disconnect() };
  }

  onMount(() => {
    const t = setTimeout(() => (loaded = true), 3000);
    window.addEventListener('keydown', onKey);
    return () => { clearTimeout(t); clearInterval(watch); window.removeEventListener('keydown', onKey); };
  });
  $effect(() => { if (item) loaded = true; });
</script>

<Page title={item ? item.label : 'App'} description={item ? 'A desktop in your browser, run by this hub.' : ''}>
  {#if item}
    <section class="ah-glass ah-card workspace" class:full data-testid="app-workspace">
      <div class="wsbar">
        <div class="who">
          <span class="dot"></span>
          <b>{item.label}</b>
          {#if item.experimental}<span class="chip warn">Experimental</span>{/if}
        </div>
        <div class="acts">
          {#if popped}
            <button class="btn sm" onclick={bringBack}><Glyph name="arrow-left" size={15} />Bring it back here</button>
          {:else}
            <button class="btn sm" onclick={popOut} data-testid="pop-out"><Glyph name="external" size={15} />Pop out</button>
            <a class="btn sm" href={item.frame} target="_blank" rel="noopener" data-testid="new-tab"><Glyph name="external" size={15} />Open in a new tab</a>
            <button class="btn sm" onclick={() => (full = !full)} data-testid="full-screen"><Glyph name={full ? 'collapse' : 'expand'} size={15} />{full ? 'Exit full screen' : 'Full screen'}</button>
          {/if}
        </div>
      </div>
      {#if sent}
        <p class="note ah-muted" data-testid="app-sent">Opening <b>{sent}</b> in {item.label}. It also stays in Files, From the farm.</p>
      {/if}
      {#if popped}
        <p class="note ah-muted">{item.label} is open in its own window. Resize it as you like; close it to have it here again.</p>
      {:else}
        <div class="frame" use:rememberHeight style={height && !full ? `height:${height}px` : ''} data-testid="app-frame">
          <iframe title={item.label} src={item.frame} allow="clipboard-read; clipboard-write; fullscreen"></iframe>
        </div>
      {/if}
    </section>
  {:else if loaded}
    <section class="ah-glass ah-card">
      <p>This hub has no app called {id}, or it is not given to you. The hub's owner gives apps to people on
        <a href="/people">People</a>.</p>
    </section>
  {/if}
</Page>

<style>
  .workspace { padding: 0; overflow: hidden; }
  .wsbar { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap;
           padding: 10px 16px; border-bottom: 1px solid var(--ah-row-line); }
  .who, .acts { display: flex; align-items: center; gap: 8px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--ophq-success, #9be28f);
         box-shadow: 0 0 0 4px rgba(155, 226, 143, 0.18); flex: none; }
  /* Drag the bottom right corner to change its height; the desktop fits itself to the new size. */
  .frame { height: calc(100vh - 260px); min-height: 320px; max-height: 2400px; resize: vertical; overflow: hidden; }
  iframe { display: block; width: 100%; height: 100%; border: 0; background: var(--ah-solid-panel, #111); }
  .workspace.full { position: fixed; inset: 0; z-index: 200; margin: 0; border-radius: 0; display: flex; flex-direction: column; }
  .workspace.full .frame { flex: 1; height: auto !important; min-height: 0; resize: none; }
  .note { padding: 10px 16px 0; margin: 0; }
  .workspace .note:last-child { padding-bottom: 20px; }
</style>
