<script>
  // Timelapses: recorded prints from every camera, newest first.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import PageTitle from '$lib/components/PageTitle.svelte';
  import PageHead from '$lib/components/PageHead.svelte';
  import Icon from '$lib/components/AppIcon.svelte';
  import Timelapses from '$lib/components/Timelapses.svelte';

  let reload = $state();
</script>

<PageTitle page="Timelapses" />

<PageHead title="Timelapses" description="Recorded prints from every camera, newest first.">
  {#snippet actions()}
    <button class="btn" onclick={() => reload?.()}><Icon name="refresh" size={16} />Refresh</button>
  {/snippet}
</PageHead>

<div class="tlpage fill">
  <Timelapses bind:reload />
</div>

<style>
  .tlpage { display: flex; flex-direction: column; min-height: 0; flex: 1; }
</style>
