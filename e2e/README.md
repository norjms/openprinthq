# OpenPrintHQ — E2E regression suite (Playwright)

End-to-end regression tests that hit a **deployed** instance through the real
npmplus reverse proxy + Authentik forward-auth, so they catch proxy/auth/routing
regressions as well as UI ones.

## Run

```sh
cd e2e
npm ci                       # installs @playwright/test (pinned)
BASE_URL=https://internal.example.com npm test
```

- `BASE_URL` selects the tier (defaults to dev). Point it at test/prod to smoke those.
- `PW_CHROMIUM=/path/to/chrome` reuses a preinstalled Chromium (cloud/CI); omit to use Playwright's bundled browser (`npx playwright install chromium` once).

## Coverage (v1 — unauthenticated surface)

- **landing** — loads, default theme is light, theme switcher present.
- **responsive** — no horizontal overflow + header actions on-screen at 390px (regression: mobile banner).
- **auth-gating** — `/app` redirects to Authentik; `/`, `/signup`, `/legal` are public.
- **signin-links** — footer/signup/header sign-in links carry `data-sveltekit-reload`, and clicking signup's "Sign in" routes through Authentik (regression: client-side nav landed users on an unauthenticated `/app`).
- **signup** — page loads; invite field presence matches `/api/pub/signup-info`.
- **api** — `/api/pub/signup-info` returns config JSON; gated `/api/me` is never 200 without auth.

## TODO (authenticated flows)

App-shell mobile menu, first-login signup end-to-end, owner console — need a test
auth strategy (dedicated Authentik test user + programmatic login, or a scoped
test bypass). Tracked for v2.
