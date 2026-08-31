<script>
  // OpenPrintHQ - upload into the tenant's model library.
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // Shared by the Models grid and the Folders view. It was originally only in
  // Folders, which meant the default view of the Files tab offered no way to
  // add a file at all: the feature existed and was unreachable.
  import { uploadToLibrary, unzipToLibrary } from '$lib/library.js';

  let { folderPath = '', label = 'Upload', ondone = () => {} } = $props();

  let uploading = $state(null);   // { name, percent, index, total }
  let error = $state(null);
  let input;

  export async function send(list) {
    const chosen = Array.from(list || []);
    if (chosen.length === 0) return;
    error = null;
    let i = 0;
    for (const f of chosen) {
      i += 1;
      uploading = { name: f.name, percent: 0, index: i, total: chosen.length };
      try {
        const track = (percent) => { uploading = { name: f.name, percent, index: i, total: chosen.length }; };
        // A zip is unpacked rather than stored. Storing it would leave the
        // library indexing one opaque object the slicer cannot open.
        if (/\.zip$/i.test(f.name)) {
          await unzipToLibrary(f, folderPath, track);
        } else {
          await uploadToLibrary(f, folderPath, track);
        }
      } catch (e) {
        error = `Could not upload ${f.name}: ${e.message}`;
        uploading = null;
        return;
      }
    }
    uploading = null;
    // The scan is asynchronous on the library's side, so the caller reloading
    // straight away can still miss the new row. Reload anyway: the folder
    // listing reads the bucket rather than the index and is right immediately.
    ondone(chosen);
  }

  export function pick() { input?.click(); }
</script>

<button class="act" onclick={pick} disabled={!!uploading}>{label}</button>
<input
  class="hiddenInput"
  type="file"
  multiple
  bind:this={input}
  onchange={(e) => { send(e.currentTarget.files); e.currentTarget.value = ''; }}
/>

{#if uploading}
  <span class="progress">
    Uploading {uploading.name}
    {#if uploading.total > 1}({uploading.index} of {uploading.total}){/if}
    ... {uploading.percent}%
  </span>
{/if}
{#if error}<span class="warn">{error}</span>{/if}

<style>
  .hiddenInput { display: none; }
  .act {
    white-space: nowrap; font-size: 0.85rem;
    padding: 0.35rem 0.8rem; border: 1px solid var(--ophq-border); border-radius: 8px;
    background: var(--ophq-surface); color: var(--ophq-text); cursor: pointer; font-family: inherit;
  }
  .act:disabled { opacity: 0.5; cursor: default; }
  .progress { color: var(--ophq-text-2); font-size: 0.82rem; }
  .warn { color: var(--ophq-warn); font-size: 0.82rem; }
</style>
