# Changelog

Notable changes to Artisan Hub. Versions follow the tags of this repository.

## Unreleased: first public release

- Desktop apps (`docs/apps.md`): the installer tells the hub which slicers and design apps it runs as desktops in a
  browser tab (`PUT /api/apps/v1`). Each is in its craft's sidebar group, and People gives each to anyone through its
  group in the user directory, as for OrcaSlicer.
- Your shop's logo (`docs/brand.md`): upload an SVG or PNG on Settings, Shop and crafts, and the phone app draws it
  in place of the OpenPrintHQ wordmark. Kept in the hub's data folder; unsafe SVGs are refused.
- Crafts (`docs/crafts.md`): 3D Printing is always on, and Laser Crafting, Sublimation and Graphic Art can be
  turned on for the installation, at first-run setup (`crafts` in `POST /api/setup/v1/initialize`), on Settings,
  Crafts, or with `PUT /api/crafts/v1`. A craft that is off is left out of the sidebar. Laser Crafting links to
  OpenPrintHQ Laser at `/apps/laser`, which the installer adds; Sublimation and Graphic Art have pages saying their
  tools are coming. Uses `openprinthq-ui` 0.3.0 for the new icons.
- First public release of Artisan Hub under the GNU AGPL-3.0.
- Shopify and Etsy beside WooCommerce (`docs/stores.md`): push products, one shared stock pool, orders in from
  webhooks and a regular check, and Mark shipped to fulfil an order in its store. Each product can be kept off a
  store. Etsy connects through Connect Etsy (OAuth) and takes its listing defaults on the Connections page.
- ArtisanHub Farm (OpenPrintHQ) is the only print farm. Support for other farm apps is removed, with the settings,
  relays, spool sync and store SKU mapping that only they used; orders reach the farm through the hub's own order
  routing. Database columns get farm-neutral names on upgrade. An installation set up with another farm refuses to
  start until it is moved (`docs/farm.md`, Moving from another farm; `HUB_FARM_MOVE`).
- Configuration reference in `docs/configuration.md`. Every setting defaults to a neutral value, so a new hub
  connects to nothing it was not told about.
- `compose.example.yml` for self-hosting with Docker Compose.
- First-run setup at `/setup` and through the setup API (`docs/setup-api.md`).
- The image is published to GitHub Container Registry for amd64 and arm64 by the maintainer.
- A new look shared by every ArtisanHub app (the `artisanhub-ui` package): glass over a dark page, Manrope and Sora,
  one sidebar grouped as Print, Library, Shop and Admin. The earlier skins and the light
  and dark modes are replaced by named themes, with Glass dark the only one for now, and a "Reduce transparency"
  choice. Saved skins move to Glass dark.
- People brings together Team and the owner's People and teams; Devices brings together the browser add-on,
  Connect a TV and the phone app; printer profiles are a tab of Slicing. The old addresses redirect.
- The sidebar is served as data at `/api/nav/v1` (`docs/nav-api.md`), so ArtisanHub Farm draws the same one.
- The sidebar has a Farm group after Print with the pages only the farm has (Files, Slicer, Projects, Statistics,
  Print reports, Timelapses, Farm settings), shown when the hub links to an installed ArtisanHub Farm.
- Forgot your password: with logins in LLDAP and email set up, `/account/password` emails a one-time link (30
  minutes, works once, a new one cancels older ones) to choose a new password. The answer is the same whether or not
  the login exists, and requests are limited per client and per login. The hub's owner can make such a link for
  anyone from People.
- OrcaSlicer in the browser: on an installation that runs it behind Authelia (`HUB_ORCASLICER_GROUP`), the hub's
  owner turns it on or off per person on People (Whole hub). The person joins or leaves that LLDAP group; hub
  owners can always open it.
- Send to hub from OrcaSlicer in the browser (`HUB_ORCASLICER_OUTBOX`): files saved to "Send to hub" in the desktop
  become draft models in the library, credited to the desktop or to the person whose desktop sent them. Files the
  hub does not keep move to "Not sent" beside them with a note saying why.
- OrcaSlicer in the browser, a desktop for each person: when the installation gives everyone their own
  (`HUB_ORCASLICER_DESKTOPS=per-person`), People (Whole hub) lists the desktops' files under "Desktop files", with
  who has one open now and who has left the hub, and the owner removes a desktop's files there after confirming.

- Shared folders for OrcaSlicer in the browser: on People (Whole hub) the owner keeps a list of folders and gives each
  person no access, read only, or read and write (LLDAP groups `share-<slug>-read` and `share-<slug>-write`); owners
  always write. The installation reads the list (`GET /api/platform/shares`, with its own key) when it starts someone's
  desktop and opens what they may use under Files, Shared. Removing a folder marks it; its files go only when the
  owner confirms on the installer's summary.
- Printers are shared by the team, and the owner may narrow them per person on People: all printers (the default,
  also for anyone new) or a chosen list. Owners always have every printer. The hub leaves other printers out of every
  listing (web, phone API, alarms, timeline, plates) and answers 404 for them, and hands the list to ArtisanHub Farm
  (`PUT /api/team/printer-access`), so the farm and OrcaSlicer's print host offer each person only their own.
- The farm is called ArtisanHub Farm in the hub's pages, messages and connection settings.
