// OpenPrintHQ end-to-end regression suite (Playwright).
//
// Runs against a DEPLOYED instance through the real npmplus + Authentik path,
// so it catches proxy/auth regressions (not just component behaviour). The
// target is env-driven — same suite runs against any tier:
//   BASE_URL=https://internal.example.com   (default: dev)
//
// PW_CHROMIUM lets a host with a preinstalled Chromium (the cloud sandbox / CI
// image) skip the browser download; otherwise Playwright's bundled browser is
// used.
import { defineConfig } from '@playwright/test';

const baseURL = process.env.BASE_URL || 'https://internal.example.com';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    ignoreHTTPSErrors: true,
    trace: 'on-first-retry',
    browserName: 'chromium',
    viewport: { width: 1280, height: 800 },
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {}
  },
  projects: [{ name: 'chromium' }]
});
