<script>
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
      const r = await fetch(`/api/pub/tag/${encodeURIComponent(code)}`, { headers: { accept: 'application/json' } });
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
  <title>{state === 'ready' ? title : 'Filament tag'} · OpenPrintHQ</title>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="robots" content="noindex, nofollow" />
</svelte:head>

<main>
  {#if state === 'loading'}
    <p class="quiet">Reading tag…</p>
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
      <a class="open" href="/app/filament">Open in OpenPrintHQ</a>
    </footer>
  {/if}
</main>

<style>
  :global(body) { margin: 0; background: #0f1115; color: #e8eaed; }
  main {
    font: 16px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    max-width: 30rem; margin: 0 auto; padding: 1.5rem 1.25rem 3rem;
    padding-top: max(1.5rem, env(safe-area-inset-top));
  }
  h1 { font-size: 1.6rem; line-height: 1.15; margin: 0.35rem 0 0; }
  .chip {
    display: inline-block; background: #e8eaed; color: #0f1115; font-weight: 700;
    letter-spacing: 0.04em; padding: 0.15rem 0.5rem; border-radius: 0.25rem; font-size: 0.95rem;
  }
  .product { margin: 0.25rem 0 0; color: #a8adb6; }
  .quiet { color: #a8adb6; }
  .headline { display: flex; align-items: center; gap: 0.85rem; margin: 1.5rem 0; }
  .headline strong { display: block; font-size: 2rem; line-height: 1; }
  .headline .quiet { font-size: 0.9rem; }
  .swatch {
    width: 3rem; height: 3rem; border-radius: 0.5rem; flex: none;
    border: 1px solid rgba(255, 255, 255, 0.25);
  }
  .values { border-top: 1px solid #262a33; }
  .row { display: flex; justify-content: space-between; padding: 0.6rem 0; border-bottom: 1px solid #262a33; }
  .row span { color: #a8adb6; }
  .row strong { font-variant-numeric: tabular-nums; }
  .notes p { color: #a8adb6; font-size: 0.9rem; margin: 1rem 0 0; }
  footer { margin-top: 1.75rem; }
  dl { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem 1rem; margin: 0 0 1.5rem; }
  dl .wide { grid-column: 1 / -1; }
  dt { color: #a8adb6; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }
  dd { margin: 0.15rem 0 0; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85em; }
  .open {
    display: block; text-align: center; padding: 0.8rem; border-radius: 0.5rem;
    background: #2f6df6; color: #fff; text-decoration: none; font-weight: 600;
  }
</style>
