# First-run setup API, `/api/setup/v1`

A small, versioned API for setting up a new hub from outside. The hub's own `/setup` page uses it, and so
does the OpenPrintHQ installer. Everything after first setup goes through the normal API with the key this
returns.

**Stability:** within `v1`, fields are only added, never removed or renamed, and the meaning of existing
fields does not change. A breaking change gets a new version prefix.

## The setup token

Setup is authenticated by a one-time token:

- If the hub is started with `HUB_SETUP_TOKEN`, that is the token. This is what an installer uses.
- Otherwise the hub generates one at its first start and writes it to its log once:
  `First-run setup: open <public url>/setup and enter this setup token: ...`. If that log line is lost,
  restart the hub with `HUB_SETUP_TOKEN` set to a token of your own.

Once setup is complete the token stops working. At most 10 setup attempts a minute are accepted from one
address.

## `GET /api/setup/v1/status`

No authentication.

```json
{"api_version": 1, "setup_complete": false, "farm": null}
```

## `POST /api/setup/v1/initialize`

`Authorization: Bearer <setup token>`

```json
{"farm": "openprinthq", "team_name": "My workshop", "key_name": "OpenPrintHQ installer"}
```

- `farm`: `openprinthq` (the farm), the only print farm the hub supports; it may be left out. Any other
  value answers 422. See `docs/farm.md`.
- `team_name`: the first workspace's name.
- `key_name`: optional; the name the API key shows under in the hub's settings.

Answer, with the API key shown once:

```json
{"team": {"id": 1, "name": "My workshop", "slug": "my-workshop"}, "farm": "openprinthq",
 "api_key": "ahk_...", "note": "Shown once. Use as: Authorization: Bearer <key>"}
```

The key has the `read`, `write` and `admin` scopes for the first workspace, which covers connecting outside
services (`PUT /api/connections/{kind}`, `POST /api/connections/{kind}/test`). Revoke it in the hub's
settings when it is no longer needed.

| Status | Meaning |
|---|---|
| 401 | Missing or wrong setup token |
| 409 | Setup is already complete |
| 422 | A field is missing or invalid, such as an unknown farm |
| 429 | Too many attempts from this address; wait a minute |
| 503 | The hub is still starting |

## `POST /api/setup/v1/password-link`

For a hub whose logins are in LLDAP (`HUB_LLDAP_URL`). Body `{"email": "..."}`; answers `{"link": ..., "expires_in_minutes": 30, "user": ...}`: a one-time link to the hub's page where the person with that login chooses its password. Only the API key that `initialize` minted may ask (403 otherwise), since the link sets the password of any login; 404 when no login has that email, 409 without LLDAP. The installer uses it for the owner's login, which it makes but whose password it never sees.

## `POST /api/setup/v1/updates`

The installer says a part of the installation has a new version waiting on its update page (the OrcaSlicer
desktop, say). Only the key made at setup may call it. Body: `part` (lowercase name), `version`, `title`,
`message`, `ready` (default true). Ready opens one alert of kind `update_ready` per part (a newer version
updates it), sent to the phone app only, whose guided fix opens the installer at `/manage` or puts it away;
`ready: false`, sent once the update is installed, closes it.

## Reachability checks

Whether the hub can be reached through its public address, checked from wherever it matters (outside the
network, or through a reverse proxy). Each token is issued by this hub, so an answer proves the request
reached this hub and not something else at that address. Tokens last 30 minutes and only their hash is kept.

- `POST /api/setup/v1/reach`, with an API key that may connect services: returns
  `{"token": "...", "path": "/api/setup/v1/reach/<token>", "expires_at": "..."}`.
- `GET /api/setup/v1/reach/<token>`, no key, through the address being checked: `200` with
  `{"ok": true, "message": "..."}` for a live token, `404` otherwise. Limited to 10 a minute per address.
- `GET /api/setup/v1/reach/<token>/status`, with the same kind of key: `{"seen": true or false,
  "seen_at": "...", "via_proxy": true or false}`. `via_proxy` is whether the request arrived with
  `X-Forwarded-*` headers, as it does through a reverse proxy.

A person checking by hand runs, from outside their network:

```sh
curl -fsS https://hub.example.org/api/setup/v1/reach/<token>
```

## Health

`GET /healthz` answers 200 when the database answers and every schema migration has run, and 503 otherwise:

```json
{"ok": true, "database": "ok", "migrations": "done", "schema_version": 20, "setup_complete": false}
```

## Installations from before this API

At their first start with this version, existing installations are migrated once. An installation where
people have already signed in counts as set up, so the setup API refuses it. If every team with a farm
connected uses the same farm, that farm becomes the installation's explicit choice. If teams use different
farms, or none is connected, nothing is guessed: the hub keeps choosing per team as before and logs a warning.
