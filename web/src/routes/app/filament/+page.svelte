<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let loading = $state(true);
  let error = $state(null);
  let spools = $state([]);

  function norm(d) {
    const arr = Array.isArray(d) ? d : (d?.spools || d?.items || d?.results || []);
    return arr.map((s) => {
      const label = s.label_weight ?? s.total_weight ?? s.spool_weight ?? null;
      const used = s.weight_used ?? null;
      const remaining = s.remaining_weight ?? s.remaining_grams ?? s.weight_remaining ??
        (label != null && used != null ? Math.max(0, label - used) : null);
      // Bambu AMS colours come as RRGGBBAA hex; browsers accept 8-digit hex but
      // drop the alpha so the swatch is always opaque.
      const raw = s.rgba || s.color_hex || s.color || '';
      const color = raw ? (String(raw).startsWith('#') ? raw : '#' + String(raw).slice(0, 6)) : '';
      return {
        id: s.id ?? s.spool_id ?? s.tray_uuid,
        name: s.slicer_filament_name ?? s.name ?? s.filament_name ??
          (`${s.brand ?? ''} ${s.material ?? ''} ${s.subtype ?? ''}`.replace(/\s+/g, ' ').trim() || 'Spool'),
        material: [s.material ?? s.filament_type, s.subtype].filter(Boolean).join(' · '),
        colorName: s.color_name ?? '',
        color,
        remaining: remaining != null ? Math.round(remaining) : null,
        total: label
      };
    });
  }
  async function load() {
    loading = true; error = null;
    try { spools = norm(await api.spools()); }
    catch (e) { error = e.status === 409 ? 'no-instance' : (e.message || 'engine unreachable'); }
    finally { loading = false; }
  }
  onMount(load);
  const pct = (s) => (s.remaining != null && s.total ? Math.max(0, Math.min(100, Math.round((s.remaining / s.total) * 100))) : null);
</script>

<svelte:head><title>Filament · OpenPrintHQ</title></svelte:head>

<div class="head">
  <div><h1>Filament</h1><p class="muted">Spool inventory, usage and cost.</p></div>
  <button class="btn btn-ghost btn-sm" onclick={load}>Refresh</button>
</div>

{#if loading}
  <div class="card card-pad muted">Loading spools…</div>
{:else if error === 'no-instance'}
  <div class="card card-pad"><p class="muted">Provision your instance from the <a href="/app">overview</a> first.</p></div>
{:else if error}
  <div class="card card-pad"><h3>Engine unreachable</h3><p class="muted">{error}</p><button class="btn btn-ghost btn-sm" onclick={load}>Retry</button></div>
{:else if spools.length === 0}
  <div class="card card-pad empty">
    <div class="ic">🧵</div>
    <h3>No spools yet</h3>
    <p class="muted">Track your filament here — remaining weight is deducted automatically as you print, with per-print cost.</p>
  </div>
{:else}
  <div class="grid spools">
    {#each spools as s}
      <div class="card card-pad spool">
        <div class="flex center gap">
          <span class="dot" style="background:{s.color || 'var(--ophq-faint)'}"></span>
          <div>
            <div class="sname">{s.name}</div>
            <div class="muted mono mat">{s.material}{#if s.colorName} · {s.colorName}{/if}</div>
          </div>
        </div>
        {#if pct(s) != null}
          <div class="bar"><div class="fill" style="width:{pct(s)}%"></div></div>
          <div class="muted mono rem">{s.remaining} g left · {pct(s)}%</div>
        {/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.4rem; }
  .head h1 { margin: 0; }
  .empty { text-align: center; padding: 2.6rem; }
  .empty .ic { font-size: 1.8rem; margin-bottom: 0.3rem; }
  .empty p { max-width: 48ch; margin: 0.6rem auto 0; }
  .spools { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
  .dot { width: 22px; height: 22px; border-radius: 50%; border: 1px solid var(--ophq-border); flex: none; }
  .sname { font-weight: 500; }
  .mat { font-size: 0.8rem; }
  .bar { height: 6px; background: var(--ophq-bg-2); border-radius: 3px; margin-top: 0.8rem; overflow: hidden; }
  .fill { height: 100%; background: var(--ophq-primary); }
  .rem { font-size: 0.8rem; margin-top: 0.4rem; }
</style>
