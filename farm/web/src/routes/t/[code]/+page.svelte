<script>
  import { base as basePath } from '$app/paths';
  // What a phone sees when it scans a filament tag or a label's QR code.
  //
  // Deliberately reachable WITHOUT login. The code in the URL is the only
  // credential: a tag's code is its NTAG UID, and a spool's code carries a
  // signature, so ids cannot be walked. Nothing about the account comes back,
  // and only the one spool that was scanned is ever shown.
  //
  // Built for a phone held at a shelf: the material and what is left have to be
  // readable at arm's length, the print values without scrolling.
  import { page } from '$app/stores';
  import { onMount } from 'svelte';

  let state = $state('loading');
  let spool = $state(null);
  let filament = $state(null);
  let problem = $state('');

  const code = $derived($page.params.code);

  onMount(async () => {
    try {
      const r = await fetch(`${basePath}/api/pub/tag/${encodeURIComponent(code)}`, { headers: { accept: 'application/json' } });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        problem = body.error || `Lookup failed (${r.status})`;
        state = r.status === 404 ? 'unknown' : 'error';
        return;
      }
      spool = body.spool;
      filament = body.filament;
      state = 'ready';
    } catch (e) {
      problem = 'Could not reach the server.';
      state = 'error';
    }
  });

  const title = $derived(spool ? [spool.brand, spool.material, spool.subtype].filter(Boolean).join(' ') : 'Filament tag');
  const swatch = $derived(spool?.rgba ? '#' + String(spool.rgba).slice(0, 6) : 'transparent');

  function grams(v) {
    return v === null || v === undefined ? null : `${Math.round(v)} g`;
  }

  // Only the values a person standing at a printer needs. A missing one is left
  // out rather than shown as a blank row.
  const values = $derived.by(() => {
    const s = filament?.specs;
    if (!s) return [];
    const rows = [];
    const lo = s.nozzle_temp_range_low, hi = s.nozzle_temp_range_high;
    if (lo && hi && hi > lo) rows.push(['Nozzle', `${Math.round(lo)} to ${Math.round(hi)} C`]);
    else if (s.nozzle_temp_normal) rows.push(['Nozzle', `${Math.round(s.nozzle_temp_normal)} C`]);
    if (s.nozzle_temp_initial_layer) rows.push(['First layer', `${Math.round(s.nozzle_temp_initial_layer)} C`]);
    if (s.bed_temp) rows.push(['Bed', `${Math.round(s.bed_temp)} C`]);
    if (s.chamber_temp) rows.push(['Chamber', `${Math.round(s.chamber_temp)} C`]);
    if (s.density) rows.push(['Density', String(s.density)]);
    if (s.flow_ratio) rows.push(['Flow', String(s.flow_ratio)]);
    return rows;
  });
</script>

<svelte:head>
  <title>{state === 'ready' ? title : 'Filament tag'} · ArtisanHub Farm</title>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="robots" content="noindex, nofollow" />
</svelte:head>

<div class="ah-root ah-page tagpage">
<main class="ah-glass">
  {#if state === 'loading'}
    <p class="quiet">Reading tag</p>
  {:else if state === 'unknown'}
    <h1>Nothing linked to this tag</h1>
    <p class="quiet">Code <code>{code}</code> does not match a spool. If you just wrote this tag, link it to a
      spool in the tag writer first.</p>
  {:else if state === 'error'}
    <h1>Could not look that up</h1>
    <p class="quiet">{problem}</p>
  {:else}
    <header>
      <span class="chip">{spool.material}</span>
      <h1>{spool.brand || 'Unknown brand'}</h1>
      <p class="product">{[spool.subtype, spool.color_name].filter(Boolean).join(' · ') || filament?.product_name || ''}</p>
    </header>

    <section class="headline">
      <div class="swatch" style:background={swatch} aria-hidden="true"></div>
      <div>
        <strong>{grams(spool.remaining_weight) ?? 'Weight unknown'}</strong>
        <span class="quiet">
          {#if spool.label_weight && spool.remaining_weight !== null && spool.remaining_weight !== spool.label_weight}
            left of {grams(spool.label_weight)}
          {:else}
            on the spool
          {/if}
        </span>
      </div>
    </section>

    {#if values.length}
      <section class="values">
        {#each values as [label, value]}
          <div class="row"><span>{label}</span><strong>{value}</strong></div>
        {/each}
      </section>
    {:else}
      <p class="quiet">No stored print values for this filament yet.</p>
    {/if}

    {#if filament?.specs?.notes}
      <section class="notes"><p>{filament.specs.notes}</p></section>
    {/if}

    <footer>
      <dl>
        <div><dt>Spool</dt><dd>#{spool.id}</dd></div>
        {#if spool.location}<div><dt>Location</dt><dd>{spool.location}</dd></div>{/if}
        {#if spool.tag_uid}<div><dt>Tag</dt><dd><code>{spool.tag_uid}</code></dd></div>{/if}
        {#if spool.note}<div class="wide"><dt>Note</dt><dd>{spool.note}</dd></div>{/if}
      </dl>
      <a class="open" href="{basePath}/app/filament">Open in ArtisanHub Farm</a>
    </footer>
  {/if}
</main>
</div>

<style>
  .tagpage { min-height: 100vh; padding: 16px; padding-top: max(16px, env(safe-area-inset-top)); display: grid; place-items: start center; }
  main { width: min(30rem, 100%); padding: 22px 20px 24px; font-size: 16px; line-height: 1.45; }
  h1 { font-family: var(--ah-font-display); font-weight: 500; letter-spacing: -0.03em; font-size: 28px; line-height: 1.15; margin: 6px 0 0; }
  .chip { font-size: 13px; font-weight: 600; letter-spacing: 0.04em; }
  .product { margin: 4px 0 0; color: var(--ah-text-2); }
  .quiet { color: var(--ah-text-3); }
  .headline { display: flex; align-items: center; gap: 14px; margin: 20px 0; }
  .headline strong { display: block; font-family: var(--ah-font-display); font-weight: 500; letter-spacing: -0.03em; font-size: 34px; line-height: 1; font-variant-numeric: tabular-nums; }
  .headline .quiet { font-size: 14px; }
  .swatch { width: 48px; height: 48px; border-radius: 14px; flex: none; border: 1px solid var(--ah-field-line); }
  .values { border-top: 1px solid var(--ah-row-line); }
  .row { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid var(--ah-row-line); }
  .row span { color: var(--ah-text-2); }
  .row strong { font-variant-numeric: tabular-nums; }
  .notes p { color: var(--ah-text-2); font-size: 14px; margin: 14px 0 0; }
  footer { margin-top: 24px; }
  dl { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; margin: 0 0 20px; }
  dl .wide { grid-column: 1 / -1; }
  dt { color: var(--ah-group-label); font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.12em; }
  dd { margin: 2px 0 0; }
  .open { display: flex; align-items: center; justify-content: center; height: 44px; border-radius: var(--ah-radius-btn); background: var(--ah-accent); color: var(--ah-on-accent); text-decoration: none; font-weight: 600; }
  .tagpage a.open:hover { color: var(--ah-on-accent); }
</style>
