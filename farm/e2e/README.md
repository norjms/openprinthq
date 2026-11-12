# OpenPrintHQ: E2E regression suite (Playwright)

End-to-end regression tests that hit a **deployed** instance through the real
reverse proxy + Authentik forward-auth, so they catch proxy/auth/routing
regressions as well as UI ones.

## Run

```sh
cd e2e
npm ci                       # installs @playwright/test (pinned)
BASE_URL=https://internal.example.com npm test
```

- `BASE_URL` selects the tier (defaults to dev). Point it at test/prod to smoke those.
- `PW_CHROMIUM=/path/to/chrome` reuses a preinstalled Chromium (cloud/CI); omit to use Playwright's bundled browser (`npx playwright install chromium` once).

## Coverage (v1: unauthenticated surface)

- **landing**: loads with the glass theme from artisanhub-ui, no theme picker.
- **responsive**: no horizontal overflow + header actions on-screen at 390px (regression: mobile banner).
- **auth-gating**: `/app` redirects to Authentik; `/`, `/signup`, `/legal` are public.
- **signin-links**: footer/signup/header sign-in links carry `data-sveltekit-reload`, and clicking signup's "Sign in" routes through Authentik (regression: client-side nav landed users on an unauthenticated `/app`).
- **signup**: page loads; invite field presence matches `/api/pub/signup-info`.
- **api**: `/api/pub/signup-info` returns config JSON; gated `/api/me` is never 200 without auth.

## Screen checks (no deployed instance)

`playwright.screens.config.js` runs `screens/screens.spec.js` against `screens/server.mjs`, which serves a
built web app and answers its API calls from `screens/fixtures.mjs`. Every page is rendered at 1920 x 1080 and
1280 x 860 (the public pages also at 390 x 844) and must fit the window: the page itself never scrolls and
nothing is hidden (`screens/check.mjs`).

```sh
(cd ../web && npm ci && npm run build)
npm ci
SHOTS_DIR=/tmp/ophq-screens npx playwright test -c playwright.screens.config.js
```

## TODO (authenticated flows)

App-shell mobile menu, first-login signup end-to-end, owner console: need a test
auth strategy (dedicated Authentik test user + programmatic login, or a scoped
test bypass). Tracked for v2.
