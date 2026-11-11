import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter(),
    // ArtisanHub Farm is served under the hub's name at /apps/farm (BASE_PATH=/apps/farm
    // at build time). Empty means the site root, as upstream OpenPrintHQ.
    paths: { base: process.env.BASE_PATH || '', relative: false },
    alias: {
      $lib: 'src/lib'
    }
  }
};

export default config;
