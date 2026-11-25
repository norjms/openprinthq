import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
export default defineConfig({ root: new URL('.', import.meta.url).pathname, base: './', plugins: [svelte()], build: { outDir: 'dist', emptyOutDir: true } });
