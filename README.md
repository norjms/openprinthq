# OpenPrintHQ

OpenPrintHQ is a self-hosted hub for small 3D print businesses and print farms. It manages every 3D model as a
product: photos, print settings, files, colour options, bill of materials, store listing and supply levels. Other
systems are driven from it: the print farm, the filament inventory, the parts inventory, the model library and
the web store.

Licence: [GNU AGPL-3.0](LICENSE).

## What is in this repository

| Folder | What it is |
|---|---|
| `backend/`, `frontend/`, `woocommerce/` | The hub: FastAPI backend, SvelteKit frontend and the WooCommerce connector |
| `farm/` | The print farm, served under the hub's name at `/apps/farm` (web app, control plane, router, slicing service) |
| `laser/` | OpenPrintHQ Laser, the Laser Crafting tools at `/apps/laser` |
| `ui/` | The shared look of every OpenPrintHQ app (MIT, `ui/LICENSE`); released as `ui-vX.Y.Z` tags |

The farm, Laser and the shared look had repositories of their own until November 2026 (`norjms/openprinthq-farm`,
`norjms/openprinthq-laser`, `norjms/openprinthq-ui`, now archived); their histories are kept here. The print engine
stays in its own repository, `norjms/openprinthq-engine`, since it follows Bambuddy.

## Features

- **Models as products.** Photos, model and print files, colour options, bill of materials, licence records and
  a store listing per model.
- **Add models many ways.** Drag files into the web UI, drop a folder into a team inbox folder, call the ingest
  API, or paste a link from MakerWorld, Printables, Thingiverse, Creality Cloud, Thangs, MyMiniFactory, Cults3D
  or any model file link (see `docs/add-by-url.md`). Every fetched link is checked against private addresses,
  DNS rebinding and size limits.
- **Licences.** Each model carries a licence code and evidence, and the hub refuses to put a model on sale when
  its licence is not confirmed.
- **Stores (WooCommerce, Shopify, Etsy).** New products are created as drafts and only go live from the hub.
  Stock is one pool for every store: the units buildable from parts and filament on hand, minus open orders from
  any store. Orders arrive by webhook and print on the farm the same way whichever store they came from. An instant
  quote form on WooCommerce lets customers upload their own model to be sliced and priced. See `docs/stores.md`.
- **Print farm.** The farm runs the printers: print queue, plate checks,
  timeline, maintenance, alarms and a wall display (`/tv`).
- **Slicing.** With a slicer service connected, the hub slices models for the farm's printers and re-slices
  plates.
- **Filament and parts.** Spoolman holds filament spools and InvenTree holds parts. The hub reads spool labels
  and barcodes, prints spool and location labels, and builds a shopping list.
- **AI label reading and translation (optional).** A team may connect a local Ollama server, OpenAI or a
  compatible API, Anthropic (Claude) or Google Gemini to read spool labels and translate imported text.
- **Teams.** Several teams on one hub, each with its own connections, people and permissions.
- **APIs.** A REST API with keys (`/api/docs`), a mobile API for phone apps (`docs/mobile-api.md`), a
  first-run setup API (`docs/setup-api.md`) and an MCP endpoint (`/mcp`) with OAuth, so MCP clients such as
  Claude can use the hub's slicing tools.

## Parts

| Part | What it does |
|---|---|
| `web` | FastAPI backend and the SvelteKit web UI, in one image, on port 8080. |
| `worker` | The same image running `python -m app.worker`: syncs, imports, scans and scheduled jobs. |
| `db` | PostgreSQL 16. |

Optional services the hub connects to:

| Service | Role |
|---|---|
| Farm | Print farm: printers, print queue, print-ready files, filament library. |
| Spoolman | Filament spools, the source of truth for filament on hand. |
| Manyfold | Model library with 3D preview, sharing a folder with the hub. |
| InvenTree | Parts such as screws, magnets, inserts and packaging; suppliers and purchase orders. |
| WooCommerce | Web store. Plugins for WordPress are in `woocommerce/`. |
| Shopify | Web store (an app from your Shopify Dev Dashboard). |
| Etsy | Marketplace shop (an app from etsy.com/developers). |
| ResourceSpace | Digital asset management for product photos. |
| An OpenID Connect provider, such as Authentik | Sign-in for people. |
| A slicer service | Runs OrcaSlicer or PrusaSlicer for the hub. |

## Quick start

You need Docker with the compose plugin.

```sh
git clone https://github.com/norjms/openprinthq.git
cd openprinthq
cp compose.example.yml compose.yml
# Edit compose.yml: set a database password and HUB_SESSION_SECRET (a long random value).
docker compose up -d --build
docker compose logs web worker | grep -i "setup token"
```

Open `http://localhost:8080/setup`, enter the setup token from the log, choose your print farm and name your
first team. The hub answers `GET /healthz` with 200 once the database is ready and migrations have run.

Connections to the other services are made per team in the web UI under **Connections**. To let people sign in
with their own accounts, configure an OpenID Connect provider (see below).

## Configuration

Every setting is an environment variable named `HUB_<NAME>`. The full list, with defaults and what each one
does, is in [docs/configuration.md](docs/configuration.md). Defaults are neutral: a hub connects to nothing until
you configure it.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for running the hub locally and running the tests.

## Security

Please report security problems privately. See [SECURITY.md](SECURITY.md).

## Licence

OpenPrintHQ is free software: you can redistribute it and modify it under the terms of the GNU Affero General
Public License, version 3, as published by the Free Software Foundation. See [LICENSE](LICENSE). If you run a
modified version for others over a network, the AGPL asks you to offer them its source.

Contributions are accepted under the contributor licence agreement in [CLA.md](CLA.md).
