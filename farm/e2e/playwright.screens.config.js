// Screen checks against a stand-in backend: no deployed instance needed.
//   (cd web && npm run build) && cd e2e && npx playwright test -c playwright.screens.config.js
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
  webServer: {
    command: `node screens/server.mjs ${process.env.WEB_BUILD || '../web/build'} ${port}`,
    url: `http://127.0.0.1:${port}${process.env.BASE_PATH || ''}/login`,
    reuseExistingServer: true,
    timeout: 30_000
  }
});
