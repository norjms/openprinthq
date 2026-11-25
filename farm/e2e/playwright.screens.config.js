// Screen checks against a stand-in backend: no deployed instance needed.
//   (cd web && BASE_PATH=/apps/farm npm run build) && cd e2e &&
//   BASE_PATH=/apps/farm npx playwright test -c playwright.screens.config.js
// Two stand-ins run: one where the hub does not answer (the built-in sidebar),
// and one, on the next port, where it does (HUB=1).
// SHOTS_DIR=<dir> keeps a screenshot of every page.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { defineConfig } from '@playwright/test';

const port = Number(process.env.SCREENS_PORT || 4173);
export default defineConfig({
  testDir: './screens',
  timeout: 45_000,
  fullyParallel: true,
  workers: process.env.CI ? 2 : undefined,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: 'chromium',
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM, args: ['--no-sandbox'] } : {}
  },
  webServer: [false, true].map((hub) => ({
    command: `node screens/server.mjs ${process.env.WEB_BUILD || '../web/build'} ${port + (hub ? 1 : 0)}`,
    env: { ...process.env, HUB: hub ? '1' : '' },
    url: `http://127.0.0.1:${port + (hub ? 1 : 0)}${process.env.BASE_PATH || ''}/legal`,
    reuseExistingServer: true,
    timeout: 30_000
  }))
});
