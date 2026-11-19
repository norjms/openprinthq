<script>
  // A page inside the hub's AppShell: the title row and the widget column come from here, the content is the
  // children. crumb overrides the last part of the header's breadcrumb (a model's name, for example).
  import { shell } from './shell.svelte.js';
  /** @type {{ title: string, description?: string, crumb?: string, actions?: import('svelte').Snippet, widgets?: import('svelte').Snippet, children?: import('svelte').Snippet }} */
  let { title, description = '', crumb = null, actions = null, widgets = null, children } = $props();
  $effect.pre(() => {
    shell.title = title;
    shell.description = description;
    shell.crumb = crumb;
    shell.actions = actions;
    shell.widgets = widgets;
  });
</script>

<svelte:head><title>{title} · OpenPrintHQ</title></svelte:head>
{@render children?.()}
