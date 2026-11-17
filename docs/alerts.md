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

| A print finished (`print_done`) | The print is good and the plate is clear (pass the plate check); something is wrong with the print (fail it with a reason, reprint if wanted) |
| A print failed or stopped (`print_failed`) | Clear the plate and print it again; clear the plate without reprinting; look at the printer first |
| The camera saw a failure (`failure_detected`) | It looks fine (a paused print resumes); pause it while I look (the alert stays open); stop the print |
| Printer alarm (`alarm`) | I fixed it: resume the print; clear the alarm; stop alerts for this code on this printer; stop the print; open its maintenance task |
| Printer went offline mid-print (`printer_offline`) | Check again; take it out of service; send its waiting jobs to another printer. Closes by itself when the printer is back online |
| Maintenance due (`maintenance_due`) | Done (with notes; parts come out of stock); not needed this time; take the printer out of service while I do it (back in service when marked done) |
| Order couldn't go to the farm (`order_problem`) | Try again; open the order |
| Order may ship late (`order_late`) | Print it sooner (high or urgent priority); move the due date (1, 2, 3 or 7 days); open the farm timeline |
| Custom print to review (`quote_ordered`) | Approve it; refuse it (with a reason for the customer); look at it first |
| An update is ready (`update_ready`, from the installer) | Open the installer's update page (`/manage`, on a computer); not now |

Filament and ready have their own screens in the web dashboard and the app. The others are described by the hub
(`services/alert_fixes.py`): `GET /alerts/{id}` answers `context.kind = "guided"` with a summary, photos, the
printer whose camera to show, and the ways to fix it, each with its button, the values it asks for (a choice, a
text or a switch), whether it asks first, and why it can't be used right now. `POST /alerts/{id}/do/{way}` with
`{"values": {...}}` carries one out (farm permission) and answers `{message, closed, alert}`. A way that only opens
a page has `open` (`web` path, `app` place) instead.

Alerts close by themselves when the problem goes away elsewhere: a plate check done on the farm page, an alarm the
printer clears, a printer back online, a maintenance task done or dismissed, an order sent to the farm, a quote
approved or refused, a print that ends after the camera saw a failure.

Updates (print started, order shipped, ...) have no fix: **Fix it** opens the page where they are dealt with.

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
