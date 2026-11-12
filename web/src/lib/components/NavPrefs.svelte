<script>
  // Settings, Appearance, Navigation. Lets each user reorder (within a group)
  // or hide the built-in sidebar items and add their own external links. Kept
  // per user in the appearance config under `nav`; the AppShell reads the same
  // store, so a save shows in the sidebar straight away.
  import { get } from 'svelte/store';
  import Icon from '$lib/components/AppIcon.svelte';
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import { appearance, saveAppearance as persist } from '$lib/stores/appearance';
  import { normalizeNav } from '$lib/theme';
  import {
    NAV_ITEMS, GENFILAMENT_ITEM, NAV_SETTINGS_HREF, isHttpUrl
  } from '$lib/nav';

  let genfilament = $state(false);
  let items = $state([]);      // [{ href, label, icon, hidden }] in the user's order
  let links = $state([]);      // [{ label, url }]
  let saving = $state(false);
  let msg = $state(null);

  onMount(() => {
    api.myInstance().then((i) => { genfilament = !!(i?.features && i.features.genfilament); seed(); }).catch(() => seed());
  });

  // Seed the editable draft from the saved nav prefs, mirroring how AppShell
  // merges defaults + prefs (robust to missing / extra / duplicate hrefs).
  function seed() {
    const nav = normalizeNav(get(appearance)?.nav);
    const builtins = genfilament ? [...NAV_ITEMS, GENFILAMENT_ITEM] : [...NAV_ITEMS];
    const byHref = new Map(builtins.map((i) => [i.href, i]));
    const seen = new Set();
    const ordered = [];
    for (const href of nav.order) {
      if (byHref.has(href) && !seen.has(href)) { ordered.push(byHref.get(href)); seen.add(href); }
    }
    for (const it of builtins) {
      if (!seen.has(it.href)) { ordered.push(it); seen.add(it.href); }
    }
    const hidden = new Set(nav.hidden);
    items = ordered.map((it) => ({ ...it, hidden: it.href !== NAV_SETTINGS_HREF && hidden.has(it.href) }));
    links = nav.links.map((l) => ({ label: l.label, url: l.url }));
    msg = null;
  }

  // Items move within their own group; groups keep their place in the sidebar.
  function neighbour(i, dir) {
    for (let j = i + dir; j >= 0 && j < items.length; j += dir) if (items[j].group === items[i].group) return j;
    return -1;
  }
  function move(i, dir) {
    const j = neighbour(i, dir);
    if (j < 0) return;
    const next = [...items];
    [next[i], next[j]] = [next[j], next[i]];
    items = next;
  }
  function toggleHidden(i) {
    if (items[i].href === NAV_SETTINGS_HREF) return; // Settings is always visible
    items[i].hidden = !items[i].hidden;
  }

  function addLink() { links = [...links, { label: '', url: '' }]; }
  function removeLink(i) { links = links.filter((_, k) => k !== i); }

  // Build the persisted nav prefs from the draft.
  function buildNav() {
    const order = items.map((i) => i.href);
    const hidden = items.filter((i) => i.hidden && i.href !== NAV_SETTINGS_HREF).map((i) => i.href);
    const cleanLinks = links
      .map((l) => ({ label: (l.label || '').trim(), url: (l.url || '').trim() }))
      .filter((l) => isHttpUrl(l.url))
      .map((l) => ({ label: l.label || l.url, url: l.url }));
    return { order, hidden, links: cleanLinks };
  }

  async function save() {
    saving = true; msg = null;
    try {
      // Merge onto the current full config so the other settings are untouched.
      await persist({ ...get(appearance), nav: buildNav() });
      seed(); // re-seed from the (normalized) saved state
      msg = { ok: true, text: 'Navigation saved.' };
    } catch (e) {
      msg = { ok: false, text: e.message || 'Could not save navigation.' };
    } finally { saving = false; }
  }

  async function resetNav() {
    saving = true; msg = null;
    try {
      await persist({ ...get(appearance), nav: { order: [], hidden: [], links: [] } });
      seed();
      msg = { ok: true, text: 'Navigation reset to default.' };
    } catch (e) {
      msg = { ok: false, text: e.message || 'Could not reset navigation.' };
    } finally { saving = false; }
  }
</script>

<div class="np card card-pad">
  <div class="np-head">
    <div>
      <span class="eyebrow">Navigation</span>
      <p class="muted tiny">Reorder or hide the sidebar items and add your own links. Saved to your account; the sidebar updates straight away.</p>
    </div>
    <div class="np-actions">
      <button class="btn btn-ghost btn-sm" onclick={resetNav} disabled={saving}>Reset to default</button>
      <button class="btn btn-primary btn-sm" onclick={save} disabled={saving}>{saving ? 'Saving' : 'Save navigation'}</button>
    </div>
  </div>
  {#if msg}<p class={msg.ok ? 'ok-msg' : 'err'}>{msg.text}</p>{/if}

  <div class="tglabel">Built-in items</div>
  <ul class="rows">
    {#each items as item, i (item.href)}
      <li class="row" class:off={item.hidden}>
        <span class="ic" aria-hidden="true"><Icon name={item.icon} size={17} /></span>
        <span class="lbl">{item.label} <span class="ah-muted">{item.group}</span></span>
        <label class="show" title={item.href === NAV_SETTINGS_HREF ? 'Settings is always shown' : 'Show in sidebar'}>
          <input type="checkbox" checked={!item.hidden} disabled={item.href === NAV_SETTINGS_HREF}
                 onchange={() => toggleHidden(i)} aria-label={`Show ${item.label}`} />
          <span>Show</span>
        </label>
        <span class="movers">
          <button class="btn btn-ghost btn-xs up" onclick={() => move(i, -1)} disabled={neighbour(i, -1) < 0} aria-label={`Move ${item.label} up`}><Icon name="chevron-down" size={14} /></button>
          <button class="btn btn-ghost btn-xs" onclick={() => move(i, 1)} disabled={neighbour(i, 1) < 0} aria-label={`Move ${item.label} down`}><Icon name="chevron-down" size={14} /></button>
        </span>
      </li>
    {/each}
  </ul>

  <div class="tglabel links-head">Custom links</div>
  <p class="muted tiny">External links only (must start with http:// or https://). Blank or invalid rows are dropped on save.</p>
  <div class="links">
    {#each links as link, i (i)}
      <div class="lrow">
        <input class="li" type="text" placeholder="Label" bind:value={link.label} aria-label="Link label" />
        <input class="li" type="url" placeholder="https://example.com" bind:value={link.url} aria-label="Link URL" />
        <button class="btn btn-ghost btn-sm" onclick={() => removeLink(i)}>Remove</button>
      </div>
    {/each}
    <button class="btn btn-ghost btn-sm" onclick={addLink}><Icon name="plus" size={15} />Add link</button>
  </div>
</div>

<style>
  .np { margin: 0; }
  .np-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
  .np-head p { margin: 0.3rem 0 0.6rem; max-width: 60ch; }
  .np-actions { display: flex; gap: 0.5rem; flex-shrink: 0; }
  .ok-msg { color: var(--ophq-success); font-size: 0.9rem; }
  .err { color: var(--ophq-danger); font-size: 0.9rem; }
  .tiny { font-size: 0.82rem; }
  .tglabel { display: block; font-size: 12px; font-weight: 600; color: var(--ah-group-label); text-transform: uppercase; letter-spacing: 0.12em; margin: 0.9rem 0 0.5rem; }
  .links-head { margin-top: 1.2rem; }

  .rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.35rem; }
  .row { display: grid; grid-template-columns: auto 1fr auto auto; align-items: center; gap: 0.7rem; padding: 0.4rem 0.6rem; border: 1px solid var(--ophq-border); border-radius: var(--radius-sm); background: var(--ophq-bg-2); }
  .row.off { opacity: 0.55; }
  .row .ic { display: grid; place-items: center; color: var(--ah-text-2); }
  .lbl { font-size: 0.9rem; color: var(--ophq-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .show { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.8rem; color: var(--ophq-text-2); cursor: pointer; }
  .show input { width: 16px; height: 16px; accent-color: var(--ophq-primary); }
  .movers { display: inline-flex; gap: 0.25rem; }
  .btn-xs { height: 30px; width: 30px; padding: 0; }
  .btn-xs.up :global(svg) { transform: rotate(180deg); }

  .links { display: flex; flex-direction: column; gap: 0.5rem; align-items: flex-start; }
  .lrow { display: grid; grid-template-columns: 1fr 2fr auto; gap: 0.5rem; width: 100%; }
  .li { height: 36px; font-size: 14px; background: var(--ah-field); border: 1px solid var(--ah-field-line); color: var(--ah-text); border-radius: var(--ah-radius-btn-sm); padding: 0 10px; }

  @media (max-width: 900px) {
    .np-head { flex-direction: column; }
    .lrow { grid-template-columns: 1fr auto; }
  }
</style>
