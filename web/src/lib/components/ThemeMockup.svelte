<script>
  // A small, self-contained preview of the app chrome rendered with a given set
  // of --ophq-* variables. Setting the variables on the mockup's root scopes them
  // to this subtree (custom properties inherit), so it shows exactly how the theme
  // will look without touching the rest of the page.
  import { TOKEN_KEYS } from '$lib/theme';

  let { vars = {}, textScale = 1, brandName = 'OpenPrintHQ', logo = '', compact = false } = $props();

  const styleStr = $derived(
    TOKEN_KEYS.map((k) => (vars[k] ? `${k}:${vars[k]}` : '')).filter(Boolean).join(';') +
    `;--ophq-text-scale:${textScale}`
  );
</script>

<div class="mock" class:compact style={styleStr} aria-hidden="true">
  <div class="mtop">
    <span class="brand">
      {#if logo}<img src={logo} alt="" class="blogo" />{:else}<span class="dot"></span>{/if}
      <b>{brandName}</b>
    </span>
    <span class="pill">●</span>
  </div>
  <div class="mbody">
    <div class="mside">
      <span class="nav on">Overview</span>
      <span class="nav">Printers</span>
      <span class="nav">Queue</span>
    </div>
    <div class="mmain">
      <div class="mcard">
        <span class="eyebrow">Printer</span>
        <div class="mrow"><span class="h">Voron 2.4</span><span class="chip ok">online</span></div>
        <p class="mtext">Nozzle 210° · Bed 60°</p>
        <div class="mbtns">
          <span class="btn p">Start</span>
          <span class="btn g">Details</span>
        </div>
      </div>
      {#if !compact}
        <div class="mcard tiny"><span class="k">Jobs</span><b>3</b></div>
      {/if}
    </div>
  </div>
</div>

<style>
  .mock {
    background: var(--ophq-bg); border-radius: 10px; overflow: hidden;
    border: 1px solid var(--ophq-border); font-family: var(--font-ui);
    color: var(--ophq-text); box-shadow: var(--shadow); user-select: none;
    container-type: inline-size;
  }
  .mtop {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0.5rem 0.7rem; background: var(--ophq-glass); border-bottom: 1px solid var(--ophq-border-soft);
  }
  .brand { display: inline-flex; align-items: center; gap: 0.4rem; font-size: 0.8rem; color: var(--ophq-text); }
  .brand b { font-weight: 700; }
  .blogo { height: 14px; width: auto; border-radius: 3px; }
  .dot { width: 12px; height: 12px; border-radius: 4px; background: linear-gradient(135deg, var(--ophq-primary-2), var(--ophq-primary)); display: inline-block; }
  .pill { color: var(--ophq-success); font-size: 0.7rem; }
  .mbody { display: grid; grid-template-columns: 78px 1fr; min-height: 118px; }
  .mside { background: var(--ophq-bg-2); border-right: 1px solid var(--ophq-border-soft); padding: 0.5rem 0.4rem; display: flex; flex-direction: column; gap: 0.25rem; }
  .nav { font-size: 0.66rem; color: var(--ophq-text-2); padding: 0.22rem 0.35rem; border-radius: 5px; }
  .nav.on { background: var(--ophq-primary-dim); color: var(--ophq-primary-2); box-shadow: inset 2px 0 0 var(--ophq-primary); }
  .mmain { padding: 0.6rem; display: flex; flex-direction: column; gap: 0.5rem; }
  .mcard { background: linear-gradient(180deg, var(--ophq-surface), var(--ophq-surface-2)); border: 1px solid var(--ophq-border); border-radius: 8px; padding: 0.55rem 0.6rem; }
  .eyebrow { text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.55rem; font-weight: 700; color: var(--ophq-primary-2); font-family: var(--font-mono); }
  .mrow { display: flex; align-items: center; justify-content: space-between; margin: 0.2rem 0; }
  .h { font-size: 0.82rem; font-weight: 700; color: var(--ophq-text); }
  .chip { font-size: 0.58rem; font-weight: 600; padding: 0.1rem 0.4rem; border-radius: 999px; border: 1px solid var(--ophq-border); }
  .chip.ok { color: var(--ophq-success); border-color: color-mix(in srgb, var(--ophq-success) 35%, transparent); background: color-mix(in srgb, var(--ophq-success) 10%, transparent); }
  .mtext { font-size: 0.66rem; color: var(--ophq-muted); margin: 0.15rem 0 0.4rem; }
  .mbtns { display: flex; gap: 0.35rem; }
  .btn { font-size: 0.62rem; font-weight: 600; padding: 0.22rem 0.5rem; border-radius: 6px; border: 1px solid transparent; }
  .btn.p { background: var(--ophq-primary); color: #fff; }
  .btn.g { background: transparent; color: var(--ophq-text); border-color: var(--ophq-border); }
  .mcard.tiny { display: flex; align-items: center; justify-content: space-between; padding: 0.4rem 0.6rem; }
  .mcard.tiny .k { font-size: 0.66rem; color: var(--ophq-muted); }
  .mcard.tiny b { font-family: var(--font-mono); font-size: 1rem; color: var(--ophq-text); }
  .compact .mbody { min-height: 96px; }
</style>
