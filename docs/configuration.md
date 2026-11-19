# Configuration

OpenPrintHQ reads its settings from environment variables. Each setting is named `HUB_` followed by the
name in upper case, so `public_url` is `HUB_PUBLIC_URL`. With docker compose, put them in an env file and
load it with `env_file:` (see `compose.example.yml`).

Defaults are neutral. A fresh hub connects to nothing it was not told about: a setting left empty turns its
feature off, and the matching page says it is not configured. Most connections to other services (print farm,
Spoolman, Manyfold, InvenTree, WooCommerce, AI providers) are made per team in the web UI under
**Connections**. The `HUB_` settings for those services are optional fallbacks and seeds for new teams.

Never commit a filled-in env file. Settings marked *secret* below hold credentials.

## Core

| Variable | Default | Meaning |
|---|---|---|
| `HUB_PUBLIC_URL` | `http://localhost:8080` | The address people and services use to reach the hub. Used in links, OAuth and webhooks. |
| `HUB_DATABASE_URL` | `postgresql+psycopg://hub:hub@db:5432/hub` | SQLAlchemy database URL. PostgreSQL 16 is what the hub is tested with. |
| `HUB_SESSION_SECRET` | `change-me` | *Secret.* Signs sign-in sessions. Set a long random value before first start. |
| `HUB_CONNECTION_KEY` | empty | *Secret.* Encrypts stored connection secrets. Derived from `HUB_SESSION_SECRET` when empty. Changing either later makes stored secrets unreadable. |
| `HUB_DATA_DIR` | `/data` | Photos, rendered sizes, slices and other files the hub stores. Mount a volume here. |
| `HUB_LIBRARY_ROOT` | `/srv/library` | Model library folder, shared with Manyfold when Manyfold is used. |
| `HUB_WORKER_THREADS` | `3` | Threads in the background worker. |
| `HUB_SETUP_TOKEN` | empty | *Secret.* One-time token for first-run setup (`docs/setup-api.md`). Generated and written to the log at first start when empty. |
| `HUB_FIRST_TEAM_NAME` | `My workshop` | Name of the first team on a new installation. |
| `HUB_DEV_AUTH_BYPASS` | `false` | Tests only. Never set it on a real hub. |

## Server folders

Folders on the server that a platform admin may assign to teams. All empty by default, which means none.

| Variable | Default | Meaning |
|---|---|---|
| `HUB_SOURCE_ROOT` | empty | The only folder tree admins may assign. Team folders must sit under it. |
| `HUB_INBOX_DIR` | empty | First team's inbox folder, under `HUB_SOURCE_ROOT`. Each subfolder dropped there is imported as a model. |
| `HUB_DELTAPRINTS_DIR` | empty | First team's library folder in the DeltaPrints layout (`<Category>/<MID - Name>/`), under `HUB_SOURCE_ROOT`. Scanned every 15 minutes. |

## Sign-in (OpenID Connect)

People sign in through an OpenID Connect provider such as Authentik. Without it, only API keys work.

| Variable | Default | Meaning |
|---|---|---|
| `HUB_OIDC_ISSUER` | empty | Issuer URL of the provider. |
| `HUB_OIDC_CLIENT_ID` | empty | Client id of the hub's application at the provider. |
| `HUB_OIDC_CLIENT_SECRET` | empty | *Secret.* Client secret. |
| `HUB_OIDC_CA_PEM` | empty | A certificate authority in PEM to trust for the provider, besides the system's own. For a provider whose certificate comes from a private authority. |
| `HUB_ADMIN_GROUP` | `hub-admins` | Group whose members are platform admins. |
| `HUB_USER_GROUPS` | `hub-users` | Comma list of groups allowed to sign in, besides the admin group. |
| `HUB_AUTHENTIK_URL` | empty | Authentik API address, to send invitations. Empty turns invitations through Authentik off. |
| `HUB_AUTHENTIK_TOKEN` | empty | *Secret.* Authentik service account token. |
| `HUB_AUTHENTIK_GROUP` | `hub-users` | Group invited people join. |
| `HUB_AUTHENTIK_INVITE_FLOW` | `artisan-hub-invite` | Slug of the Authentik enrollment flow used for invitations. |
| `HUB_LLDAP_URL` | empty | LLDAP's web API (for example `http://lldap:17170`), for logins when the provider is Authelia with LLDAP behind it. An invite to someone with no login makes one, with a one-time link to choose its password at `/account/password/...`. With email set up (`HUB_SMTP_HOST`), someone who forgot their password asks for such a link at `/account/password` (point the sign-in page's "Forgot your password?" there); without it, the hub's owner makes one from People. Empty turns it off. Used instead of Authentik when set. |
| `HUB_LDAP_URL` | empty | LLDAP's LDAP address (for example `ldap://lldap:3890`), where passwords are set. |
| `HUB_LDAP_BASE_DN` | `dc=artisanhub,dc=local` | LLDAP's base DN. |
| `HUB_LLDAP_USER` | `hub` | The hub's LLDAP account, in the `lldap_admin` group. |
| `HUB_LLDAP_PASSWORD` | empty | *Secret.* Its password. |
| `HUB_LLDAP_GROUP` | `hub-users` | Group invited people join. |
| `HUB_ORCASLICER_GROUP` | empty | When the installation runs OrcaSlicer in the browser behind Authelia: the LLDAP group whose members may open it, besides the hub's owners (`HUB_ADMIN_GROUP`), who always may. The owner turns it on or off per person on People (Whole hub). Needs `HUB_LLDAP_URL`. Empty means the hub shows no such switch. |
| `HUB_ORCASLICER_OUTBOX` | empty | When the installation runs OrcaSlicer in the browser: the folder in the worker where the desktop's "Send to hub" folder is mounted. Every 30 seconds the worker takes each finished 3MF, STL, STEP, OBJ or G-code file (up to 500 MB) into the library as a new draft model with its licence unknown, credited to "OrcaSlicer in the browser" (`shared/`) or to the person whose desktop sent it (`people/<username>/`). Anything else moves to `Not sent` in the same folder with a note saying why. Empty means off. |
| `HUB_ORCASLICER_DESKTOPS` | empty | `per-person` when the installation gives each person an OrcaSlicer desktop of their own. People (Whole hub) then lists each desktop's files, from the installation at `/apps/orcaslicer/_desktop/homes`, for the owner to remove once its person has left. Empty means everyone shares one desktop and People lists none. With `per-person`, the shared folders the owner keeps on People (Whole hub) are opened in each person's desktop by the installation; with one shared desktop none is. |
| `HUB_OIDC_LOGOUT_URL` | empty | Where signing out sends people, for a provider without an end-session page at the issuer. |

## WooCommerce

| Variable | Default | Meaning |
|---|---|---|
| `HUB_WOO_URL` | empty | Store address. |
| `HUB_WOO_KEY` | empty | *Secret.* REST API consumer key. |
| `HUB_WOO_SECRET` | empty | *Secret.* REST API consumer secret. |
| `HUB_WOO_WEBHOOK_SECRET` | empty | *Secret.* Secret that signs webhooks to `/hooks/woo`. |
| `HUB_WOO_VERIFY_TLS` | `true` | Verify the store's TLS certificate. |

The plugins in `woocommerce/` go on the WordPress side. Set `ARTISAN_HUB_HOST` in `wp-config.php` to the hub's
host name, for example `define('ARTISAN_HUB_HOST', 'hub.example.com');`.

## ResourceSpace (DAM)

| Variable | Default | Meaning |
|---|---|---|
| `HUB_RS_URL` | empty | ResourceSpace API address. |
| `HUB_RS_HOST` | empty | Host header to send, when the API address is not the public name. |
| `HUB_RS_USER` | empty | API user. |
| `HUB_RS_KEY` | empty | *Secret.* API key. |

## Manyfold

| Variable | Default | Meaning |
|---|---|---|
| `HUB_MANYFOLD_URL` | empty | Manyfold address the hub calls. |
| `HUB_MANYFOLD_PUBLIC_URL` | empty | Manyfold address for links people open. Seeds new teams. |
| `HUB_MANYFOLD_CLIENT_ID` | empty | OAuth client id, for API calls. |
| `HUB_MANYFOLD_CLIENT_SECRET` | empty | *Secret.* OAuth client secret. |
| `HUB_MANYFOLD_LIBRARY_PATH` | empty | The shared library folder as Manyfold sees it. Seeds new teams. |

## Print farm

The print farm is the farm. First-run setup records it, and its connection is made in the UI
(Settings, Connected services) with an OpenPrintHQ API key. See `docs/farm.md`.

| Variable | Default | Meaning |
|---|---|---|
| `HUB_FARM_MOVE` | empty | `openprinthq` moves an installation set up with a farm app the hub no longer supports to the farm at the next start (`docs/farm.md`, Moving from another farm). |

## InvenTree

| Variable | Default | Meaning |
|---|---|---|
| `HUB_INVENTREE_URL` | empty | InvenTree API address. |
| `HUB_INVENTREE_HOST` | empty | Host header to send, when the API address is not the public name. |
| `HUB_INVENTREE_PUBLIC_URL` | empty | InvenTree address for links. Seeds new teams. |
| `HUB_INVENTREE_TOKEN` | empty | *Secret.* API token. |

## Spoolman and slicing

| Variable | Default | Meaning |
|---|---|---|
| `HUB_SPOOLMAN_URL` | empty | Spoolman address the hub calls. |
| `HUB_SPOOLMAN_PUBLIC_URL` | empty | Spoolman address for links. Seeds new teams. |
| `HUB_SLICER_URL` | empty | A slicer service that runs OrcaSlicer or PrusaSlicer for the hub, for example `http://slicer:8090`. Empty turns slicing off. |
| `HUB_SLICER_TOKEN` | empty | *Secret.* Token the slicer service expects. |

## Reading spool labels (AI)

Teams connect AI providers under **Connections**: a local Ollama server, OpenAI or a compatible API, Anthropic
(Claude) or Google Gemini. The settings below are the fallback for a team with none of them connected.

| Variable | Default | Meaning |
|---|---|---|
| `HUB_VISION_URL` | empty | An Ollama server with a model that reads images, for example `http://ollama.local:11434`. Empty means none. |
| `HUB_VISION_MODEL` | `gemma4:e4b` | Model to use on that server. |
| `HUB_VISION_TIMEOUT` | `90` | Seconds to wait for an answer. |
| `HUB_UPC_LOOKUP_URL` | UPCitemdb trial API | Barcode lookup service. The free trial allows about 100 lookups a day. |

## Alerts by email

| Variable | Default | Meaning |
|---|---|---|
| `HUB_SMTP_HOST` | empty | Mail server. Empty turns email off. |
| `HUB_SMTP_PORT` | `587` | Mail server port. |
| `HUB_SMTP_USER` | empty | Mail user. |
| `HUB_SMTP_PASSWORD` | empty | *Secret.* Mail password. |
| `HUB_SMTP_FROM` | `OpenPrintHQ <hub@localhost>` | Sender address. |
| `HUB_ALERT_TO` | empty | Where alerts go, comma list. |

Push alerts (ntfy, Telegram) are set per team in the UI.

## Look

| Variable | Default | Meaning |
|---|---|---|
| `HUB_THEME_PROXY_KEY` | empty | *Secret.* Key a reverse proxy sends, with the signed-in person's email, to fetch that person's look for the supporting apps (`/theme/apps.css`, `/theme/apps.js`). Empty turns it off. |
| `HUB_WORDMARK` | `/theme/openprinthq-wordmark.svg` | Path or URL of the wordmark the phone apps draw. |

## Phone app links

The install page (`/get-app`), Connect a phone links (`/pair`), Android App Links and iOS Universal Links.
All off until set.

| Variable | Default | Meaning |
|---|---|---|
| `HUB_APP_ID` | empty | Android package name and iOS bundle id of the phone app. Empty means links open by the app's URL scheme only, and the app link files are empty. |
| `HUB_ANDROID_CERT_SHA256` | empty | SHA-256 fingerprint of the app's signing key, comma list. Needed with `HUB_APP_ID` for `/.well-known/assetlinks.json`. |
| `HUB_ANDROID_APK_DIR` | empty | Folder holding `openprinthq-<version>.apk` files to offer for download (builds from before the rename are named `artisan-hub-<version>.apk`; both are found). |
| `HUB_ANDROID_STORE_URL` | empty | Play listing. When set it replaces the APK download. |
| `HUB_IOS_TEAM_ID` | empty | Apple Developer team id. With `HUB_APP_ID` it turns on Universal Links. |
| `HUB_IOS_STORE_URL` | empty | TestFlight or App Store link for the install page. |
| `HUB_IOS_APP_STORE_ID` | empty | Numeric App Store id, for Safari's Smart App Banner. |
