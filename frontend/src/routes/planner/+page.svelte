<script>
  import Page from '$lib/Page.svelte';
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { Icon } from 'openprinthq-ui';
  let include = $state('live');
  let plan = $state(null);
  let showAll = $state(false);
  async function load() { plan = await api.get(`/api/planner?include=${include}`); }
  onMount(load);
  async function pos() {
    const r = await attempt(() => api.post('/api/planner/purchase-orders'));
    toast(r.purchase_orders.length ? `Created purchase orders: ${r.purchase_orders.join(', ')}` : 'Nothing to order from suppliers with InvenTree links');
  }
  const fil = $derived(plan ? plan.filament.filter((x) => showAll || x.short_g > 0) : []);
  const parts = $derived(plan ? plan.parts.filter((x) => showAll || x.short > 0) : []);
</script>

<Page title="Shopping list" description={`Covers open orders plus the supply buffer for each ${include === 'live' ? 'live ' : ''}product.`}>
{#snippet actions()}
  <div class="ah-seg" role="group" aria-label="Which products">
    <button type="button" aria-pressed={include === 'live'} onclick={() => { include = 'live'; load(); }}>Live products</button>
    <button type="button" aria-pressed={include === 'all'} onclick={() => { include = 'all'; load(); }}>All products (planning)</button>
  </div>
  <label class="flex items-center gap-2"><input type="checkbox" bind:checked={showAll} /> Show covered items</label>
  <a class="btn" href={`/api/planner.csv?include=${include}`}><Icon name="download" size={16} />Download CSV</a>
  <button class="btn primary" onclick={pos}>Create purchase orders</button>
{/snippet}
{#if plan}
  <div class="grid gap-[14px]" style="grid-template-columns:repeat(2,minmax(0,1fr));flex:1;min-height:0">
    <section class="ah-glass ah-card ah-scroll">
      <h2 class="ah-card-title">Filament</h2>
      <table class="grid">
        <thead><tr><th>Filament</th><th class="text-right">Need</th><th class="text-right">Have</th><th class="text-right">Buy</th><th>Where</th></tr></thead>
        <tbody>
          {#each fil as f}
            <tr>
              <td><span class="inline-flex items-center gap-2"><span class="swatch" style="background:{f.color_hex || 'transparent'}"></span>{f.label}</span><div class="text-xs muted">{f.used_by.join(', ')}</div></td>
              <td class="text-right">{f.required_g} g</td><td class="text-right">{f.on_hand_g} g</td>
              <td class="text-right">{#if f.buy_spools}<span class="chip bad">{f.buy_spools} spool{f.buy_spools > 1 ? 's' : ''}</span>{:else}<span class="chip ok">Covered</span>{/if}</td>
              <td class="text-xs">{#if f.catalog}{f.catalog.vendor} {f.catalog.name}{#if f.catalog.url} <a class="underline" target="_blank" href={f.catalog.url}>buy</a>{/if}{:else}<span class="muted">Not in the Spoolman catalog</span>{/if}</td>
            </tr>
          {:else}<tr><td colspan="5" class="muted text-center py-6">Nothing to buy.</td></tr>{/each}
        </tbody>
      </table>
    </section>
    <section class="ah-glass ah-card ah-scroll">
      <h2 class="ah-card-title">Parts and packaging</h2>
      <table class="grid">
        <thead><tr><th>Part</th><th class="text-right">Need</th><th class="text-right">Have</th><th class="text-right">On order</th><th class="text-right">Buy</th><th>Supplier</th></tr></thead>
        <tbody>
          {#each parts as p}
            <tr>
              <td>{p.name}<div class="text-xs muted">{p.used_by.join(', ')}</div></td>
              <td class="text-right">{p.required}</td><td class="text-right">{p.on_hand}</td><td class="text-right">{p.on_order || ''}</td>
              <td class="text-right">{#if p.buy_packs}<span class="chip bad">{p.buy_packs} pack{p.buy_packs > 1 ? 's' : ''}</span>{:else}<span class="chip ok">Covered</span>{/if}</td>
              <td class="text-xs">{#if p.supplier}{#if p.supplier.link}<a class="underline" target="_blank" href={p.supplier.link}>{p.supplier.name || 'buy'}</a>{:else}{p.supplier.name}{/if}{:else}<span class="muted">No supplier</span>{/if}</td>
            </tr>
          {:else}<tr><td colspan="6" class="muted text-center py-6">Nothing to buy.</td></tr>{/each}
        </tbody>
      </table>
    </section>
  </div>
{/if}
</Page>
