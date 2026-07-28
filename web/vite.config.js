import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  // Deployed commit SHAs, injected at build time so the /legal AGPL §13 source
  // offer can pin the exact running version of the app + engine. Empty when not
  // provided (e.g. local dev builds), in which case /legal falls back to the repo.
  define: {
    __OPHQ_APP_COMMIT__: JSON.stringify(process.env.OPHQ_APP_COMMIT || ''),
    __OPHQ_ENGINE_COMMIT__: JSON.stringify(process.env.OPHQ_ENGINE_COMMIT || '')
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.CONTROL_PLANE_URL || 'http://localhost:8080',
        changeOrigin: true
      }
    }
  }
});
