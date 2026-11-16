# Artisan Hub mobile API (v1)

One API for phone apps. The hub is the relay: the phone only ever talks to the hub, and the hub talks to
ArtisanHub Farm, Spoolman, InvenTree, WooCommerce, Manyfold, the DAM and the Klipper printers with the team's stored
credentials. The phone never holds those credentials.

- Base URL: `https://<hub>/api/mobile/v1`, for example `https://hub.example.com/api/mobile/v1`
- Interactive reference: `https://<hub>/api/docs` (tag "mobile")
- Push notifications: ntfy, see "Push" below

## Keys

Every call sends:

```
Authorization: Bearer ahk_<prefix>_<secret>
X-Hub-Instance: <the app install's id>     (when the key is tied to an instance)
```

Make keys in the hub (Settings, API keys) or with `POST /keys`:

```json
{"name": "Workshop iPhone", "scopes": ["read", "write"], "expires_in_days": 90,
 "instance_id": null, "bind_instance": true, "note": "Artisan app"}
```

- **Time**: `expires_in_days` (1 to 3650, or null for no expiry). Expired keys get 401 "this API key has expired".
- **Instance**: `instance_id` ties the key to one named install. With `bind_instance: true` and no id, the key
  binds to the first `X-Hub-Instance` it sees and refuses every other one (401 "this API key belongs to another
  app instance"). `POST /keys/{id}/unbind` frees it for a new phone.
- **Scopes**:
  - `read`, `write`, `admin`: the usual role sets.
  - Any single permission: `view`, `models`, `slice`, `inventory`, `store`, `delete`, `licenses`, `settings`,
    `connections`, `api_keys`, `invite`, `people`, `team`, `farm`.
  - `relay:<app>` or `relay:*`: raw access to a connected app. Also needs `connections`.
  - `owner`: the hub owner's provisioning endpoints. Only works on a key made by a platform admin.
- A key can never have more than the person who made it. Keys record their last IP and last use.

## Curated endpoints

| Area | Endpoints |
|---|---|
| Account | `GET /me`, `GET /overview` |
| App | `GET /look`, `GET /tiles` |
| Printers | `GET /printers`, `GET /printers/{name}`, `GET /printers/{name}/snapshot` (JPEG), `GET /printers/{name}/cover` (PNG), `POST /printers/{name}/command`, nozzles, filament paths, loaded (below) |
| Queue | `GET /queue`, `POST /queue/{id}/priority`, `POST /queue/{id}/cancel` |
| Plate checks | `GET /plate-checks`, `GET /plate-checks/{id}/photo`, `POST /plate-checks/{id}/pass`, `/fail`, `/skip` |
| Spools | `GET /spools`, `GET/PATCH/DELETE /spools/{id}`, `POST /spools`, `/use`, `/move`, `/assign`, `/unassign` (below) |
| Filaments | `GET /filaments`, `GET /filaments/lookup`, `POST /filaments/barcodes`, `POST /filaments/identify` |
| Locations, scanning, labels | `GET/POST /locations`, `PATCH/DELETE /locations/{id}`, `POST /locations/sync-printers`, `GET /scan/{code}`, `POST /labels`, `GET /labels/file`, `GET/POST /labels/printers` |
| Parts | `GET /parts`, `POST /parts/{id}/adjust` |
| Orders | `GET /orders`, `GET /orders/{id}`, `POST /orders/{id}/prioritise`, `POST /orders/{id}/status` |
| Products | `GET /products`, `GET/PATCH /products/{id}`, `POST /products/{id}/push`, `POST /products/{id}/live` |
| Store sources | `GET /sources/status`, `GET /sources/search`, `GET /sources/preview`, `POST /sources/import`, `GET /jobs/{id}` |
| Quotes | `GET /quotes`, `POST /quotes/{id}/approve`, `POST /quotes/{id}/refuse` |
| Maintenance | `GET /maintenance`, `POST /maintenance/tasks/{id}/done` |
| Alarms | `GET /alarms`, `GET /alarms/summary`, `POST /alarms/{id}/ack`, `/dismiss`, `/suppress`, `POST /printers/{name}/alarms/ack`, `GET /alarm-suppressions`, `DELETE /alarm-suppressions/{id}` (below) |
| Activity | `GET /events?since_id=` (poll for new hub events) |
| Push | `GET /push`, `POST /push/test` |
| Keys | `GET /keys`, `POST /keys`, `DELETE /keys/{id}`, `POST /keys/{id}/unbind` |

Printer commands: `set_ready`, `clear_ready`, `pause`, `resume`, `cancel`, `stop`, `home`, `light_on`,
`light_off`, `set_temperature`, `preheat`, `cooldown` (what each farm maps: see Mobile control, Printers).

Each printer in `GET /printers` and `GET /printers/{name}` may carry `commands`, the ones its connection
supports. OpenPrintHQ engines from 2026-11-07 send it from the engine's `controls`: a PrusaLink printer (Prusa
MK4, MK4S, MINI, XL) has `set_ready`, `clear_ready`, `pause`, `resume`, `cancel` and `stop` only, because PrusaLink
cannot home, set temperatures or run G-code over the network; the chamber light is offered on Bambu and Klipper
printers only. A printer without `commands` (older engines) takes every command. Hide the buttons a
printer does not list; a command it does not list answers 502 with "<printer> cannot <command> over the network".

These run the same hub logic as the web app, so permissions, staggered start, reprints and the event log behave
the same way.

## Which farm

The print farm is ArtisanHub Farm (OpenPrintHQ), connected under Settings, Connected services. `GET /me` answers
`farm`: `{"kind": "openprinthq", "label", "capabilities": [...], "connected"}`. Show a feature only when its
capability is listed (`commands`, `snapshot`, `priority`, `maintenance`, ...), since an older engine may not have
every one yet. A call the farm cannot do answers 502 with "not supported by this farm".

The farm lists `out_of_service` and `maintenance_log` only when its engine has them. The driver asks the engine once and keeps the answer for ten minutes, so the app
sees them appear within that time of an engine upgrade. Priority:
25 low, 50 normal, 75 high, 100 urgent; on an older OpenPrintHQ engine only 75 and up has an effect (front of the
queue).

## Build plates and plate reslicing (OpenPrintHQ)

The hub keeps the team's plate catalog and the plate on each printer, and turns a plate hold into a print.

| Endpoint | Permission | What it does |
|---|---|---|
| `GET /plates` | view | The catalog (seeded on first use: Darkmoon CFX, Bambu SuperTack, Bambu Textured PEI) and the slicer plate types |
| `POST /plates`, `PATCH /plates/{id}`, `DELETE /plates/{id}` | slice | Edit it: `name`, `slicer_type`, `bambu_marker`, `materials` (filament families), `notes` |
| `GET /printer-profiles/plates` | view | Each printer's usual plate (`bed_type`) and installed plate |
| `PUT /printer-profiles/{id}/plate` `{"plate_id"}` | farm | Swap a plate: writes the engine's `installed_plate`, turns Bambu plate marker detection off for a plate without Bambu's marker (on for one with it), and logs it as maintenance |
| `GET /plate-slices?state=review` | view | Plate reslices waiting for a look |
| `POST /plate-slices/{id}/approve` | slice | Approve one; the waiting job is swapped on the next scan |

How a plate hold is handled (`services/plate_reslice.py`, every minute, task `farm.plate_scan`):

1. A pending farm job whose only reason on some idle printer is the plate (engine preflight `plate_mismatch`).
2. The plate on that printer must suit every filament family the job uses. A plate with no filament list suits
   nothing, so fill it in first.
3. The model is resliced from its source for that plate (task `farm.plate_reslice`), or the cached plate slice
   for (model, printer, plate) is reused while its inputs are unchanged. Plate slices never replace the
   production files or change the bill of materials.
4. No problems from the slicer: it is approved and the engine's queue item is pointed at the new file and pinned
   to that printer (same id, position, priority). Problems: it waits in review.
5. Just before the swap the printer is checked again; if it was taken, the slice stays cached and the job waits.

The farm job's `plate_reslice` holds the state: `slicing`, `swapped`, `review`, `waiting_printer`, `unsuitable`,
`no_source` or `failed`, with a message.

## Plates, service, Print now and orders (app parity, 2026-10-25)

The same features as the web, over the same services, so the app and the web never disagree.

| Endpoint | Permission | What it does |
|---|---|---|
| `GET /printers/{name}/plate` | view | Fitted plate (with `slices_as` for this printer), usual plate, whether it is a Bambu, and the catalog |
| `PUT /printers/{name}/plate` `{"plate_id"}` | farm | Record a swap (null clears it): engine plate check, Bambu marker detection, maintenance log |
| `POST /printers/{name}/service` `{"down", "reason"}` | farm | Out of service (no new jobs) or back in service |
| `GET /products/{id}/print-options` | view | Printers with an approved slice (time, grams), `can_print_now`, suggested order `price` |
| `POST /products/{id}/print` `{"profile_id", "quantity"}` | farm | Print now: the approved slice pinned to that printer |
| `POST /products/{id}/order` | store | Create order (same body as the web's `POST /api/models/{id}/order`): pending WooCommerce order, `payment_url`, invoice when `send_invoice`, the farm now when `print_now` |
| `POST /orders/{id}/invoice` | store | Email Woo's invoice (with the Pay link) again |

Printer names match the farm's without case (`voron01` and `Voron01`). The web uses the spool routes above
(`/spools`, `/spools/{id}/assign`, `/printers/{name}/loaded`) for the Farm page's Filament tab, and
`/printers/{name}/nozzles` for the tool display (a `rack` means a tool switcher).

## For the phone app

### `GET /look`

The look to draw with, read from the hub's shared stylesheet (`/theme/apps.css`), so it always matches the web apps.

- The key owner's skin and light/dark choice (`mode` is `system`, `light` or `dark`; `system` means follow the phone).
- `colors.light` and `colors.dark`: every plain colour token (`bg`, `panel`, `panel_2`, `line`, `text`, `muted`,
  `accent`, `accent_hover`, `accent_text`, `ok`, `warn`, `bad`, `info`, and the rest).
- `fonts.body` and `fonts.mono`: family name and its font files (weight, style, `url` of the web font and `ttf`
  of a TrueType copy, which is what phones load). URLs are paths on the hub;
  join them with the hub address. Body fonts include a Light (300) weight for Metro headings.
- `wordmark`: path of the instance's wordmark SVG. `web_radius_px`: the web skin's corner radius (the app keeps
  square corners).
- `?skin=openprinthq` previews another skin; `?all=true` adds every skin under `all` for a picker.
- Sends an `ETag`; send `If-None-Match` to get `304` when nothing changed.

### `GET /tiles`

Everything the Start screen tiles show, in one call, cheap enough for background refresh:
`farm` (printing, idle, offline, and the printers printing now with progress and time left), `plate_checks`
(waiting count and the latest one with its photo path), `queue` (counts by status), `orders` (open and the next due),
`filament` (spools under `?spool_low_g=`, default 150 g, and the three lowest), `parts` (below minimum), `quotes`
(waiting), `store` (products live), `maintenance` (open, due, printers down) and `activity` (latest event).
A part that fails (for example the farm down) is left out and named in `errors`; the rest still answers.

### Connecting a phone by QR code

The usual way to connect a phone. In the hub, Settings, API keys, **Connect a phone**: choose what the phone can
do (View only, View and act, Everything I can do) and for how long (30 days, 90 days, 1 year, no expiry), then
**Show QR code**. The code works once, for 10 minutes; the page shows a countdown and then "connected".

The QR holds a plain link, `https://<hub>/pair?code=<one-time code>`, so any phone camera opens it (2026-10-22;
before that it held the `artisanhub://` link below, which only opened with the app installed). The app's scanner reads
it directly. Opened in a browser, the public **/pair** page (no hub sign-in) checks the code without using it and:

- Android: opens the app at once through an `intent://` link (`package=` the app id in `HUB_APP_ID`); without the app,
  Chrome goes to **/get-app?code=...**, which offers the newest APK from `HUB_ANDROID_APK_DIR` (or the Play listing
  in `HUB_ANDROID_STORE_URL`) and an "I've installed it, open Artisan Hub" button. App 0.2.1 and later also claim
  the link as a verified Android App Link (`/.well-known/assetlinks.json`, release key `HUB_ANDROID_CERT_SHA256`), so
  the camera opens the app without the browser.
- iPhone: an **Open in Artisan Hub** button with the app's link `artisanhub://pair?hub=<hub address>&code=<code>`
  (Safari opens other apps only from a tap). If nothing opens, the page points at the install part: the TestFlight
  or App Store link in `HUB_IOS_STORE_URL` when set, otherwise a note that the app is installed from a Mac for now.
  `HUB_IOS_APP_STORE_ID` adds Safari's Smart App Banner. `HUB_IOS_TEAM_ID` serves
  `/.well-known/apple-app-site-association` for Universal Links (needs the paid Apple Developer Program).

The app shows a confirmation screen; only after **Connect** does the hub make the key, already tied to that phone.
The key itself never appears on a screen. The panel's **Copy link** gives the same link for a phone that cannot scan.

| Call | Auth | What |
|---|---|---|
| `POST /pairings` `{"access": "act", "days": 90}` | api_keys permission | Start: returns `id`, `url`, `qr_svg`, `expires_at` |
| `GET /pairings/{id}` | api_keys | `waiting`, `connected` (with the phone's name), `expired` or `cancelled` |
| `DELETE /pairings/{id}` | api_keys | Cancel a code that has not been used |
| `GET /pair/{code}` | none (the code) | Preview for the confirmation screen: hub, team, who made it, what the phone can do, how long, the hub's accent colours. Uses nothing up |
| `POST /pair/{code}/claim` `{"device": "iPhone"}` | none (the code) + `X-Hub-Instance` | Makes the key tied to that install and uses the code up; returns the key once |

- Only a hash of the code is stored. Two phones racing for one code: the first wins, the other gets 410.
- A pairing never grants more than its maker holds; "Everything" leaves out the owner scope, raw relay and the
  permission to make keys.
- Unauthenticated pairing calls are limited to 30 a minute per address.
- Errors the app shows as they come: 404 (not from this hub), 410 (used, cancelled or expired), 429 (too many tries).

### Keys for a phone

Settings, API keys, **Phone key (paste)** still makes a read and write key that lasts 90 days and ties itself to the first
phone that uses it. It is shown once with a Copy button and the hub address to paste into the app. The key list
shows each key's expiry and whether it is tied to a device. A new phone: `POST /keys/{id}/unbind`.

## Owner endpoints (scope `owner`)

`GET /owner/teams`, `POST /owner/teams`, `POST /owner/people`, `POST /owner/teams/{id}/keys`,
`GET /owner/teams/{id}/members`. Used to set up a new team, invite its people and hand it a first key.

## Raw relay

```
GET|POST|PUT|PATCH|DELETE /relay/{app}/{path}
```

| app | `{path}` is relative to |
|---|---|
| `openprinthq` | OpenPrintHQ engine `/api/v1/`, through its control plane with the team's API key |
| `spoolman` | Spoolman `/api/` (so `v1/spool`) |
| `inventree` | InvenTree `/api/` (so `part/`) |
| `woo` | WordPress `/wp-json/` (so `wc/v3/orders`) |
| `manyfold` | Manyfold `/` (OAuth token added by the hub) |
| `moonraker` | `/{printer}/...` on that Klipper printer's Moonraker |
| `dam` | `POST /relay/dam` with `{"function": "...", "params": [...]}` |

- The phone's Authorization, cookies, Host and forwarding headers are never passed on.
- Bodies are capped at 20 MB each way.
- Every change (anything but GET) is written to the hub's event log with who made it.
- Manyfold answers 409 until its first-run setup is finished.

## Mobile control (2026-10-23)

The phone runs spools, locations, labels, printer hardware and the store through these calls. Code:
`backend/app/routers/mobile_control.py`, services `spool_library.py`, `barcodes.py`, `scan.py`, `labels.py`.

### One spool library

When the farm is connected, it keeps the filament library: the engine tenant's Spoolman, reached through the
engine's `/spoolman/inventory/*` routes. Every spool read and write goes there and the hub's spool cache is filled
from it. Until the farm is connected the hub's own Spoolman connection stays the library; spools still get codes,
but locations with codes, slot assignments and moves by code need the farm (400 otherwise).
`GET /spools` answers `source: "farm" | "spoolman"`.

Moving the hub's Spoolman into the farm library is the job `spools.migrate_to_farm` (web: `POST
/api/actions/spools-migrate-to-farm`, settings permission; payload `dry_run`, `keep_spoolman`). It is idempotent: every
copied spool, filament and vendor is recorded in the hub table `farm_library_migration` (the engine API cannot write
Spoolman extra fields, so `hub_migrated_from` is kept there, and each farm spool's note ends with `(hub Spoolman spool
#<old id>)`). Vendors and filaments come along with their spools; a filament with no spool cannot be made through
the engine and is listed in `filaments_without_spools`. Product variant and materials-list filament ids are moved to
the farm's ids once. When every spool made it, the hub's `spoolman` connection is turned off (kept, not deleted).

### Spools and filaments [inventory; `GET /spools` needs view]

| Call | What |
|---|---|
| `GET /spools?q=&location=&material=` | `{"spools": [spool], "source"}`. `location` matches the name or the location code. |
| `GET /spools/{id}` | One spool. |
| `POST /spools` | `{"filament_id" \| "filament": {vendor, name, material, color_hex, color_name, weight_g, diameter_mm, spool_weight_g, price, barcode}, "quantity": 1..50, "remaining_g", "location_code", "lot", "note", "print_labels": {"format", "printer_id", "copies"}}` -> `{"spools", "filament", "labels": {"printed"} \| {"pdf_url"} \| {"file_url"} \| null}`. The filament (and vendor) is made when only `filament` is given; its `barcode` is learned. |
| `PATCH /spools/{id}` | `{remaining_weight, location, comment}` |
| `POST /spools/{id}/use` | `{"grams"}` |
| `DELETE /spools/{id}` | Archive (nothing is deleted). |
| `POST /spools/{id}/move` | `{"location_code": "L..."}` |
| `POST /spools/{id}/assign` | `{"target_code": "L..."}` (a slot's code) or `{"printer", "ams_id", "tray_id"}`, plus optional `extruder_index`. 409 `{"detail", "allowed": [..]}` when that extruder is not on the slot's path. Answers the spool plus `extruder_index`, `slot_location_id`. |
| `POST /spools/{id}/unassign` | Take it out of its slot (its location stays). |
| `GET /filaments?q=&material=&catalog=true` | Library filaments (`source` farm or spoolman), then SpoolmanDB catalog hits (`source: "catalog"`, `id: null`, `catalog_id`). |
| `GET /filaments/lookup?barcode=` | `{"barcode", "match", "source": "learned" \| "upc" \| "none", "candidates", "product": {"title", "brand", "weight_g", "material", "color_name", "images"} \| null}` |
| `POST /filaments/barcodes` | `{"barcode", "filament_id"}`: learn. |
| `POST /filaments/identify` | multipart `photo` (JPEG), `text` (OCR) and `hints` (JSON), or JSON `{"text", "hints"}` -> `{"guess": {vendor, name, material, color_name, color_hex, weight_g, diameter_mm, barcode}, "candidates", "engine": "ollama:<model>" \| "rules", "note"?}` |

A spool: `{id, code: "S<id>", filament_id, vendor, name, material, subtype, color_hex, color_name, remaining_g,
label_weight_g, location, location_id, location_code, assigned: {printer, printer_id, ams_id, tray_id,
extruder_index, slot_label} | null, barcode, archived, note, slicer_preset, source}`. Slot labels follow the
engine: `AMS A slot 2`, `HT 1`, `External left` / `External right` (dual nozzle; ams 255 tray 0 is left, extruder 1),
`External` (one nozzle).

Barcode lookup order: the team's learned barcodes (`filament_barcode`), then UPCitemdb's free trial API (no key,
about 100 a day, 6 s timeout, cached for every team in `upc_lookup` for 30 days, misses too; when it is down or
over its limit the answer is just `source: "none"`). The product title is read by rules (brand, material such as
PLA, PLA+, PETG, PETG-CF, ABS, ASA, TPU, PA, PC, weight like 1kg / 1000g / 750 g, diameter 1.75 / 2.85) and the
candidates are ranked against the library and SpoolmanDB.

Identify asks the hub's vision model (Ollama `/api/chat`, JSON format, strict prompt) with the photo and the OCR
text, then fills gaps from the rules parser; when the model is not set up, down or answers nonsense the rules parser
answers alone (`engine: "rules"`, `note` says why). It never fails because of the model. Settings:
`GET/PUT /settings/identify` `{vision_url, vision_model}` [settings] (team settings; defaults from `HUB_VISION_URL`,
`HUB_VISION_MODEL`, `HUB_VISION_TIMEOUT`).

### Locations and scanning [inventory]

| Call | What |
|---|---|
| `GET /locations?kind=&flat=` | The farm's locations as a tree (`children`); flat with `flat=true` or `kind`. Each: `id, name, kind, parent_id, printer_id, ams_id, tray_id, code, note, spool_count, spools` (spool summaries: `id, code, vendor, name, material, color_hex, remaining_g, printer, slot_label`). |
| `POST /locations` | `{name, kind (storage, drybox, printer, ams, ams_slot, external, other), parent_id, printer_id, ams_id, tray_id, code?, note}`; the code is made when left out. |
| `PATCH /locations/{id}`, `DELETE /locations/{id}` | Only fields sent change (`{"parent_id": null}` clears it). |
| `POST /locations/sync-printers` | One location per printer, AMS unit, slot and external holder (idempotent). The hub also runs it itself (job `farm.sync_locations`, checked every 5 minutes): when a printer has come online since the last check (its AMS is only known while it reports), and at least every 30 minutes. |
| `GET /scan/{code}` | What a code is (below). |

`GET /scan/{code}` takes `S12`, `L7K2QX`, `P5`, `O9` (any case) or a whole label link (`https://<hub>/l/S12`), and
any UPC/EAN. It answers `{"kind": "spool" | "location" | "printer" | "ams" | "ams_slot" | "external" | "product" |
"order" | "barcode" | "unknown", "code", "title", "subtitle", "actions": [...]}` plus `spool`, `location` (with its
`spools`), `printer`, `slot: {printer, printer_id, ams_id, tray_id, label, extruders, fixed, loaded: spool | null,
live}`, `product`, `order` or `lookup` (barcodes); `location.spools` are spool summaries as in `GET /locations`, and a
slot of an offline printer is still named (`AMS C slot 4`) and shows what is installed. Actions: spool `details, use, move, assign, unassign, label,
archive`; slot `assign, unassign, details, label`; storage `details, move_here, label`; product `details, live, push`;
order `details, status`; barcode `new_spool, learn`. A code that is not found answers `kind: "unknown"`.

### Labels [inventory; printer setup: settings]

| Call | What |
|---|---|
| `GET /labels/printers` | `{"printers": [{id, name, kind: brother_ql \| zpl \| pdf, model, tape, dpi}], "formats"}`; `pdf` (id null) is the built-in file output. |
| `POST /labels/printers` | `{id? (to change one), name, kind, host, port (9100), model, tape, dpi}` [settings] |
| `DELETE /labels/printers/{id}`, `POST /labels/printers/{id}/test` | [settings]; the test only opens a TCP connection. |
| `POST /labels` | `{"targets": [{"kind": "spool" \| "location", "id"}], "format", "printer_id", "copies"}` -> PDF, PNG, ZIP (several PNGs) or `{"printed": n}`. Location ids may be the numeric id or the L code. |
| `GET /labels/file?targets=spool:12,location:L7K2QX&format=&copies=` | The same file as a link (what `POST /spools` answers in `pdf_url`). |

Formats: `pdf_avery5160` (Letter sheet of 30, Avery 5160 / L7160 layout), `pdf_62x29`, `pdf_62x100` (one label per
page), `png_62x29`, `png_40x30` (300 dpi, for a Bluetooth label maker), `printer`. Every label has a big QR code
with `https://<hub public URL>/l/<code>` (the hub's `HUB_PUBLIC_URL`, the same base the pairing links use), the
name, what it is (and the printer for slots), and for spools vendor, material, weight, a colour swatch and the code.

Label printers are the team's `labelprinter` connection (web: Connected services, Label printers, a JSON list).
Brother QL: raster built by the `brother_ql` package, sent raw to TCP 9100 (tapes 62x29, 62, 62x100, 29x90, 29,
38x90). ZPL: the label as one `^GFA` graphic, sent raw to TCP 9100 (`tape` is WxH in mm, `dpi` 203, 300 or 600).

The public page `GET /l/<code>` (no key) shows what kind of thing a code is and its code, with Open in Artisan Hub
(`artisanhub://l/<code>?hub=...`, Android intent with a fallback to `/get-app`) and Get the app. Only someone signed in
to the hub on the web sees the name. iOS universal links list `/l/*` next to `/pair`.

### Printers [view to read, farm to change]

`GET /printers` items carry `id`, `nozzle_count`, `job_name` and `cover_available` (true while an OpenPrintHQ
printer is printing; nothing is fetched for it). A printer the engine reports as not connected has `state:
"offline"`, `online: false`, `busy: false`.

`GET /printers/{name}` also answers `job_name`, `eta` (ISO time), `cover_available`, and with OpenPrintHQ
`nozzles`, `paths` and `loaded` (the engine's `GET /printers/{id}/nozzles`, `/filament-paths`, `/loaded`).

| Call | What |
|---|---|
| `GET /printers/{name}/cover?view=top\|side` | PNG of the current job's sliced plate (the engine's cover, with a camera stream token the hub fetches); 404 when idle. |
| `GET /printers/{name}/nozzles`, `PUT /printers/{name}/nozzles/{extruder_index}` `{diameter_mm, nozzle_type, volume_type}`, `DELETE ...` | Manual nozzle override (422 on an unknown type, DELETE 404 without one). |
| `GET /printers/{name}/filament-paths`, `PUT ...` `{track_switch_mode?, slots?: [{ams_id, tray_id, extruders}]}` | `slots` replaces the hand-set paths (`[]` clears). |
| `GET /printers/{name}/loaded` | What each slot has loaded, with its extruder. |

`POST /printers/{name}/command` on OpenPrintHQ maps `set_ready`, `pause`, `resume`, `stop`/`cancel`, `light_on`,
`light_off` (chamber light), `home`, `set_temperature` (`args`: `nozzle`, `bed`, `chamber`, `extruder_index`),
`preheat` (`args`: `material`, or `nozzle` and `bed`) and `cooldown`. Others answer "not supported by this farm".

### Store [view to read, models to import and edit, store to push and change orders]

| Call | What |
|---|---|
| `GET /sources/status` | Which model sites are signed in. |
| `GET /sources/search?platform=printables\|makerworld\|thingiverse\|creality&q=&sellable=true&sort=&offset=&limit=` | Sellable licences only unless `sellable=false`. |
| `GET /sources/preview?url=` | |
| `POST /sources/import` | `{"url", "profile_id"?}` -> `{"job_id", "model_id" (set when already imported), "platform"}` |
| `GET /jobs/{id}` | `{id, kind, status: queued \| running \| done \| failed, result: {model_id, product_id, ...} \| null, error}` |
| `GET /media/{image_id}/thumb\|large\|original` | A product or model image with the phone's key (send the same headers as every call). |
| `GET /products/{id}` | `images: [{id, thumb, large, role, public}]`: `thumb` and `large` are `/api/mobile/v1/media/...` links (key needed), `public` is a signed JPEG link that needs no headers (for image loaders that can't send them). Also title, descriptions, price, sale price, SKU, category, images, variants, BOM summary, licence (`problems` = why it can't be sold), slice status, live, store URL. |
| `PATCH /products/{id}` | `{title, short_description, description, price, sale_price, categories}`; a model has one category: the first name or id is used (a new name is made). |
| `POST /products/{id}/push` | `{"job_id"}`; 400 with the licence problems when it can't be sold. |
| `POST /products/{id}/live` | (as before) |
| `POST /orders/{id}/status` | `{"status": "processing" \| "completed" \| "on-hold"}` [store]: set in WooCommerce, then here. |

Quotes (`GET /quotes`) carry `customer_name` and `customer_email` from the store order when there is one.

## Push

The hub publishes alerts to its own ntfy server. On the phone install the ntfy app, then:

- Server: your ntfy server, for example `https://ntfy.example.com`
- Topic: the topic set in the hub's alerts connection
- Sign in with a ntfy user that can read the topic. A good setup gives the phone user read access only and
  gives anonymous users no access.

Which events alert is set in the hub under Alerts. `POST /push/test` sends a test message.

## Errors

JSON `{"detail": "..."}` with 400 (bad input), 401 (key missing, expired, wrong instance), 403 (scope or
permission), 404, 409 (a conflict; slot assignment adds `allowed`), 413 (too large), 422 (validation), 502 (the farm,
engine or other app did not answer or refused; the detail carries its message).

## Alarms (OpenPrintHQ, capability `alarms`)

What every printer is reporting right now, from the engine's alarm feed: Bambu HMS codes (fatal, serious,
common), Klipper shutdowns and print errors, PrusaLink ATTENTION and ERROR, and the error state of OctoPrint and
other printers. The capability appears once the engine has the feed (probed with `GET /alarms/summary`, cached ten
minutes).

| Endpoint | Permission | What it does |
|---|---|---|
| `GET /alarms?state=active\|cleared\|all&printer=<name>&unacknowledged=true` | view | `{"alarms": [...]}`, active ones worst first (fatal, serious, warning), then newest |
| `GET /alarms/summary` | view | `active`, `unacknowledged`, `by_severity`, `printers` (engine ids) and `printer_names` |
| `POST /alarms/{id}/ack` | farm | Acknowledge one |
| `POST /printers/{name}/alarms/ack` | farm | Acknowledge every unacknowledged alarm on that printer |
| `POST /alarms/{id}/dismiss` | farm | Take it off the active list now (and mark it seen) |
| `POST /alarms/{id}/suppress` | farm | No more alerts for this code on this printer, and dismiss it; `{"ok", "suppression"}` |
| `GET /alarm-suppressions?printer=<name>` | view | `{"suppressions": [...]}`, newest first |
| `DELETE /alarm-suppressions/{id}` | farm | Alert about that code again |

Each alarm: `id`, `printer_id`, `printer_name`, `source` (bambu, klipper, prusalink, octoprint, ...), `code`,
`severity`, `message`, `first_seen`, `last_seen`, `cleared_at`, `active`, `acknowledged_at`, `acknowledged_by`,
and from engines with suppressions `full_code`, `hms_code` (as Bambu writes it, `HMS_0C00-0100-0002-0017`),
`dismissed_at`, `dismissed_by`. Each suppression: `id`, `printer_id`, `printer_name`, `source`, `code`, `message`,
`created_at`, `created_by`.
An alarm clears by itself 30 seconds after the printer stops reporting it; acknowledging only marks it seen.
Dismissing hides it until the printer clears it and reports it again. Suppressing stops the engine recording that
code on that printer, and stops its notifications, until the suppression is removed. Only alarms and suppressions
on the caller's own printers can be acted on (404 otherwise). The
web Farm page has the same feed on its Alarms tab (`/api/farm/alarms`, same parameters).

