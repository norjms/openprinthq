# OpenPrintHQ

**One command center for every 3D printer.** OpenPrintHQ brings Bambu Lab, Creality, Prusa, Snapmaker and Voron printers into a single private HQ — queue, slice, monitor and manage your whole farm from one place. Every user gets their own isolated instance.

> Free software under the **GNU AGPL-3.0**. Your printers, your data, your rules.

---

## Status

🚧 **Early development.** The website and application framework are in place and deployable; printer/slicer functionality is being built in feature by feature. See the [issue tracker](https://internal.example.com/OpenPrintHQ/openprinthq/issues) for what's next and what needs decisions.

## Architecture

OpenPrintHQ uses an **instance-per-user** model. A small control-plane provisions an isolated engine instance (its own database + workspace) for each account.

```
                       ┌─────────────────────────── app host (Docker) ───────────────────────────┐
  internal.example.com ─▶ router (nginx) ─┬─▶ web            (SvelteKit — landing + app shell)
                                            └─▶ control-plane  (Fastify — accounts, provisioning)
                                                     │  creates per-user…
  <user>.internal.example.com ─────────────────────┴─▶ engine instance  (forked backend, 1 per user)
                                                              │
                                                    PostgreSQL (control DB + one DB per tenant)
```

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

The stack is designed to sit behind a reverse proxy (npmplus) with Authentik OIDC for authentication.

## Licensing

OpenPrintHQ is licensed under the **GNU Affero General Public License v3.0** (`LICENSE`). This is a deliberate, permanent choice: any modified version offered over a network must make its complete source available to users. See `NOTICE` for attribution to the upstream projects OpenPrintHQ builds on (notably **Bambuddy**, whose engine this forks, and open **SimplyPrint** components). A "Source code" link is exposed in the app footer to satisfy AGPL §13.
