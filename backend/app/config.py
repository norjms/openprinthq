"""Runtime configuration. Every setting comes from an environment variable named HUB_<NAME>, for example
HUB_PUBLIC_URL. See docs/configuration.md for the full list. Defaults are neutral: a setting left empty turns its
feature off.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUB_", extra="ignore")

    # Core
    public_url: str = "http://localhost:8080"
    worker_threads: int = 3
    database_url: str = "postgresql+psycopg://hub:hub@db:5432/hub"
    session_secret: str = "change-me"
    data_dir: str = "/data"
    library_root: str = "/srv/library"          # Manyfold library, shared bind mount
    source_root: str = ""                       # server folder admins may assign to teams; empty = none
    inbox_dir: str = ""                         # first team inbox folder, under source_root; empty = none
    deltaprints_dir: str = ""                   # first team DeltaPrints folder, under source_root; empty = none
    dev_auth_bypass: bool = False                # tests only
    setup_token: str = ""                        # first-run setup token (HUB_SETUP_TOKEN); generated and logged if empty
    # HUB_FARM_MOVE=openprinthq: an installation set up with a farm the hub no longer supports moves to OpenPrintHQ
    # Farm at this start (docs/farm.md, Moving from another farm). Without it such an installation refuses to start.
    farm_move: str = ""

    # Filament identification (mobile control, 2026-10-23). Team settings vision_url / vision_model override these.
    vision_url: str = ""                         # Ollama with a multimodal model; empty = none
    vision_model: str = "gemma4:e4b"
    vision_timeout: float = 90.0
    upc_lookup_url: str = "https://api.upcitemdb.com/prod/trial/lookup"   # free trial API, no key, about 100 a day
    # Phone app notifications (services/push.py): the push relay, unless a team sets its own. Empty = none.
    push_relay_url: str = ""

    # People auth (OpenID Connect, for example Authentik)
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_ca_pem: str = ""                       # PEM of a private authority to trust for the OIDC provider, besides the system's
    admin_group: str = "hub-admins"           # platform admins (manage all teams, server folders)
    user_groups: str = "hub-users"               # comma list of OIDC groups allowed to sign in (plus admin_group)
    connection_key: str = ""                     # key for connected-service secrets; derived from session_secret if empty
    first_team_name: str = "My workshop"         # name of the first team on a new installation
    authentik_url: str = ""                      # Authentik API for invites (a service account token)
    authentik_token: str = ""
    authentik_group: str = "hub-users"
    authentik_invite_flow: str = "artisan-hub-invite"
    # LLDAP, the user directory behind Authelia, for invites when there is no Authentik (an installation made by
    # the OpenPrintHQ installer). The account must be in lldap_admin. Empty = off.
    lldap_url: str = ""                          # its web API, for example http://lldap:17170
    ldap_url: str = ""                           # its LDAP port, for example ldap://lldap:3890
    ldap_base_dn: str = "dc=artisanhub,dc=local"
    lldap_user: str = "hub"
    lldap_password: str = ""
    lldap_group: str = "hub-users"
    # OrcaSlicer in the browser, when the installation runs it behind Authelia: the LLDAP group whose members may
    # open it besides the hub's owners. The owner turns it on or off per person on People. Empty = not run here.
    orcaslicer_group: str = ""
    # Its outbox ("Send to hub" in the desktop), mounted in the worker by the installer: files saved there become
    # models in the library (services/orcaslicer_outbox.py). Empty = not run here.
    orcaslicer_outbox: str = ""
    # "per-person" when the installation gives each person a desktop of their own: People then lists the desktops'
    # files, which the owner removes once their person has gone. Empty = one desktop everyone shares.
    orcaslicer_desktops: str = ""
    oidc_logout_url: str = ""                   # where sign-out sends people; empty = the provider's end-session

    # WooCommerce
    woo_url: str = ""
    woo_key: str = ""
    woo_secret: str = ""
    woo_webhook_secret: str = ""
    woo_verify_tls: bool = True

    # ResourceSpace DAM
    rs_url: str = ""
    rs_host: str = ""
    rs_user: str = ""
    rs_key: str = ""

    # The model library (gyroidhub, under the hub's name at /apps/models/ in an OpenPrintHQ installation): it browses
    # the hub's library with a 3D view. The hub links to it from its menu and from each model.
    models_url: str = ""

    # Manyfold
    manyfold_url: str = ""
    manyfold_public_url: str = ""
    manyfold_client_id: str = ""
    manyfold_client_secret: str = ""
    manyfold_library_path: str = ""   # path as Manyfold sees it

    # InvenTree
    inventree_url: str = ""
    inventree_host: str = ""
    inventree_public_url: str = ""
    inventree_token: str = ""

    # Spoolman
    spoolman_url: str = ""
    slicer_url: str = ""                 # slicer service, for example http://slicer:8090; empty = slicing off
    slicer_token: str = ""
    spoolman_public_url: str = ""

    # Alerts
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "OpenPrintHQ <hub@localhost>"
    alert_to: str = ""
    # Shared look for the supporting apps: a reverse proxy sends this key with the signed-in person's email (empty = off)
    theme_proxy_key: str = ""
    wordmark: str = "/theme/openprinthq-wordmark.svg"   # path or URL of the wordmark the phone apps draw

    # Phone app: Connect a phone links (/pair), the install page (/get-app), Android App Links and iOS Universal Links
    app_id: str = ""                               # Android package and iOS bundle id; empty = no app links
    android_cert_sha256: str = ""                  # SHA-256 of the app's signing key, comma list; empty = no App Links
    android_apk_dir: str = ""                    # folder holding openprinthq-<version>.apk files (artisan-hub-<version>.apk before the rename); empty = none
    android_store_url: str = ""                    # Play listing; when set it replaces the APK download
    ios_team_id: str = ""                          # Apple Developer team id; turns on Universal Links (paid program only)
    ios_store_url: str = ""                        # TestFlight or App Store link; when set the install page offers it
    ios_app_store_id: str = ""                     # numeric App Store id, for Safari's Smart App Banner


@lru_cache
def settings() -> Settings:
    return Settings()
