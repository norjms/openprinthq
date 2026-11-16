# Alerts

Every alert the hub raises is kept in an inbox (`services/alert_inbox.py`, table `alert`), whichever channels it went
to. A notification, an email or the bell opens the alert at `/alerts/{id}`: what happened, **Fix it** and
**Dismiss**. Repeats of the same problem update one alert (`ref`). An alert closes itself when its problem goes away
(the job stops waiting for filament, the printer becomes ready); updates that need nothing close after a day, and
anything left open after two weeks.

## Guided fixes

| Alert | Ways to fix it |
|---|---|
| Filament doesn't match (`waiting_filament`) | Use what is loaded (the mapping page, reslicing for another material); load the right filament and check again; the hub has it wrong (tell a Bambu printer what is in a slot); use a different file (the model); run it on another printer; order filament (onto the shopping list); cancel the job |
| Jobs waiting on a printer that isn't ready (`waiting_ready`) | The plate is clear (pass the plate check, or set ready); something is wrong with the last print (fail the check, reprint); use another printer (move the waiting jobs) |

Other alerts' **Fix it** opens the page where they are dealt with. Each fix marks the alert fixed.

## What reaches each person

`/alerts?tab=choices` (and the app's Settings): each person turns kinds on or off for their own phone and email,
in four groups (problems, needs you, print updates, orders). The inbox still lists everything. Which channels each
kind can use at all is the team setting under Farm, Settings, Alerts.

## API

The same routes under `/api` and `/api/mobile/v1`: `GET /alerts?state=open|closed|all`, `GET /alerts/counts`,
`GET /alerts/{id}` (with `context` for its fix), `POST /alerts/{id}/dismiss`, `POST /alerts/{id}/fixed`,
`GET/PUT /alerts/choices`, `POST /alerts/{id}/want-filament`, `GET /alerts/filament-wanted`,
`DELETE /alerts/filament-wanted/{index}`, `GET /farm/jobs/{id}/why`, `POST /farm/jobs/{id}/move`,
`POST /farm/printers/{name}/slots/{tray}/filament`. Push notifications carry the alert's id (`a`).
