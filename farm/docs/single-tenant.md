# Running your own farm: single-tenant mode

OpenPrintHQ has two ways to run.

- **Multi-tenant**, as the hosted service runs: every account gets its own engine and its own Spoolman, which
  the control-plane starts itself. For that it mounts the Docker socket.
- **Single-tenant**, for running your own farm: one engine and one Spoolman, started next to the control-plane
  like any other service, shared by every account. The control-plane talks to them by address and runs no
  `docker` command, so it never needs the Docker socket. Anything that can use the Docker socket is root on
  the host, so a web service should not have it when it does not need it.

Setting `OPHQ_ENGINE_URL` turns single-tenant mode on.

## Quick start

`docker-compose.single.yml` is a complete single-tenant stack: the router, the web app, the control-plane, one
engine, Spoolman, the slicing service, go2rtc for cameras, and Postgres.

```sh
cp .env.single.example .env      # fill in the secrets: openssl rand -hex 32
docker compose -f docker-compose.single.yml up -d
```

The published images serve the farm at `/apps/farm` (`http://<this machine>:8088/apps/farm/`): the web image is
built for that path, and the router serves it and sends the API to the control-plane without it. For the root,
build the web and router images with `BASE_PATH` empty.

Then put it behind your reverse proxy with Authentik forward-auth (below). The first person to sign in becomes
the owner.

## Settings

| Variable | |
|---|---|
| `OPHQ_ENGINE_URL` | The engine every account uses, for example `http://engine:8000`. Turns single-tenant mode on. |
| `OPHQ_SPOOLMAN_URL` | The Spoolman that engine uses for filament, for example `http://spoolman:8000`. The control-plane switches the engine onto it, copying any spools it already had. Leave it out to keep the engine's built-in inventory. One Spoolman can serve other programs too, so filament on hand has one record. |
| `OPHQ_GO2RTC_URL` | go2rtc's API, for example `http://go2rtc:1984`. |
| `OPHQ_SLICER_URL` | The slicing service, for example `http://orca-slicer-api:3000`. |
| `OPHQ_GATEWAY_SECRET` | The proxy adds it as `X-ophq-gateway` on every request; identity headers are believed only alongside it. |
| `OPHQ_OWNER_GROUP`, `OPHQ_USER_GROUP` | Authentik groups for owners and users (default `openprinthq-owners`, `openprinthq-users`). |

The per-tenant settings (`OPHQ_ENGINE_IMAGE`, `OPHQ_SPOOLMAN_IMAGE`, `OPHQ_DOCKER_NETWORK`, `OPHQ_VAULT_IMAGE`,
the bucket mount) do nothing in this mode. The model library (vault) and per-tenant object storage are
multi-tenant features. The deployment starts in local mode (printers on the same network, no cloud client
downloads); the owner can change that in the admin settings.

The engine reaches printers from its own container. Add printers by address; network discovery needs the engine
on the host's network, which this compose file does not do.

## The proxy in front

Same as the hosted service:

1. Forward-auth through an Authentik proxy provider for the OpenPrintHQ name, passing `X-authentik-email`,
   `X-authentik-name` and `X-authentik-groups` to the router, and removing any copies the client sent.
2. Add `X-ophq-gateway: <OPHQ_GATEWAY_SECRET>` to every request.
3. Let API key requests through without forward-auth, as `docs/api-keys-and-webhooks.md` describes.
4. WebSockets on for `/api/connector/`.

## Cameras

Every printer's cameras are go2rtc streams: its own camera, the engine's external camera when one is set, and any
added on the printer's page by stream address (a chamber camera, say). In single-tenant mode the control-plane
registers all of them when it starts and whenever the list changes, and checks each one every five minutes, so
the camera list shows whether each is online without anyone opening it. `GET /api/cameras` lists them and
`GET /api/cameras/frame.jpeg` serves single frames to anything holding a read key; see
`docs/api-keys-and-webhooks.md`.

For browsers to get live video straight from go2rtc, set go2rtc's WebRTC candidate in
`infra/go2rtc/go2rtc.yaml` to this machine's address on your network, and keep port 8555 (TCP and UDP) reachable
from the network the browsers are on.

## Backups

Back up the Postgres databases (`ophq_control`, `ophq_engine`, `spoolman`) and the `engine_data` volume, which
holds uploaded files, thumbnails and archives.
