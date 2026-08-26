<script>
  // SPDX-License-Identifier: AGPL-3.0-or-later
  import { onMount } from 'svelte';
  import AppShell from '$lib/components/AppShell.svelte';
  import { api } from '$lib/api';
  import { warm } from '$lib/camcache.js';
  let { children } = $props();

  // Keep a small, recent still for every printer so any page that shows a camera
  // has something to paint in its first frame instead of a black rectangle. This
  // runs once the app shell mounts (so: after sign-in, on whichever page you
  // land on) and then on a slow timer.
  //
  // The cadence is deliberately lazy. These frames are placeholders that get
  // replaced within a second by the live view, so a stale one costs nothing,
  // while the requests travel over the same connector tunnel that carries print
  // control. warm() skips anything already fresh and paces itself, so the steady
  // state is a handful of small requests every few minutes, not a burst.
  const REFRESH_MS = 5 * 60_000;

  onMount(() => {
    let stopped = false;
    let timer = null;

    async function pass() {
      if (stopped) return;
      try {
        const d = await api.printers();
        const arr = Array.isArray(d) ? d : (d?.printers || d?.items || []);
        const ids = arr.map((p) => p.id ?? p.printer_id).filter((v) => v != null);
        if (ids.length) await warm(ids, { maxAgeMs: REFRESH_MS });
      } catch { /* no instance yet, offline, not signed in — nothing to warm */ }
      if (!stopped) timer = setTimeout(pass, REFRESH_MS);
    }

    // Let the page it is rendering get on with its own loading first. The warm
    // is for the NEXT navigation, never for this one.
    timer = setTimeout(pass, 3000);
    return () => { stopped = true; if (timer) clearTimeout(timer); };
  });
</script>

<AppShell>
  {@render children()}
</AppShell>
