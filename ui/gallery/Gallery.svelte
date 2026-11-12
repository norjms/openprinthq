<script>
  import { AppShell, Sidebar, GlassCard, WidgetCard, Button, Chip, Segmented, Toggle, Field, GridTable, ProgressBar, Banner, Icon } from '../src/lib/index.js';

  const groups = [
    { label: 'Print', items: [
      { label: 'Farm overview', href: '#farm', icon: 'grid' }, { label: 'Printers', href: '#printers', icon: 'printer', badge: 4, current: true },
      { label: 'Cameras', href: '#cameras', icon: 'camera' }, { label: 'Print queue', href: '#queue', icon: 'list-queue', badge: 6 },
      { label: 'Filament', href: '#filament', icon: 'spool' }, { label: 'Slicing', href: '#slicing', icon: 'layers' } ] },
    { label: 'Library', items: [ { label: 'Models', href: '#models', icon: 'cube', badge: 128 }, { label: 'Add model', href: '#add', icon: 'plus' }, { label: 'Find models', href: '#find', icon: 'search' }, { label: 'Licenses', href: '#licenses', icon: 'file-text' } ] },
    { label: 'Shop', items: [ { label: 'Orders', href: '#orders', icon: 'cart', badge: 3 }, { label: 'Quotes', href: '#quotes', icon: 'message' }, { label: 'Inventory', href: '#inventory', icon: 'box' }, { label: 'Shopping list', href: '#shopping', icon: 'list' }, { label: 'Reports', href: '#reports', icon: 'chart' } ] },
    { label: 'Admin', items: [ { label: 'Sync health', href: '#sync', icon: 'sync' }, { label: 'People', href: '#people', icon: 'users' }, { label: 'Connections', href: '#connections', icon: 'plug' }, { label: 'Devices', href: '#devices', icon: 'tv' }, { label: 'Settings', href: '#settings', icon: 'cog' } ] },
  ];
  const printers = [
    { name: 'H2D-01', model: 'Bambu Lab H2D', status: 'Printing', tone: 'live', pct: 62, job: 'Gear housing, plate 2 of 3', left: '02:14:37' },
    { name: 'MK4S-01', model: 'Prusa MK4S', status: 'Ready', tone: 'ok', pct: 0, job: 'Plate checked 18:40', left: '' },
    { name: 'MK3S-01', model: 'Prusa MK3S + MMU3', status: 'Needs filament', tone: 'warn', pct: 41, job: 'Cable winder, slot 3 empty', left: 'paused' },
    { name: 'V24-01', model: 'Voron 2.4', status: 'Failed', tone: 'bad', pct: 18, job: 'Planter, hex, ASA-CF', left: 'stopped' },
  ];
  let view = $state('cards');
  let autoQueue = $state(true);
</script>

{#snippet side()}<Sidebar product="ArtisanHub" {groups} />{/snippet}
{#snippet head()}
  <Button size="sm">Artisan Prints <Icon name="chevron-down" size={14} /></Button>
  <div class="ah-header__crumbs">Print / <b>Printers</b></div>
  <button class="ah-round-btn" style="margin:0;width:38px;height:38px" aria-label="Notifications"><Icon name="bell" size={17} /></button>
{/snippet}
{#snippet filters()}
  <Segmented label="View" bind:value={view} options={[{ value: 'cards', label: 'Cards' }, { value: 'list', label: 'List' }]} />
  <Button variant="primary"><Icon name="plus" size={16} />Add printer</Button>
{/snippet}
{#snippet widgets()}
  <WidgetCard icon="spool" title="Filament on hand" subtitle="From Spoolman" href="#filament">
    <GridTable label="Spools" columns={[{ key: 'spool', label: 'Spool' }, { key: 'left', label: 'Left', width: '80px' }]} rows={[{ spool: 'PLA Galaxy Black', left: '120 g' }, { spool: 'PETG Clear', left: '840 g' }]} />
  </WidgetCard>
  <WidgetCard icon="cog" title="Automation" subtitle="How orders become prints">
    <div style="display:flex;align-items:center;gap:12px" class="ah-panel"><div style="flex:1">Auto-queue prints</div><Toggle label="Auto-queue prints" bind:checked={autoQueue} /></div>
    <Field id="theme" label="Theme" help="More themes will be offered here."><select id="theme" class="ah-select"><option>Glass dark</option></select></Field>
  </WidgetCard>
{/snippet}

<AppShell sidebar={side} header={head} title="Printers" description="Every printer in ArtisanHub Farm, live." {filters} {widgets}>
  <Banner><Icon name="alert" />MK3S-01 slot 3 is empty. Load PLA Galaxy Black.</Banner>
  <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;overflow:auto;min-height:0">
    {#each printers as p (p.name)}
      <GlassCard>
        <div style="display:flex;align-items:center;gap:10px"><h2 class="ah-card-title">{p.name}</h2><span class="ah-muted">{p.model}</span><span style="margin-left:auto"><Chip tone={p.tone}>{p.status}</Chip></span></div>
        <div style="display:flex" class="ah-muted"><span>{p.job}</span><span class="ah-num" style="margin-left:auto">{p.left}</span></div>
        <ProgressBar value={p.pct} label="{p.name} progress" attention={p.tone === 'warn'} />
        <div style="display:flex;gap:8px"><Button size="sm">Open</Button>{#if p.tone === 'bad'}<Button size="sm" variant="danger">Clear error</Button>{/if}</div>
      </GlassCard>
    {/each}
  </div>
</AppShell>
