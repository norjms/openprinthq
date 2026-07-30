// Programmatic authentication for e2e tests, bypassing Authentik via the
// control-plane dev-login endpoint (enabled only on dev/test with a shared
// secret). Runs once; the resulting session cookie is reused by all tests
// tagged with the 'authenticated' project.
import { test as setup, expect } from '@playwright/test';

const STORAGE = 'playwright/.auth/user.json';

setup('authenticate via dev-login', async ({ request, baseURL }) => {
  const email = process.env.TEST_USER_EMAIL || 'e2e@openprinthq.test';
  const secret = process.env.OPHQ_DEV_LOGIN_SECRET || '';
  const res = await request.post('/api/auth/dev-login', {
    data: { email },
    headers: secret ? { 'x-ophq-dev-login': secret } : {}
  });
  expect(res.ok(), `dev-login failed (${res.status()}): is OPHQ_ALLOW_DEV_LOGIN=1 and the secret correct on ${baseURL}?`).toBeTruthy();
  // Persist the session cookie for reuse.
  await request.storageState({ path: STORAGE });
});
