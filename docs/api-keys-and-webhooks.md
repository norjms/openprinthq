# API keys and webhooks for integrations

For scripts and other apps (Artisan Hub, n8n, the phone app). People in a browser keep signing in through
Authentik; nothing about that changes.

## Keys

Make one while signed in: `POST /api/api-keys` with `{"label": "Artisan Hub", "scope": "submit", "expiresInDays": 365}`
(`expiresInDays` 0 or left out means no expiry). The answer holds the key, `ophqk_...`, once. `GET /api/api-keys`
lists keys (never the secret); `DELETE /api/api-keys/{id}` revokes one. Keys cannot list, make or revoke keys.

Send it as `Authorization: Bearer ophqk_...` (or `X-API-Key`). It works on `/api/me` and on the engine API under
`/api/engine/...`, the same routes the web app uses.

| Scope | What it can do |
|---|---|
| `read` | Every `GET`: printers and their status, the queue, files, spools, history |
| `submit` | `read`, plus getting work in: upload a library file, add to the queue, print or slice a library file, upload or reprint an archive, MakerWorld import |
| `control` | `submit`, plus every other change a person can make: printer commands, queue edits, settings |

Never with a key, whatever the scope: sign-in and tokens, users, groups, MFA, the engine's own API keys, cloud
logins, backup and restore, support bundles. Those answer 403 and need a signed-in person.

Writes made with a key are logged with the key id.

### The proxy in front

the reverse proxy runs Authentik forward-auth before requests reach the router, so a request that only carries a key is
turned away there. Let these through to the router without forward-auth, and the control plane checks the key:

- requests to `/api/` whose `Authorization` header starts with `Bearer ophqk_`, or that carry an `X-API-Key`
  header starting with `ophqk_`.

The bypass rule must still remove any `X-authentik-*` headers the client sent, exactly as forward-auth does, and
`OPHQ_GATEWAY_SECRET` should be set so the control plane only believes identity headers from the gateway. A request
that carries a key is judged by the key alone (the control plane ignores identity headers next to it on the routes
that take keys), but every other route still reads those headers, so a bypassed request must never be able to
supply them.

## Webhooks

The engine already sends events through its notification providers. Add a provider of type `webhook` (Settings,
Notifications, or `POST /api/engine/api/v1/notifications/` with a `control` key) with:

- `webhook_url`: where to send, for example Artisan Hub's `/hooks/openprinthq`;
- `signing_secret`: a long random string shared with the receiver;
- the events to send (print start and complete, failures, queue job added, started, failed, printer offline or
  error, plate not empty, filament low, maintenance due, and the rest the provider offers).

Each delivery is a JSON POST with `event`, `title`, `message`, `timestamp`, `source` ("OpenPrintHQ") and the
event's fields. Headers: `X-OPHQ-Event` (the event name) and, with a secret, `X-OPHQ-Signature: sha256=<hex>`,
the HMAC-SHA256 of the exact body with the secret. Check it against the raw body before parsing.
