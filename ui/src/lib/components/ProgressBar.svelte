<script>
  /** @type {{ value: number, label: string, attention?: boolean }} */
  let { value, label, attention = false } = $props();
  const pct = $derived(Math.max(0, Math.min(100, Math.round(value))));
</script>

<div class="ah-progress{attention ? ' ah-progress--attention' : ''}" role="progressbar" aria-label={label}
  aria-valuemin="0" aria-valuemax="100" aria-valuenow={pct}><i style="width:{pct}%"></i></div>
