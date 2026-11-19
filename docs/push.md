# Phone app notifications

The OpenPrintHQ app on iPhone, iPad and Android gets every hub alert as a notification: problems (alarms, failed
prints, filament that needs mapping), print updates (started, paused, finished) and order updates (new, shipped,
cancelled). Which alerts go to the app is set per event under Farm, Settings, Alerts, in the `push` column; every
alert is on by default.

## How it works

Apple and Google take push messages only from whoever holds the app's keys, so the hub hands each alert to a push
relay ([artisanhub-push-relay](https://github.com/norjms/artisanhub-push-relay)) that has them.

1. When a person turns notifications on in the app, the phone makes an X25519 key pair. The private key stays in
   the phone's secure storage. The phone sends the hub its public key and the push token Apple or Google gave it
   (`POST /api/mobile/v1/push/devices`).
2. For each alert the hub seals the title, text and link for that phone: a one-time X25519 key, HKDF-SHA256 and
   ChaCha20-Poly1305 (`services/push.py`, the same envelope as the relay's `reference/envelope.py`, checked against
   its test vectors in `backend/tests/push_vectors.json`).
3. The relay passes the sealed bytes to Apple or Google. It has no key and keeps nothing.
4. The app opens the message on the phone. On iPhone a notification service extension does it before the
   notification shows; until then it reads "New update from your hub".

The hub keeps each phone's public key and push token (`push_device`) and forgets them when the person turns
notifications off, removes the phone, or the push service says the token is gone.

## Choices

| Who | Where | Choices |
|---|---|---|
| Each person | the app, or Farm, Settings, Phone app notifications | Phone notifications; email instead (alerts by email, filament mapping on the hub's web pages); neither. Nobody gets phone notifications until they turn them on. |
| The installation (settings permission) | Farm, Settings, Phone app notifications | Use the relay, or off (people who turned notifications on get email instead). The relay address. |

The relay address is the team setting `push_relay_url`, else `HUB_PUSH_RELAY_URL`. Any relay running
artisanhub-push-relay with keys for the app build works, so an installation can run its own.

The text a person reads before turning notifications on comes from the hub (`GET /api/push/state`, `disclaimer`),
so the app and the web say the same thing. It reads the relay's own `GET /status`: when the relay's delivery log
is on (testing), the text says so.

## API

The same routes under `/api/push` (web) and `/api/mobile/v1/push` (apps). A key must belong to a person.

| | |
|---|---|
| `GET /state` | the installation's mode and relay, your choice, your phones, `this_device` (the phone the key belongs to), `mapping_in_app`, the disclaimer |
| `POST /devices` | `{"platform": "apns"\|"fcm", "token", "public_key", "apns_env", "apns_topic", "label", "app_version"}`: registers this phone and turns your notifications on. 409 when the installation has them off |
| `DELETE /devices/{id}` | removes one of your phones |
| `PUT /choice` | `{"choice": "on"\|"off"\|null}`; off and null forget your phones |
| `POST /devices/test` | a test notification to your phones |
| `PUT /settings` | settings permission: `{"mode": "on"\|"off", "relay_url"}` |
