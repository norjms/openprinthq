# ArtisanHub Farm

The print farm part of [ArtisanHub](https://github.com/norjms/artisan-hub): a fork of
[OpenPrintHQ](https://github.com/norjms/openprinthq), run single-tenant, in ArtisanHub's look, and served under
the hub's own name at `/apps/farm`. The ArtisanHub installer sets it up when you choose "Install it for me" for the
print farm.

- Single-tenant only: one engine and one Spoolman, started by the installer, and no Docker socket
  ([docs/single-tenant.md](docs/single-tenant.md)).
- Sign-in is the hub's Authentik, through forward-auth in the farm's router.
- Images: `ghcr.io/norjms/artisan-hub-farm-{web,control-plane,router}`, built by
  [.github/workflows/build.yml](.github/workflows/build.yml) on every push to `main`; the web image is built
  with `BASE_PATH=/apps/farm`, and the router serves that path (`OPHQ_BASE_PATH`), sending the API to the
  control-plane without it. Running the images by hand, open `http://<host>:8088/apps/farm/`.
- Changes come from OpenPrintHQ: `git fetch openprinthq && git merge openprinthq/main`. OpenPrintHQ stays its
  own product.

Free software under the GNU AGPL-3.0, like OpenPrintHQ. The rest of this file is OpenPrintHQ's.

---

# OpenPrintHQ

**One command center for every 3D printer.** OpenPrintHQ brings Bambu Lab, Creality, Prusa, Snapmaker and Voron printers into a single private HQ — queue, slice, monitor and manage your whole farm from one place. Every user gets their own isolated instance.

> Free software under the **GNU AGPL-3.0**. Your printers, your data, your rules.

---

## Status

🚧 **Early development.** The website and application framework are in place and deployable; printer/slicer functionality is being built in feature by feature. See the [issue tracker](https://internal.example.com/OpenPrintHQ/openprinthq/issues) for what's next and what needs decisions.

## Architecture

OpenPrintHQ uses an **instance-per-user** model. A small control-plane provisions an isolated engine instance (its own database + workspace) for each account.

![OpenPrintHQ architecture — instance-per-user, multi-brand 3D-print-farm platform](docs/architecture.png)

| Component | Stack | Path |
|---|---|---|
| Website + app shell | SvelteKit (adapter-node) | `web/` |
| Control-plane / provisioner | Fastify + PostgreSQL | `control-plane/` |
| Edge router | nginx | `infra/router/` |
| Printer/slicer engine | fork of Bambuddy (separate repo) | `openprinthq-engine` |

Supported printers out of the box: **Bambu Lab, Creality, Prusa, Snapmaker, Voron**. Built-in slicer: **OrcaSlicer**.

## Develop

```bash
# web
cd web && npm install && npm run dev        # http://localhost:5173

# control-plane
cd control-plane && cp ../.env.example ../.env   # fill in
npm install && npm run dev                   # http://localhost:8080
```

## Deploy

```bash
cp .env.example .env    # fill in DB creds + secrets
docker compose up -d --build
```

The stack is designed to sit behind a reverse proxy (for example Nginx Proxy Manager) with Authentik OIDC for authentication.

Running your own farm? `docker-compose.single.yml` runs one engine and one Spoolman shared by every account, with no
Docker socket for the control-plane. See [docs/single-tenant.md](docs/single-tenant.md).

## Licensing

OpenPrintHQ is licensed under the **GNU Affero General Public License v3.0** (`LICENSE`). This is a deliberate, permanent choice: any modified version offered over a network must make its complete source available to users. See `NOTICE` for attribution to the upstream projects OpenPrintHQ builds on (notably **Bambuddy**, whose engine this forks, and open **SimplyPrint** components). A "Source code" link is exposed in the app footer to satisfy AGPL §13.
